"""Per-modality fairness (INGEST_MODALITY_LIMITS) — permit-at-dispatch, no HOL blocking.

Review finding #3 closed: the old design dequeued from one shared FIFO and THEN acquired
the modality permit, so a worker holding a capped-modality job blocked the whole pool —
worse than no limit. Now a worker only removes a job whose permit it can take atomically
at dispatch, scanning PAST capped jobs — a capped burst queues without occupying a
worker while other modalities flow around it. One shared bound still governs the 503
backpressure story, and a chunk waiting out a retry backoff releases its permit.

Hermetic + headless: gated stub processors (no real pipeline), MockTransport storage.
"""
from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.ingest_queue import IngestQueue
from app.processing.base import ProcessedContent, ProcessedUnit, Processor
from tests.conftest import make_c1


def _wait(pred, timeout: float = 5.0, interval: float = 0.01) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return False


class _StubProcessor(Processor):
    """Counts starts; optionally gates completion; optionally fails-once (transient)."""

    content_kind = "transcript"

    def __init__(self, modality: str, kind: str, *, gated: bool = False,
                 fail_first: set[str] | None = None) -> None:
        self.modality = modality
        self._kind = kind
        self.gate = threading.Event()
        self._gated = gated
        self._fail_first = set(fail_first or ())
        self.started: list[str] = []
        self._lock = threading.Lock()

    def pipeline_version(self, settings) -> str:
        return f"{self.modality}-stub-v0"

    def process(self, c1, blob, settings, span_seconds):
        with self._lock:
            self.started.append(c1["chunk_id"])
        if c1["chunk_id"] in self._fail_first:
            self._fail_first.discard(c1["chunk_id"])
            raise RuntimeError("transient stub hiccup")
        if self._gated:
            assert self.gate.wait(timeout=10)
        return [ProcessedUnit(content=ProcessedContent(
            kind=self._kind, text=f"stub {c1['chunk_id']}"))]


def _app(monkeypatch, fake_storage, tmp_path, stubs: dict, **env):
    monkeypatch.setenv("ASR_BACKEND", "mock")
    monkeypatch.setenv("DP_VAR_DIR", str(tmp_path / "var"))
    monkeypatch.setenv("STORAGE_URL", "http://storage.test")
    monkeypatch.setenv("INGEST_ASYNC", "1")
    for k, v in env.items():
        monkeypatch.setenv(k, str(v))
    import app.main as main_mod
    monkeypatch.setattr(main_mod, "get_processor", lambda modality: stubs[modality])
    app = main_mod.create_app()
    app.state.storage._transport = fake_storage.transport()
    return app


def _posted(fake_storage) -> list[str]:
    return [r["source"]["chunk_id"] for r in fake_storage.record_posts]


def test_capped_modality_backlog_does_not_block_other_modalities(
        monkeypatch, fake_storage, tmp_path):
    """THE finding-#3 scenario. audio=1, two workers: audio A occupies the single audio
    permit (gate-blocked in a worker); audio B queues behind the cap; video V arrives
    BEHIND B in the shared FIFO. The free worker must scan past B and process V — under
    the old dequeue-then-acquire design it would sit blocked holding B (HOL), starving
    video although a worker was idle."""
    stubs = {
        "audio": _StubProcessor("audio", "transcript", gated=True),
        "video": _StubProcessor("video", "caption"),
    }
    app = _app(monkeypatch, fake_storage, tmp_path, stubs,
               INGEST_WORKERS=2, INGEST_MODALITY_LIMITS="audio=1")
    with TestClient(app) as c:
        a = make_c1(fake_storage, chunk_id="f-audio-a", stream_id="s-a", sequence=0)
        assert c.post("/ingest", json=a).status_code == 202
        assert _wait(lambda: stubs["audio"].started == ["f-audio-a"])  # holds the permit

        b = make_c1(fake_storage, chunk_id="f-audio-b", stream_id="s-a", sequence=1)
        assert c.post("/ingest", json=b).status_code == 202  # queued: no audio permit

        v = make_c1(fake_storage, chunk_id="f-video-v", stream_id="s-v", sequence=0,
                    modality="video", codec="video/mp4",
                    blob_ref="raw/pilot-user/f-video-v.mp4")
        assert c.post("/ingest", json=v).status_code == 202

        # Video completes while audio A still holds the only audio permit…
        assert _wait(lambda: "f-video-v" in _posted(fake_storage)), \
            "video starved behind a capped audio job — HOL block regressed"
        # …and B was never dispatched into a worker (the permit stayed with A).
        assert stubs["audio"].started == ["f-audio-a"]
        assert app.state.ingest_queue.queued() == 1  # B queued, no worker holds it

        stubs["audio"].gate.set()  # release A -> permit frees -> B dispatches
        assert _wait(lambda: sorted(_posted(fake_storage)) ==
                     ["f-audio-a", "f-audio-b", "f-video-v"])


