"""Durable ingest journal (app/journal.py) — the M7 restart story.

Proves the three guarantees the journal exists for, each across a REAL app restart
(two create_app() instances over the same DP_VAR_DIR):

  1. kill-recovery — an accepted-but-unprocessed chunk survives and is auto-re-driven
     at the next startup, with no recording involvement;
  2. restart amnesia closed — continuity's processed/dead_lettered (and coverage)
     rehydrate, so a post-restart /continuity answer can never mis-read intact
     history as a leading gap (the deferred false-`gaps` caveat);
  3. durable dedup — a redelivery after restart is answered with the prior
     record_ids (200), never a reprocess.

Plus unit tests of the journal's own state machine (accept/dead-letter/processed,
QueueFull delete, counts) and lazy filesystem behaviour.
"""
from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.journal import Journal
from app.processing.base import ProcessedContent, ProcessedUnit, Processor
from tests.fake_storage import FakeStorage
from tests.conftest import make_c1


def _wait(pred, timeout: float = 5.0, interval: float = 0.01) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return False


def _c1(fs, **kw):
    return make_c1(fs, **kw)


# ---- Journal unit behaviour ---------------------------------------------------

def test_journal_is_lazy_and_reads_empty_without_db(tmp_path):
    j = Journal(tmp_path / "var" / "dp.db")
    # No filesystem touch on construction or reads.
    assert j.pending_accepted() == []
    assert j.processed_record_ids("nope") is None
    assert j.rehydration() == {}
    assert j.counts() == {"pending": 0, "dead_letter": 0, "processed": 0}
    assert not (tmp_path / "var").exists()


def test_journal_state_machine(tmp_path):
    j = Journal(tmp_path / "dp.db")
    fs = FakeStorage()
    c1 = _c1(fs, chunk_id="j-1", stream_id="s-j", sequence=0)

    epoch0, prior = j.accept(c1, "2026-07-20T00:00:00Z")
    assert epoch0 == 0 and prior is None
    assert [c["chunk_id"] for c in j.pending_accepted()] == ["j-1"]
    assert j.counts()["pending"] == 1

    # Dead-letter (epoch-guarded): durable, out of the re-drive set, counted.
    j.mark_dead_letter("j-1", "blob gone", "2026-07-20T00:01:00Z", epoch0)
    assert j.pending_accepted() == []
    assert j.counts() == {"pending": 0, "dead_letter": 1, "processed": 0}
    # Rehydration surfaces it as dead for its stream.
    re = j.rehydration()
    assert re["s-j"]["dead"][0][:2] == (0, "j-1")

    # Redelivery resets it to accepted (another attempt): epoch bumps, prior snapshot
    # carries the replaced dead_letter row.
    epoch1, prior = j.accept(c1, "2026-07-20T00:02:00Z")
    assert epoch1 == epoch0 + 1 and prior["state"] == "dead_letter"
    assert [c["chunk_id"] for c in j.pending_accepted()] == ["j-1"]

    # Processing moves it to processed in one step (epoch-matched delete).
    j.mark_processed(c1, ["r1", "r2"], "asr-mock-v0", "2026-07-20T00:03:00Z", epoch1)
    assert j.pending_accepted() == []
    assert j.processed_record_ids("j-1") == ["r1", "r2"]
    assert j.counts() == {"pending": 0, "dead_letter": 0, "processed": 1}
    re = j.rehydration()
    assert re["s-j"]["processed"][0][:2] == (0, "j-1")
    assert re["s-j"]["dead"] == []  # the dead mark was superseded


