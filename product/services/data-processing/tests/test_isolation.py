"""Subprocess isolation (INGEST_ISOLATION=subprocess) — poison-chunk + ghost-kill.

The two failure classes the in-process pool cannot contain, proven contained:

  * a chunk whose processing HARD-CRASHES (segfault / native OOM / os._exit) kills its
    CHILD, not the service — it dead-letters after bounded retries while other chunks
    keep flowing (in-process, it would crash-loop the whole service through the
    durable re-drive cap);
  * a drain-timeout cancel SIGKILLs the child — the computation is truly reclaimed
    (in-process, the threadpool thread would run to completion as an unkillable ghost).

Tests use start method ``fork`` so a monkeypatched mock-ASR (the poison / the sleeper)
is inherited by the child; one end-to-end test uses ``spawn`` to prove the child
entrypoint is import-clean in a fresh interpreter. All headless (mock backends).
"""
from __future__ import annotations

import asyncio
import os
import time

import pytest
from fastapi.testclient import TestClient

import app.asr.mock as mock_asr
from app.ingest_core import ProcessingError
from app.isolation import run_processor_in_subprocess
from app.processing import registry
from app.processing.base import ProcessedContent, ProcessedUnit, Processor
from tests.conftest import make_c1


# Tests deliberately use start method `fork` (the child must inherit monkeypatched
# state); Python 3.12 warns that forking a multi-threaded process (the TestClient
# portal) MAY deadlock. Production defaults to `spawn` for exactly this reason — the
# warning is the known, accepted cost of the test seam, not a product code path.
pytestmark = pytest.mark.filterwarnings(
    "ignore:.*use of fork\\(\\) may lead to deadlocks.*:DeprecationWarning"
)


def _wait(pred, timeout: float = 10.0, interval: float = 0.02) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return False


def _env(monkeypatch, tmp_path, **extra):
    monkeypatch.setenv("ASR_BACKEND", "mock")
    monkeypatch.setenv("DP_VAR_DIR", str(tmp_path / "var"))
    monkeypatch.setenv("STORAGE_URL", "http://storage.test")
    monkeypatch.setenv("INGEST_ISOLATION", "subprocess")
    for k, v in extra.items():
        monkeypatch.setenv(k, str(v))


def test_inline_spawn_end_to_end_byte_identical(monkeypatch, fake_storage, tmp_path):
    """Inline mode + spawn: the child is a FRESH interpreter (proves the entrypoint is
    import-clean, nothing hidden inherited) running the real mock pipeline; the C2 that
    comes back is the same record the in-process path produces."""
    _env(monkeypatch, tmp_path, INGEST_SUBPROC_START="spawn")
    from app.main import create_app

    app = create_app()
    app.state.storage._transport = fake_storage.transport()
    with TestClient(app) as c:
        c1 = make_c1(fake_storage, chunk_id="iso-spawn-1")
        r = c.post("/ingest", json=c1)
        assert r.status_code == 200
        assert len(r.json()["record_ids"]) == 1
    c2 = fake_storage.record_posts[0]
    from app import schemas
    assert schemas.validate_c2(c2) == []
    assert c2["pipeline_version"] == "asr-mock-v0"          # parent-stamped dialect
    assert "Mock transcript for chunk iso-spawn-1" in c2["content"]["text"]
    assert len(c2["content"]["segments"]) == 2               # the real mock ran in-child