def test_shared_bound_503_still_governs_with_limits(monkeypatch, fake_storage, tmp_path):
    """Capacity counts every queued job regardless of modality/eligibility — the single
    bounded-queue backpressure story survives the fairness dispatch."""
    stubs = {"audio": _StubProcessor("audio", "transcript", gated=True)}
    app = _app(monkeypatch, fake_storage, tmp_path, stubs,
               INGEST_WORKERS=1, INGEST_QUEUE_MAX=2, INGEST_MODALITY_LIMITS="audio=1")
    with TestClient(app) as c:
        for i, cid in enumerate(["bp-a", "bp-b", "bp-c"]):
            r = c.post("/ingest", json=make_c1(
                fake_storage, chunk_id=cid, stream_id="s-bp", sequence=i))
            assert r.status_code == 202
        assert _wait(lambda: stubs["audio"].started == ["bp-a"])
        assert _wait(lambda: app.state.ingest_queue.queued() == 2)
        r = c.post("/ingest", json=make_c1(
            fake_storage, chunk_id="bp-d", stream_id="s-bp", sequence=3))
        assert r.status_code == 503  # full is full — modality caps don't add capacity
        assert r.json() == {"ok": False, "error": "ingest queue full"}
        stubs["audio"].gate.set()
    assert sorted(_posted(fake_storage)) == ["bp-a", "bp-b", "bp-c"]


def test_backoff_sleep_releases_the_permit(monkeypatch, fake_storage, tmp_path):
    """A chunk waiting out its retry backoff must not occupy a modality slot: while
    rt-fail sleeps (transient failure, backoff 1.5s), rt-b takes the freed permit and
    completes. Preserves the old semaphore semantics under permit-at-dispatch."""
    stub = _StubProcessor("audio", "transcript", fail_first={"rt-fail"})
    app = _app(monkeypatch, fake_storage, tmp_path, {"audio": stub},
               INGEST_WORKERS=2, INGEST_MODALITY_LIMITS="audio=1",
               INGEST_MAX_RETRIES=2, INGEST_RETRY_BACKOFF=1.5)
    with TestClient(app) as c:
        assert c.post("/ingest", json=make_c1(
            fake_storage, chunk_id="rt-fail", stream_id="s-rt", sequence=0)).status_code == 202
        # First attempt failed -> it is now in its 1.5s backoff, permit released.
        assert _wait(lambda: stub.started == ["rt-fail"])
        assert c.post("/ingest", json=make_c1(
            fake_storage, chunk_id="rt-b", stream_id="s-rt", sequence=1)).status_code == 202
        # rt-b completes well inside rt-fail's backoff window.
        assert _wait(lambda: "rt-b" in _posted(fake_storage), timeout=1.2), \
            "backoff-sleeping chunk still held the modality permit"
        assert "rt-fail" not in _posted(fake_storage)
        # rt-fail's retry then re-acquires and succeeds.
        assert _wait(lambda: "rt-fail" in _posted(fake_storage))


def test_no_limits_is_pure_fifo_dispatch(monkeypatch, fake_storage, tmp_path):
    """Empty knob (the default): every job is eligible, dispatch order == arrival order
    — byte-identical to the unlimited pool."""
    stub = _StubProcessor("audio", "transcript", gated=True)
    app = _app(monkeypatch, fake_storage, tmp_path, {"audio": stub},
               INGEST_WORKERS=1)
    with TestClient(app) as c:
        for i in range(4):
            assert c.post("/ingest", json=make_c1(
                fake_storage, chunk_id=f"fifo-{i}", stream_id="s-ff",
                sequence=i)).status_code == 202
        assert _wait(lambda: stub.started == ["fifo-0"])
        stub.gate.set()  # gate is a shared Event: all subsequent process() calls pass
    assert stub.started == [f"fifo-{i}" for i in range(4)]  # strict arrival order


def test_backoff_retrier_is_not_starved_by_a_sustained_backlog(
        monkeypatch, fake_storage, tmp_path):
    """Review-confirmed starvation, now closed: under audio=1 with a sustained audio
    backlog, a finishing worker's same-tick rescan used to steal every freed permit
    for a NEWER queued job before the parked retrier's wakeup ran — the oldest chunk's
    retry was deferred until the backlog emptied (unbounded under continuous ingest).
    Parked re-acquirers now hold a permit RESERVATION the dispatch scan must respect."""

    class _SlowStub(_StubProcessor):
        def process(self, c1, blob, settings, span_seconds):
            if c1["chunk_id"].startswith("bl-"):
                time.sleep(0.05)          # sustained backlog: each job takes a while
            return super().process(c1, blob, settings, span_seconds)

    stub = _SlowStub("audio", "transcript", fail_first={"st-fail"})
    app = _app(monkeypatch, fake_storage, tmp_path, {"audio": stub},
               INGEST_WORKERS=2, INGEST_MODALITY_LIMITS="audio=1",
               INGEST_MAX_RETRIES=2, INGEST_RETRY_BACKOFF=0.1)
    with TestClient(app) as c:
        assert c.post("/ingest", json=make_c1(
            fake_storage, chunk_id="st-fail", stream_id="s-st",
            sequence=0)).status_code == 202
        assert _wait(lambda: stub.started == ["st-fail"])  # first attempt failed
        for i in range(10):                                # arrive DURING its backoff
            assert c.post("/ingest", json=make_c1(
                fake_storage, chunk_id=f"bl-{i}", stream_id="s-st",
                sequence=i + 1)).status_code == 202
        assert _wait(lambda: len(_posted(fake_storage)) == 11)
    order = _posted(fake_storage)
    # Backoff ~0.1s ≈ two 0.05s backlog jobs; the reservation hands the NEXT freed
    # permit to the retrier. Unstarved, st-fail lands early — never behind the whole
    # backlog (the pre-fix behavior put it dead last, index 10).
    assert order.index("st-fail") < 8, \
        f"retrier starved behind the backlog: {order}"