def test_stale_worker_epoch_writes_no_op(tmp_path):
    """The epoch contract (memo L1-F1): a stale worker's terminal write after a
    redelivery re-accepted the chunk NO-OPS — the fresh accepted row survives."""
    j = Journal(tmp_path / "dp.db")
    fs = FakeStorage()
    c1 = _c1(fs, chunk_id="j-e", stream_id="s-e", sequence=0)
    epoch0, _ = j.accept(c1, "2026-07-20T00:00:00Z")
    epoch1, _ = j.accept(c1, "2026-07-20T00:01:00Z")   # redelivery re-accepts
    assert epoch1 == epoch0 + 1

    # Stale dead-letter (old epoch) -> no-op: row still accepted.
    j.mark_dead_letter("j-e", "stale failure", "2026-07-20T00:02:00Z", epoch0)
    assert [c["chunk_id"] for c in j.pending_accepted()] == ["j-e"]
    assert j.counts()["dead_letter"] == 0

    # Stale processed (old epoch): pending row SURVIVES (epoch-guarded delete no-ops)
    # but the receipt is recorded — the C2s were genuinely written.
    j.mark_processed(c1, ["r"], "asr-mock-v0", "2026-07-20T00:03:00Z", epoch0)
    assert [c["chunk_id"] for c in j.pending_accepted()] == ["j-e"]
    assert j.processed_record_ids("j-e") == ["r"]
    # The current-epoch completion then clears pending.
    j.mark_processed(c1, ["r"], "asr-mock-v0", "2026-07-20T00:04:00Z", epoch1)
    assert j.pending_accepted() == []


def test_unaccept_restores_or_deletes(tmp_path):
    """QueueFull rollback: a fresh row is deleted; a replaced dead_letter row is
    RESTORED with its history (the 503 must not erase durable dead-letter state)."""
    j = Journal(tmp_path / "dp.db")
    fs = FakeStorage()
    c1 = _c1(fs, chunk_id="j-503", stream_id="s-j", sequence=1)

    # Fresh accept -> unaccept -> gone.
    epoch, prior = j.accept(c1, "2026-07-20T00:00:00Z")
    j.unaccept("j-503", prior)
    assert j.pending_accepted() == [] and j.counts()["pending"] == 0

    # Dead-lettered chunk -> redelivery accept -> QueueFull -> unaccept restores the
    # dead_letter row (with its error), not a hole.
    epoch, _ = j.accept(c1, "2026-07-20T00:01:00Z")
    j.mark_dead_letter("j-503", "terminal", "2026-07-20T00:02:00Z", epoch)
    _, prior = j.accept(c1, "2026-07-20T00:03:00Z")
    j.unaccept("j-503", prior)
    assert j.counts() == {"pending": 0, "dead_letter": 1, "processed": 0}


def test_redrive_cap_is_per_processing_attempt_not_per_restart(tmp_path):
    """Crash-loop cap is EVIDENCE-BASED: pending_for_redrive dead-letters only rows whose
    OWN processing attempts (note_redrive_attempt) exceed the cap — a restart that never
    dequeues a chunk must not charge it (the old blanket per-restart increment did, mass
    dead-lettering innocent co-pending chunks)."""
    j = Journal(tmp_path / "dp.db")
    fs = FakeStorage()
    poison = _c1(fs, chunk_id="poison", stream_id="s", sequence=0)
    innocent = _c1(fs, chunk_id="innocent", stream_id="s", sequence=1)
    j.accept(poison, "t0")
    j.accept(innocent, "t0")

    # Simulate 5 restarts where ONLY 'poison' is ever dequeued+attempted (it crashes the
    # service each time); 'innocent' sits in the backlog, never dequeued.
    for i in range(5):
        rows = j.pending_for_redrive(2, f"t{i}")           # cap=2
        got = {r["c1"]["chunk_id"] for r in rows}
        j.note_redrive_attempt("poison", f"t{i}")          # only poison attempted
        if "poison" not in got:                            # poison already dead-lettered
            break

    # poison exceeded 2 attempts -> dead-lettered; innocent (0 attempts) stays alive.
    survivors = {r["c1"]["chunk_id"] for r in j.pending_for_redrive(2, "tN")}
    assert "innocent" in survivors                          # never falsely dead-lettered
    assert "poison" not in survivors
    counts = j.counts()
    assert counts["dead_letter"] == 1 and counts["pending"] == 1

    # A redelivery re-arms poison with a fresh budget (accept resets redrive_attempts).
    j.accept(poison, "tR")
    assert "poison" in {r["c1"]["chunk_id"] for r in j.pending_for_redrive(2, "tR2")}