def test_poison_chunk_kills_child_not_service(monkeypatch, fake_storage, tmp_path):
    """THE blast-radius test: a chunk whose processing os._exit(139)s (standing in for
    a segfault) dead-letters visibly while the service — and the chunks after it —
    keep working. In-process this would have killed the whole pool."""
    _env(monkeypatch, tmp_path, INGEST_SUBPROC_START="fork",
         INGEST_ASYNC=1, INGEST_MAX_RETRIES=1, INGEST_RETRY_BACKOFF=0)

    real = mock_asr.transcribe

    def poisoned(settings, audio_bytes, codec, chunk_seconds, chunk_id):
        if b"POISON" in audio_bytes:
            os._exit(139)  # hard child death — no exception, no reply
        return real(settings, audio_bytes, codec, chunk_seconds, chunk_id)

    monkeypatch.setattr(mock_asr, "transcribe", poisoned)  # fork child inherits this

    from app.main import create_app
    app = create_app()
    app.state.storage._transport = fake_storage.transport()
    with TestClient(app) as c:
        bad = make_c1(fake_storage, chunk_id="iso-poison", stream_id="s-iso", sequence=0,
                      audio=b"POISON" + b"x" * 32, blob_ref="raw/pilot-user/poison.wav")
        assert c.post("/ingest", json=bad).status_code == 202
        assert _wait(lambda: c.get("/continuity/s-iso").json()["dead_lettered"] == [[0, 0]]), \
            "poison chunk never dead-lettered"
        # Service alive and processing AFTER the crash-chunk:
        assert c.get("/health").json()["ok"] is True
        good = make_c1(fake_storage, chunk_id="iso-good", stream_id="s-iso", sequence=1)
        assert c.post("/ingest", json=good).status_code == 202
        assert _wait(lambda: c.get("/continuity/s-iso").json()["processed"] == [[1, 1]]), \
            "healthy chunk failed after the poison one"
        metrics = c.get("/metrics").text
        assert 'dp_dead_letter_total{modality="audio"} 1' in metrics
        assert 'dp_ingest_retries_total{modality="audio"} 1' in metrics  # bounded retry ran
    assert [r["source"]["chunk_id"] for r in fake_storage.record_posts] == ["iso-good"]


def test_child_generic_error_is_transient_and_retries(monkeypatch, fake_storage, tmp_path):
    """The retry-vs-dead-letter taxonomy survives the process boundary: a generic child
    exception behaves exactly like an in-process infra hiccup — retried, then success."""
    _env(monkeypatch, tmp_path, INGEST_SUBPROC_START="fork",
         INGEST_ASYNC=1, INGEST_MAX_RETRIES=2, INGEST_RETRY_BACKOFF=0)
    flag = tmp_path / "failed-once"  # state must live OUTSIDE the child (forks don't share)
    real = mock_asr.transcribe

    def flaky(settings, audio_bytes, codec, chunk_seconds, chunk_id):
        if not flag.exists():
            flag.touch()
            raise RuntimeError("cold model (503)")
        return real(settings, audio_bytes, codec, chunk_seconds, chunk_id)

    monkeypatch.setattr(mock_asr, "transcribe", flaky)

    from app.main import create_app
    app = create_app()
    app.state.storage._transport = fake_storage.transport()
    with TestClient(app) as c:
        assert c.post("/ingest", json=make_c1(
            fake_storage, chunk_id="iso-flaky", stream_id="s-fl", sequence=0)).status_code == 202
        # Wait on continuity 'processed' — the true done-signal (record_posts fires a
        # hair earlier, in the post-write / pre-note window; asserting on it races).
        assert _wait(lambda: c.get("/continuity/s-fl").json()["processed"] == [[0, 0]]), \
            "retry never succeeded"
        assert len(fake_storage.record_posts) == 1
        assert c.get("/continuity/s-fl").json()["dead_lettered"] == []
        metrics = c.get("/metrics").text
    assert 'dp_ingest_retries_total{modality="audio"} 1' in metrics
    assert "dp_dead_letter_total" not in metrics


def test_processing_error_crosses_the_boundary_intact(monkeypatch, tmp_path):
    """A child ProcessingError re-raises in the parent with detail/status/transient
    preserved — the retry-vs-dead-letter split stays load-bearing under isolation."""
    monkeypatch.setenv("INGEST_ISOLATION", "subprocess")
    monkeypatch.setenv("INGEST_SUBPROC_START", "fork")

    class Term(Processor):
        modality = "audio"
        content_kind = "transcript"
        def pipeline_version(self, settings): return "t-v0"
        def process(self, c1, blob, settings, span_seconds):
            raise ProcessingError({"error": "corrupt frames"},
                                  http_status=502, transient=False)

    registry.get_processor("audio")  # ensure discovery ran (child must not re-discover)
    monkeypatch.setitem(registry._REGISTRY, "audio", Term())  # fork child inherits this

    from app.config import get_settings
    c1 = {"chunk_id": "iso-term", "modality": "audio"}

    async def go():
        with pytest.raises(ProcessingError) as ei:
            await run_processor_in_subprocess(
                modality="audio", c1=c1, blob=b"x",
                settings=get_settings(), span_seconds=1.0)
        return ei.value

    exc = asyncio.run(go())
    assert exc.detail == {"error": "corrupt frames"}
    assert exc.http_status == 502 and exc.transient is False