def test_worker_backstop_after_run_job_blowup_releases_permit(
        monkeypatch, fake_storage, tmp_path):
    """The _worker generic-exception backstop (post-process_chunk failure, e.g.
    continuity.note_processed raising) must release the modality permit — a leak
    there permanently wedges the capped modality with zero signal (review gap)."""
    stub = _StubProcessor("audio", "transcript")
    app = _app(monkeypatch, fake_storage, tmp_path, {"audio": stub},
               INGEST_WORKERS=2, INGEST_MODALITY_LIMITS="audio=1")
    real_note = app.state.continuity.note_processed
    blown = {"done": False}

    def exploding_note(stream_id, sequence):
        if not blown["done"]:
            blown["done"] = True
            raise RuntimeError("metrics/continuity blip AFTER the C2 was written")
        return real_note(stream_id, sequence)

    monkeypatch.setattr(app.state.continuity, "note_processed", exploding_note)
    with TestClient(app) as c:
        assert c.post("/ingest", json=make_c1(
            fake_storage, chunk_id="bs-a", stream_id="s-bs", sequence=0)).status_code == 202
        assert _wait(lambda: "bs-a" in _posted(fake_storage))  # C2 landed pre-blowup
        # The permit must be free again: a second capped-modality chunk processes.
        assert c.post("/ingest", json=make_c1(
            fake_storage, chunk_id="bs-b", stream_id="s-bs", sequence=1)).status_code == 202
        assert _wait(lambda: "bs-b" in _posted(fake_storage)), \
            "permit leaked in the worker backstop — capped modality wedged"


def test_drain_cancels_cleanly_during_backoff_and_permit_wait(
        monkeypatch, fake_storage, tmp_path):
    """Cancel during the backoff sleep (permit released, held=False) and during the
    _acquire_permit park must not double-release or wedge the drain; the chunks stay
    'accepted' in the journal — re-drivable (review gap: this path was unpinned)."""
    stub = _StubProcessor("audio", "transcript", gated=True,
                          fail_first={"dr-sleeper"})
    app = _app(monkeypatch, fake_storage, tmp_path, {"audio": stub},
               INGEST_WORKERS=2, INGEST_MODALITY_LIMITS="audio=1",
               INGEST_MAX_RETRIES=3, INGEST_RETRY_BACKOFF=30,  # parks in backoff
               INGEST_DRAIN_TIMEOUT=0.3)
    with TestClient(app) as c:
        # dr-sleeper: fails once -> releases permit -> 30s backoff (cancel hits here).
        assert c.post("/ingest", json=make_c1(
            fake_storage, chunk_id="dr-sleeper", stream_id="s-dr",
            sequence=0)).status_code == 202
        assert _wait(lambda: "dr-sleeper" in stub.started)
        # dr-gated: takes the freed permit, blocks on the gate until drain cancels it.
        assert c.post("/ingest", json=make_c1(
            fake_storage, chunk_id="dr-gated", stream_id="s-dr",
            sequence=1)).status_code == 202
        assert _wait(lambda: "dr-gated" in stub.started)
    # `with` exit: drain times out at 0.3s, cancels both workers (one in the backoff
    # sleep, one in process) — exiting at all proves no wedge/double-release blowup.
    stub.gate.set()  # unblock the abandoned threadpool thread promptly
    accepted = sorted(c1["chunk_id"] for c1 in app.state.journal.pending_accepted())
    assert accepted == ["dr-gated", "dr-sleeper"]     # both re-drivable
    assert fake_storage.record_posts == []


def test_limit_parsing_ignores_garbage_entries():
    async def go():
        q = IngestQueue(SimpleNamespace(state=SimpleNamespace()),  # duck-typed app
                        workers=1, maxsize=4, max_retries=0, backoff=0.0,
                        modality_limits=" video = 2 ,bogus, audio=x, =3, text=0, image=1 ")
        assert q._limits == {"video": 2, "image": 1}
        assert q._permits == {"video": 2, "image": 1}
    asyncio.run(go())