def test_processed_record_ids_pipeline_version_check(tmp_path):
    """A receipt under an OLD dialect is not served: the redelivery reprocesses under
    the new config (version-forward) instead of getting stale ids."""
    j = Journal(tmp_path / "dp.db")
    fs = FakeStorage()
    c1 = _c1(fs, chunk_id="j-pv", stream_id="s-pv", sequence=0)
    j.mark_processed(c1, ["old-id"], "asr-mock-v0", "2026-07-20T00:00:00Z")

    assert j.processed_record_ids("j-pv") == ["old-id"]                    # no check
    assert j.processed_record_ids("j-pv", lambda m: "asr-mock-v0") == ["old-id"]
    assert j.processed_record_ids("j-pv", lambda m: "asr-fw-v1") is None   # dialect moved
    assert j.processed_record_ids("j-pv", lambda m: None) == ["old-id"]    # can't judge


# ---- Restart drills (two apps over one var dir) --------------------------------

class _GateProcessor(Processor):
    """Blocks process() on an Event so a test can hold a chunk un-processed."""

    modality = "audio"
    content_kind = "transcript"

    def __init__(self) -> None:
        self.gate = threading.Event()
        self.calls = 0

    def pipeline_version(self, settings) -> str:
        return "asr-mock-v0"

    def process(self, c1, blob, settings, span_seconds):
        self.calls += 1
        self.gate.wait(timeout=10)
        return [ProcessedUnit(content=ProcessedContent(kind="transcript",
                                                       text=f"ok {c1['chunk_id']}"))]


def _async_env(monkeypatch, tmp_path, **extra):
    monkeypatch.setenv("ASR_BACKEND", "mock")
    monkeypatch.setenv("STORAGE_URL", "http://storage.test")
    monkeypatch.setenv("DP_VAR_DIR", str(tmp_path / "var"))
    monkeypatch.setenv("INGEST_ASYNC", "1")
    monkeypatch.setenv("INGEST_RETRY_BACKOFF", "0")
    for k, v in extra.items():
        monkeypatch.setenv(k, v)


def test_kill_recovery_startup_redrive(monkeypatch, tmp_path):
    """Accept a chunk, 'kill' the app before it processes (drain timeout 0 cancels the
    gated worker), then boot a NEW app on the same var dir: the journal re-drives it
    to completion with no external re-POST."""
    _async_env(monkeypatch, tmp_path, INGEST_DRAIN_TIMEOUT="0")
    import app.main as main_mod
    from app.processing.registry import get_processor as real_get_processor
    from tests.conftest import SAMPLE_AUDIO

    fs1 = FakeStorage()
    gated = _GateProcessor()
    gate_on = {"v": True}  # phase toggle: app1 sees the gate, app2 the real registry
    monkeypatch.setattr(
        main_mod, "get_processor",
        lambda modality: gated if gate_on["v"] else real_get_processor(modality),
    )
    app1 = main_mod.create_app()
    app1.state.storage._transport = fs1.transport()
    with TestClient(app1) as c:
        r = c.post("/ingest", json=_c1(fs1, chunk_id="kill-1", stream_id="s-k", sequence=0))
        assert r.status_code == 202
        assert _wait(lambda: gated.calls == 1)  # worker picked it up, gate-blocked
    # Exited with drain timeout 0 -> worker cancelled mid-process; nothing written.
    assert fs1.record_posts == []

    # "Restart": new app, same var dir, real processors; fresh fake storage that still
    # serves the blob (blob_ref is durable in /raw).
    gate_on["v"] = False
    fs2 = FakeStorage()
    fs2.add_blob("raw/pilot-user/chunk-ULID-0001.wav", SAMPLE_AUDIO)
    app2 = main_mod.create_app()
    app2.state.storage._transport = fs2.transport()
    with TestClient(app2) as c:
        # No POST at all — the startup re-drive must process it on its own.
        assert _wait(lambda: len(fs2.record_posts) == 1), "journal re-drive never ran"
        entry = c.get("/continuity/s-k").json()
        assert entry["processed"] == [[0, 0]]
    # Journal now holds the processed receipt, pending empty.
    j = Journal(tmp_path / "var" / "dp.db")
    assert j.counts()["pending"] == 0 and j.counts()["processed"] == 1