def test_unpicklable_processing_error_detail_keeps_taxonomy(monkeypatch, tmp_path):
    """Review-confirmed hole, now closed: an unpicklable detail dict used to blow up
    the child's send, exiting cleanly with code 0 — the parent then misread a
    TERMINAL failure as a transient 'died' (taxonomy flip). The child now falls back
    to a sanitized string detail that PRESERVES the transient/status flags."""
    monkeypatch.setenv("INGEST_ISOLATION", "subprocess")
    monkeypatch.setenv("INGEST_SUBPROC_START", "fork")

    class ExoticTerm(Processor):
        modality = "audio"
        content_kind = "transcript"
        def pipeline_version(self, settings): return "t-v0"
        def process(self, c1, blob, settings, span_seconds):
            raise ProcessingError({"error": "corrupt", "handle": lambda: None},  # unpicklable
                                  http_status=502, transient=False)

    registry.get_processor("audio")  # ensure discovery ran in-parent
    monkeypatch.setitem(registry._REGISTRY, "audio", ExoticTerm())

    from app.config import get_settings

    async def go():
        with pytest.raises(ProcessingError) as ei:
            await run_processor_in_subprocess(
                modality="audio", c1={"chunk_id": "iso-exotic", "modality": "audio"},
                blob=b"x", settings=get_settings(), span_seconds=1.0)
        return ei.value

    exc = asyncio.run(go())
    assert exc.http_status == 502 and exc.transient is False   # flags survived
    assert "not picklable" in str(exc.detail.get("note", ""))  # sanitized, visible


def test_large_child_reply_pins_recv_before_join(monkeypatch):
    """A reply far beyond the ~64KB pipe buffer: the child blocks in send until the
    parent drains, so any refactor to join-before-recv deadlocks HERE instead of in
    production on the first real ASR/VLM chunk (review gap: only tiny replies were
    exercised)."""
    monkeypatch.setenv("INGEST_ISOLATION", "subprocess")
    monkeypatch.setenv("INGEST_SUBPROC_START", "fork")
    big = "x" * (1 << 20)  # 1 MiB of transcript

    class BigProc(Processor):
        modality = "audio"
        content_kind = "transcript"
        def pipeline_version(self, settings): return "t-v0"
        def process(self, c1, blob, settings, span_seconds):
            return [ProcessedUnit(content=ProcessedContent(kind="transcript", text=big))]

    registry.get_processor("audio")
    monkeypatch.setitem(registry._REGISTRY, "audio", BigProc())

    from app.config import get_settings

    async def go():
        return await run_processor_in_subprocess(
            modality="audio", c1={"chunk_id": "iso-big", "modality": "audio"},
            blob=b"x", settings=get_settings(), span_seconds=1.0)

    units = asyncio.run(go())
    assert len(units) == 1 and units[0].content.text == big