def test_restart_amnesia_closed_for_continuity(monkeypatch, tmp_path):
    """Process seqs 0-2 in app1; restart; WITHOUT any new delivery the new app's
    /continuity already knows them as seen+processed — recording's confirm loop
    works and no leading gap is fabricated when a later chunk arrives."""
    _async_env(monkeypatch, tmp_path)
    import app.main as main_mod

    fs = FakeStorage()
    app1 = main_mod.create_app()
    app1.state.storage._transport = fs.transport()
    with TestClient(app1) as c:
        for i in range(3):
            c.post("/ingest", json=_c1(fs, chunk_id=f"am-{i}", stream_id="s-am",
                                       sequence=i, blob_ref=f"raw/am-{i}.wav"))
        assert _wait(lambda: len(fs.record_posts) == 3)

    app2 = main_mod.create_app()
    app2.state.storage._transport = fs.transport()
    with TestClient(app2) as c:
        # Before ANY new delivery: rehydrated identity + processed runs.
        entry = c.get("/continuity/s-am").json()
        assert entry["user_id"] == "pilot-user" and entry["modality"] == "audio"
        assert entry["processed"] == [[0, 2]]
        assert entry["missing"] == [] and entry["dead_lettered"] == []
        # A later chunk arrives post-restart: history must NOT read as a leading gap.
        c.post("/ingest", json=_c1(fs, chunk_id="am-3", stream_id="s-am",
                                   sequence=3, blob_ref="raw/am-3.wav"))
        assert _wait(lambda: c.get("/continuity/s-am").json()["processed"] == [[0, 3]])
        entry = c.get("/continuity/s-am").json()
        assert entry["missing"] == []
        assert entry["max_sequence"] == 3


def test_durable_dedup_answers_redelivery_after_restart(monkeypatch, tmp_path):
    """A chunk processed before a restart: redelivery to the NEW app returns the
    prior record_ids (200) without reprocessing — no blob pull, no /context write."""
    _async_env(monkeypatch, tmp_path)
    import app.main as main_mod

    fs = FakeStorage()
    app1 = main_mod.create_app()
    app1.state.storage._transport = fs.transport()
    c1 = _c1(fs, chunk_id="dd-1", stream_id="s-dd", sequence=0)
    with TestClient(app1) as c:
        c.post("/ingest", json=c1)
        assert _wait(lambda: len(fs.record_posts) == 1)
    prior_ids = [fs.record_posts[0]["record_id"]]

    fs2 = FakeStorage()  # fresh fake: any blob pull / write would be visible
    app2 = main_mod.create_app()
    app2.state.storage._transport = fs2.transport()
    with TestClient(app2) as c:
        r = c.post("/ingest", json=c1)
        assert r.status_code == 200
        assert r.json()["record_ids"] == prior_ids
    assert fs2.blob_gets == [] and fs2.record_posts == []


def test_dead_letter_survives_restart(monkeypatch, tmp_path):
    """A dead-lettered chunk (missing blob = terminal) stays visible as dead_lettered
    after a restart — recording keeps reading it as gaps until a redelivery heals it."""
    _async_env(monkeypatch, tmp_path)
    import app.main as main_mod

    fs = FakeStorage()
    app1 = main_mod.create_app()
    app1.state.storage._transport = fs.transport()
    with TestClient(app1) as c:
        c.post("/ingest", json=_c1(fs, chunk_id="dl-r", stream_id="s-dlr", sequence=0,
                                   blob_ref="raw/never.wav", register_blob=False))
        assert _wait(lambda: c.get("/continuity/s-dlr").json()["dead_lettered"] == [[0, 0]])

    app2 = main_mod.create_app()
    app2.state.storage._transport = fs.transport()
    with TestClient(app2) as c:
        entry = c.get("/continuity/s-dlr").json()
        assert entry["dead_lettered"] == [[0, 0]] and entry["processed"] == []
        # Redelivery WITH the blob now present heals it end-to-end.
        healed = _c1(fs, chunk_id="dl-r", stream_id="s-dlr", sequence=0,
                     blob_ref="raw/never.wav")  # registers the blob this time
        r = c.post("/ingest", json=healed)
        assert r.status_code == 202
        assert _wait(lambda: c.get("/continuity/s-dlr").json()["processed"] == [[0, 0]])
        assert c.get("/continuity/s-dlr").json()["dead_lettered"] == []