def test_each_retry_attempt_gets_a_fresh_child(monkeypatch, fake_storage, tmp_path):
    """Pins the fresh-child-per-attempt contract BEFORE a warm child pool lands: a
    retry must never reuse a child whose in-process state attempt 1 corrupted
    (review gap: the flaky test passed identically with a reused child)."""
    _env(monkeypatch, tmp_path, INGEST_SUBPROC_START="fork",
         INGEST_ASYNC=1, INGEST_MAX_RETRIES=2, INGEST_RETRY_BACKOFF=0)
    pids = tmp_path / "pids"
    real = mock_asr.transcribe

    def pid_logging(settings, audio_bytes, codec, chunk_seconds, chunk_id):
        with open(pids, "a") as fh:
            fh.write(f"{os.getpid()}\n")
        if len(pids.read_text().splitlines()) == 1:
            raise RuntimeError("first attempt fails")
        return real(settings, audio_bytes, codec, chunk_seconds, chunk_id)

    monkeypatch.setattr(mock_asr, "transcribe", pid_logging)

    from app.main import create_app
    app = create_app()
    app.state.storage._transport = fake_storage.transport()
    with TestClient(app) as c:
        assert c.post("/ingest", json=make_c1(
            fake_storage, chunk_id="iso-fresh", stream_id="s-fr",
            sequence=0)).status_code == 202
        assert _wait(lambda: len(fake_storage.record_posts) == 1)
    attempt_pids = pids.read_text().split()
    assert len(attempt_pids) == 2
    assert attempt_pids[0] != attempt_pids[1], "retry reused the failed attempt's child"


def test_async_mode_under_spawn_end_to_end(monkeypatch, fake_storage, tmp_path):
    """The production combination (async workers + spawn children) was untested —
    a job-dict field that stops being picklable, or a spawn-import regression, must
    fail HERE, not in the first real deployment (review gap)."""
    _env(monkeypatch, tmp_path, INGEST_SUBPROC_START="spawn", INGEST_ASYNC=1)
    from app.main import create_app
    app = create_app()
    app.state.storage._transport = fake_storage.transport()
    with TestClient(app) as c:
        for i in range(2):
            assert c.post("/ingest", json=make_c1(
                fake_storage, chunk_id=f"iso-sp-{i}", stream_id="s-sp",
                sequence=i)).status_code == 202
        # Wait on continuity 'processed' — the TRUE done-signal (record_posts fires a
        # hair earlier, in the post-write / pre-note window). Generous timeout: each
        # spawn child boots a fresh interpreter.
        assert _wait(lambda: c.get("/continuity/s-sp").json()["processed"] == [[0, 1]],
                     timeout=30.0), "spawn children never completed"
        assert c.get("/continuity/s-sp").json()["dead_lettered"] == []


def test_drain_cancel_kills_the_child_and_chunk_stays_redrivable(
        monkeypatch, fake_storage, tmp_path):
    """THE ghost-computation test: a drain-timeout cancel SIGKILLs the mid-flight child
    (in-process, the thread would burn 30s of CPU computing a result nobody reads),
    and the chunk's journal row stays 'accepted' — re-drivable at next startup."""
    _env(monkeypatch, tmp_path, INGEST_SUBPROC_START="fork",
         INGEST_ASYNC=1, INGEST_DRAIN_TIMEOUT=0.3)
    pid_file, done_file = tmp_path / "child.pid", tmp_path / "child.done"

    def sleeper(settings, audio_bytes, codec, chunk_seconds, chunk_id):
        pid_file.write_text(str(os.getpid()))
        time.sleep(30)                       # far beyond the drain timeout
        done_file.touch()                    # must NEVER appear (SIGKILL, not cancel)
        raise AssertionError("unreachable")

    monkeypatch.setattr(mock_asr, "transcribe", sleeper)

    from app.main import create_app
    app = create_app()
    app.state.storage._transport = fake_storage.transport()
    with TestClient(app) as c:
        assert c.post("/ingest", json=make_c1(
            fake_storage, chunk_id="iso-ghost", stream_id="s-gh",
            sequence=0)).status_code == 202
        assert _wait(lambda: pid_file.exists()), "child never started"
    # `with` exit ran the drain: 0.3s timeout -> cancel -> SIGKILL the child.
    pid = int(pid_file.read_text())

    def dead() -> bool:
        try:
            os.kill(pid, 0)
            return False
        except ProcessLookupError:
            return True

    assert _wait(dead, timeout=5.0), "child survived the drain cancel (ghost leak)"
    assert not done_file.exists()            # the 30s computation was truly reclaimed
    assert fake_storage.record_posts == []   # nothing half-written
    # The chunk is still durably 'accepted' -> the next startup re-drives it.
    assert [c1["chunk_id"] for c1 in app.state.journal.pending_accepted()] == ["iso-ghost"]