# ---- rehydration classes + backlog + double-boot (memo Phase-A gaps) ----------

def test_pending_rehydrates_as_seen_not_processed():
    """A still-pending (accepted, unprocessed) chunk rehydrates as SEEN coverage but
    NEITHER processed NOR dead — so a restart can't fabricate a gap out of an in-flight
    chunk (the keystone), and recording reads it as 'recording', not 'clean'/'gaps'."""
    from app.continuity import ContinuityTracker
    t = ContinuityTracker()
    t.rehydrate({
        "s": {
            "user_id": "u", "device_id": "d", "modality": "audio",
            "processed": [(0, "c0", "2026-07-20T00:00:00Z")],
            "dead": [(2, "c2", "2026-07-20T00:02:00Z")],
            "accepted": [(1, "c1", "2026-07-20T00:01:00Z")],
        }
    })
    e = t.report_stream("s")
    assert e["max_sequence"] == 2
    assert e["missing"] == []                 # 1 is SEEN (accepted), not a gap
    assert e["processed"] == [[0, 0]]
    assert e["dead_lettered"] == [[2, 2]]     # 1 is neither processed nor dead
    assert e["received"] == 3


def test_pending_backlog_larger_than_queue_all_recover(monkeypatch, tmp_path):
    """Startup re-drive of MORE pending rows than the queue bound: the waiting submit
    drains them all as workers free slots (none stranded, none deleted)."""
    _async_env(monkeypatch, tmp_path, INGEST_QUEUE_MAX="1", INGEST_WORKERS="1")
    import app.main as main_mod
    from app.journal import Journal

    # Pre-seed the journal with 6 accepted chunks (no app processed them yet).
    j = Journal(tmp_path / "var" / "dp.db")
    fs = FakeStorage()
    for i in range(6):
        c1 = _c1(fs, chunk_id=f"bk-{i}", stream_id="s-bk", sequence=i,
                 blob_ref=f"raw/bk-{i}.wav")
        j.accept(c1, "2026-07-20T00:00:00Z")
    assert j.counts()["pending"] == 6

    app = main_mod.create_app()
    app.state.storage._transport = fs.transport()
    with TestClient(app) as c:
        assert _wait(lambda: len(fs.record_posts) == 6, timeout=8.0), "backlog not fully drained"
    j2 = Journal(tmp_path / "var" / "dp.db")
    assert j2.counts() == {"pending": 0, "dead_letter": 0, "processed": 6}


def test_double_lifespan_does_not_inflate_counters(monkeypatch, tmp_path):
    """Entering the lifespan twice on the SAME app (TestClient runs it per `with`) must
    not re-rehydrate over live state — duplicate_deliveries/received stay honest."""
    _async_env(monkeypatch, tmp_path)
    import app.main as main_mod
    fs = FakeStorage()
    app = main_mod.create_app()
    app.state.storage._transport = fs.transport()
    with TestClient(app) as c:
        c.post("/ingest", json=_c1(fs, chunk_id="d0", stream_id="s-d", sequence=0))
        assert _wait(lambda: len(fs.record_posts) == 1)
        first = c.get("/continuity/s-d").json()
    with TestClient(app) as c:   # second lifespan on the same app object
        entry = c.get("/continuity/s-d").json()
    assert entry["received"] == first["received"]
    assert entry["duplicate_deliveries"] == first["duplicate_deliveries"]
    assert entry["processed"] == [[0, 0]]
