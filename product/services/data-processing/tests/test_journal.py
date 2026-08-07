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
from tests.fake_storage import FakeStorage
from tests.conftest import (
    MOCK_AUDIO_PV,
    FakeGraphProcessor,
    install_mock_audio_registry,
    make_c1,
)


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
        rows, _ = j.pending_for_redrive(2, f"t{i}")        # cap=2
        got = {r["c1"]["chunk_id"] for r in rows}
        j.note_redrive_attempt("poison", f"t{i}")          # only poison attempted
        if "poison" not in got:                            # poison already dead-lettered
            break

    # poison exceeded 2 attempts -> dead-lettered; innocent (0 attempts) stays alive.
    survivors = {r["c1"]["chunk_id"] for r in j.pending_for_redrive(2, "tN")[0]}
    assert "innocent" in survivors                          # never falsely dead-lettered
    assert "poison" not in survivors
    counts = j.counts()
    assert counts["dead_letter"] == 1 and counts["pending"] == 1

    # A redelivery re-arms poison with a fresh budget (accept resets redrive_attempts).
    j.accept(poison, "tR")
    assert "poison" in {r["c1"]["chunk_id"] for r in j.pending_for_redrive(2, "tR2")[0]}


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


# ---- Ledger v2 (L8/D27): done-row extension + heal bookkeeping ----------------

def test_done_row_returns_ledger_v2_fields(tmp_path):
    """The extended done-row: statuses verbatim (failed vs cancelled DISTINCT),
    heal budget fields, and the specified-but-unpopulated cached_slots seat."""
    j = Journal(tmp_path / "dp.db")
    fs = FakeStorage()
    c1 = _c1(fs, chunk_id="d-row", stream_id="s-d", sequence=0)
    statuses = {"asr": "ok", "acoustic": "failed", "speaker_align": "cancelled"}
    j.mark_processed(c1, ["rid-1"], "pv-1", "t0", statuses=statuses)

    row = j.done_row("d-row")
    assert row["record_ids"] == ["rid-1"]
    assert row["pipeline_version"] == "pv-1"
    assert row["modality"] == "audio"
    assert row["stage_status"] == statuses          # the L8 map, verbatim
    assert row["heal_attempts"] == 0
    assert row["done_final"] is False
    assert row["cached_slots"] is None              # schema seat only (L8), no logic
    assert row["superseded_pv"] is None
    assert j.done_row("missing") is None


def test_mark_processed_without_statuses_is_a_legacy_row(tmp_path):
    """Pre-D-shaped calls still work; the row reads with NULL statuses and benign
    defaults (a legacy row carries no hole evidence — classification must skip)."""
    j = Journal(tmp_path / "dp.db")
    fs = FakeStorage()
    c1 = _c1(fs, chunk_id="d-legacy", stream_id="s-d", sequence=1)
    j.mark_processed(c1, ["rid"], "pv-1", "t0")
    row = j.done_row("d-legacy")
    assert row["stage_status"] is None
    assert row["heal_attempts"] == 0 and row["done_final"] is False


def test_heal_marks_increment_only_while_holes_remain(tmp_path):
    """A completed heal that still leaves holes increments the budget; a fully
    green heal clears to skip-state WITHOUT an increment (the counter counts this
    chunk's own non-green heals, never deliveries)."""
    j = Journal(tmp_path / "dp.db")
    fs = FakeStorage()
    c1 = _c1(fs, chunk_id="d-heal", stream_id="s-d", sequence=2)
    holey = {"asr": "ok", "acoustic": "failed"}
    j.mark_processed(c1, ["rid"], "pv-1", "t0", statuses=holey)      # fresh holey ship

    st = j.mark_processed(c1, ["rid"], "pv-1", "t1", statuses=holey, heal=True)
    assert st["heal_attempts"] == 1 and st["done_final"] is False
    assert st["newly_final"] is False

    green = {"asr": "ok", "acoustic": "ok"}
    st = j.mark_processed(c1, ["rid"], "pv-1", "t2", statuses=green, heal=True)
    assert st["heal_attempts"] == 1                  # green heal: no increment
    assert st["done_final"] is False
    assert j.done_row("d-heal")["stage_status"] == green


def test_heal_budget_exhaustion_finalizes_once(tmp_path):
    """At HEAL_MAX_ATTEMPTS with holes still present the row goes done_final —
    newly_final is True exactly once (the metric-firing edge)."""
    from app.journal import HEAL_MAX_ATTEMPTS

    j = Journal(tmp_path / "dp.db")
    fs = FakeStorage()
    c1 = _c1(fs, chunk_id="d-exh", stream_id="s-d", sequence=3)
    holey = {"asr": "ok", "acoustic": "failed"}
    j.mark_processed(c1, ["rid"], "pv-1", "t0", statuses=holey)

    finals = []
    for i in range(HEAL_MAX_ATTEMPTS + 1):
        st = j.mark_processed(c1, ["rid"], "pv-1", f"t{i}", statuses=holey, heal=True)
        finals.append((st["heal_attempts"], st["done_final"], st["newly_final"]))
    assert finals[HEAL_MAX_ATTEMPTS - 1] == (HEAL_MAX_ATTEMPTS, True, True)
    assert finals[HEAL_MAX_ATTEMPTS] == (HEAL_MAX_ATTEMPTS + 1, True, False)
    assert all(not f[1] for f in finals[: HEAL_MAX_ATTEMPTS - 1])


def test_heal_failed_increments_never_regresses_and_finalizes_at_budget(tmp_path):
    """A heal whose re-run FAILED (even a required stage — fleet down): the chunk
    keeps its record and done-row untouched except the budget; at budget the row
    finalizes. A heal never dead-letters a chunk that has a durable record."""
    from app.journal import HEAL_MAX_ATTEMPTS

    j = Journal(tmp_path / "dp.db")
    fs = FakeStorage()
    c1 = _c1(fs, chunk_id="d-hf", stream_id="s-d", sequence=4)
    holey = {"asr": "ok", "acoustic": "failed"}
    j.mark_processed(c1, ["rid"], "pv-1", "t0", statuses=holey)

    # A heal claim on the async path re-accepts (pending row) before the run.
    epoch, _ = j.accept(c1, "t1")
    st = j.heal_failed("d-hf", "t2", epoch)
    assert st["heal_attempts"] == 1 and st["newly_final"] is False
    assert j.pending_accepted() == []               # pending cleared, NOT dead_letter
    assert j.counts() == {"pending": 0, "dead_letter": 0, "processed": 1}
    row = j.done_row("d-hf")                        # done never regresses
    assert row["record_ids"] == ["rid"] and row["stage_status"] == holey

    for _ in range(HEAL_MAX_ATTEMPTS - 2):
        st = j.heal_failed("d-hf", "t3")
    st = j.heal_failed("d-hf", "t4")
    assert st["heal_attempts"] == HEAL_MAX_ATTEMPTS
    assert st["done_final"] is True and st["newly_final"] is True
    assert j.heal_failed("d-hf", "t5")["newly_final"] is False   # fires once

    assert j.heal_failed("never-processed", "t6") is None        # no row -> no-op


def test_heal_failed_on_an_all_green_row_is_a_no_op(tmp_path):
    """Close-out round: heal_failed mirrors mark_processed's evidence-based
    arithmetic — a row with no hole evidence (all-green: a racing worker healed
    it green first) is neither charged nor finalized by a stale failure report.
    Without the guard, stale writers could finalize a row with newly_final only
    ever False on the holey rewrite, silently swallowing the permanent-holes
    metric. The pending clear (the delivery IS over) still runs."""
    from app.journal import HEAL_MAX_ATTEMPTS

    j = Journal(tmp_path / "dp.db")
    fs = FakeStorage()
    c1 = _c1(fs, chunk_id="j-green", stream_id="s-g", sequence=0)
    j.mark_processed(c1, ["rid"], "pv-1", "t0",
                     statuses={"asr": "ok", "acoustic": "ok"})
    epoch, _ = j.accept(c1, "t1")                    # a stale heal claim's row

    st = j.heal_failed("j-green", "t2", epoch)
    assert st is not None
    assert st["heal_attempts"] == 0                  # no charge without evidence
    assert st["done_final"] is False and st["newly_final"] is False
    row = j.done_row("j-green")
    assert row["heal_attempts"] == 0 and row["done_final"] is False
    assert j.pending_accepted() == []                # the claim's row still clears

    # Repeated stale reports can never finalize a green row.
    for i in range(HEAL_MAX_ATTEMPTS + 1):
        st = j.heal_failed("j-green", f"t{3 + i}")
    assert st["heal_attempts"] == 0 and st["done_final"] is False


def test_heal_failed_pending_delete_is_epoch_guarded_but_truth_is_not(tmp_path):
    """Same posture as mark_processed: the budget increment is TRUE regardless of
    which delivery's worker ran the failed heal; only the pending delete is
    epoch-guarded so a stale worker cannot clobber a fresh redelivery's row."""
    j = Journal(tmp_path / "dp.db")
    fs = FakeStorage()
    c1 = _c1(fs, chunk_id="d-ep", stream_id="s-d", sequence=5)
    j.mark_processed(c1, ["rid"], "pv-1", "t0",
                     statuses={"asr": "ok", "acoustic": "failed"})
    epoch0, _ = j.accept(c1, "t1")
    epoch1, _ = j.accept(c1, "t2")                  # redelivery re-accepts

    st = j.heal_failed("d-ep", "t3", epoch0)        # stale worker finishes late
    assert st["heal_attempts"] == 1                 # truth recorded
    assert [c["chunk_id"] for c in j.pending_accepted()] == ["d-ep"]  # row survives
    j.heal_failed("d-ep", "t4", epoch1)             # current epoch clears it
    assert j.pending_accepted() == []


def test_version_forward_records_superseded_pv_and_resets_budget(tmp_path):
    """L8 case 2: the done-row is keyed per chunk_id — the latest attempt wins,
    the superseded dialect is recorded in the row, and the heal budget is the NEW
    version's own (reset)."""
    j = Journal(tmp_path / "dp.db")
    fs = FakeStorage()
    c1 = _c1(fs, chunk_id="d-vf", stream_id="s-d", sequence=6)
    holey = {"asr": "ok", "acoustic": "failed"}
    j.mark_processed(c1, ["rid-1"], "pv-1", "t0", statuses=holey)
    j.mark_processed(c1, ["rid-1"], "pv-1", "t1", statuses=holey, heal=True)
    assert j.done_row("d-vf")["heal_attempts"] == 1

    j.mark_processed(c1, ["rid-2"], "pv-2", "t2", statuses=holey)
    row = j.done_row("d-vf")
    assert row["record_ids"] == ["rid-2"] and row["pipeline_version"] == "pv-2"
    assert row["superseded_pv"] == "pv-1"
    assert row["heal_attempts"] == 0 and row["done_final"] is False

    j.mark_processed(c1, ["rid-3"], "pv-3", "t3", statuses=holey)
    assert j.done_row("d-vf")["superseded_pv"] == "pv-2"   # latest supersession only


def test_pre_stage_d_journal_migrates_in_place(tmp_path):
    """Schema evolution ruling: a journal written before the ledger-v2 columns
    existed MIGRATES (ALTER TABLE, additive) rather than recreate — var/ is
    disposable, but dropping processed rows would un-SEE intact history at
    rehydration and fabricate gaps."""
    import sqlite3

    db = tmp_path / "dp.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE pending (
          chunk_id TEXT PRIMARY KEY, c1_json TEXT NOT NULL,
          state TEXT NOT NULL DEFAULT 'accepted', epoch INTEGER NOT NULL DEFAULT 0,
          attempts INTEGER NOT NULL DEFAULT 0, redrive_attempts INTEGER NOT NULL DEFAULT 0,
          last_error TEXT, accepted_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE processed (
          chunk_id TEXT PRIMARY KEY, stream_id TEXT NOT NULL, sequence INTEGER NOT NULL,
          user_id TEXT NOT NULL, device_id TEXT NOT NULL, modality TEXT NOT NULL,
          record_ids TEXT NOT NULL, pipeline_version TEXT NOT NULL, processed_at TEXT NOT NULL
        );
        CREATE INDEX idx_processed_stream ON processed (stream_id, sequence);
    """)
    conn.execute(
        "INSERT INTO processed VALUES ('old-1','s-old',0,'u','d','audio',"
        "'[\"rid-old\"]','asr-fw-v1','t0')")
    conn.execute(
        "INSERT INTO pending (chunk_id, c1_json, accepted_at, updated_at)"
        " VALUES ('old-2', '{\"chunk_id\":\"old-2\",\"stream_id\":\"s-old\","
        "\"sequence\":1,\"user_id\":\"u\",\"device_id\":\"d\",\"modality\":\"audio\"}',"
        " 't1', 't1')")
    conn.commit()
    conn.close()

    j = Journal(db)
    row = j.done_row("old-1")                       # migrated read: v2 defaults
    assert row["record_ids"] == ["rid-old"]
    assert row["stage_status"] is None and row["heal_attempts"] == 0
    assert row["done_final"] is False and row["cached_slots"] is None
    assert j.processed_record_ids("old-1") == ["rid-old"]
    assert j.counts() == {"pending": 1, "dead_letter": 0, "processed": 1}
    re = j.rehydration()
    assert re["s-old"]["processed"][0][:2] == (0, "old-1")
    assert re["s-old"]["accepted"][0][:2] == (1, "old-2")
    # And the migrated DB accepts v2 writes.
    fs = FakeStorage()
    c1 = _c1(fs, chunk_id="new-1", stream_id="s-old", sequence=2)
    j.mark_processed(c1, ["rid-new"], "pv-1", "t2", statuses={"asr": "ok"})
    assert j.done_row("new-1")["stage_status"] == {"asr": "ok"}


def test_receipt_supersedes_a_dead_letter_pending_row_any_epoch(tmp_path):
    """Review round (races lens): a durable receipt contradicts ANY dead_letter
    pending row — a chunk with a C2 written must never read as terminally lost.
    The epoch guard protects fresh ACCEPTED rows only; a dead_letter row is
    cleared by the receipt regardless of epoch (the stale-redrive-vs-live-worker
    interleaving: live attempt dead-letters at epoch N+1, the redrive job's
    receipt lands with snapshot epoch N — the row must not survive)."""
    j = Journal(tmp_path / "dp.db")
    fs = FakeStorage()
    c1 = _c1(fs, chunk_id="j-super", stream_id="s-x", sequence=0)
    epoch0, _ = j.accept(c1, "t0")
    epoch1, _ = j.accept(c1, "t1")                   # live redelivery re-accepts
    j.mark_dead_letter("j-super", "storage outage", "t2", epoch1)
    assert j.counts()["dead_letter"] == 1

    # The stale-epoch worker's receipt still lands (unguarded truth) AND clears
    # the contradicted dead_letter row.
    j.mark_processed(c1, ["rid"], "pv-1", "t3", epoch0, statuses={"asr": "ok"})
    assert j.counts() == {"pending": 0, "dead_letter": 0, "processed": 1}

    # Same rule on the heal_failed path: the chunk keeps a durable record, so a
    # coexisting dead_letter row is equally contradicted.
    j.accept(c1, "t4")
    e_new, _ = j.accept(c1, "t5")
    j.mark_dead_letter("j-super", "outage again", "t6", e_new)
    assert j.counts()["dead_letter"] == 1
    j.heal_failed("j-super", "t7", epoch=0)          # stale epoch on purpose
    assert j.counts()["dead_letter"] == 0

    # The guard on ACCEPTED rows is untouched: a stale receipt still no-ops.
    c2 = _c1(fs, chunk_id="j-super2", stream_id="s-x", sequence=1)
    j.accept(c2, "t8")
    e1, _ = j.accept(c2, "t9")
    j.mark_processed(c2, ["rid2"], "pv-1", "t10", 0, statuses={"asr": "ok"})
    assert [c["chunk_id"] for c in j.pending_accepted()] == ["j-super2"]


def test_clear_pending_is_epoch_guarded(tmp_path):
    """Review round: the redrive loop's skip-reconcile — a skip verdict is proof
    the ledger says done, so the stale pending row it was launched to resolve is
    cleared (guarded on the snapshot epoch: a row re-accepted since belongs to a
    live delivery and survives)."""
    j = Journal(tmp_path / "dp.db")
    fs = FakeStorage()
    c1 = _c1(fs, chunk_id="j-clear", stream_id="s-x", sequence=0)
    epoch0, _ = j.accept(c1, "t0")
    j.clear_pending("j-clear", epoch0 + 1)           # stale guard -> no-op
    assert j.counts()["pending"] == 1
    j.clear_pending("j-clear", epoch0)
    assert j.counts()["pending"] == 0
    j.clear_pending("never-there", 0)                # absent -> no-op, no error


def test_redrive_cap_finalizes_durable_record_chunks_never_dead_letters(tmp_path):
    """Heal containment at the crash-loop cap: a pending chunk that HAS a durable
    record (a crash-looping heal) is FINALIZED — pending cleared, done_final set,
    reported to the caller — never flipped to dead_letter (recording must not read
    a chunk with a durable record as a gap). A record-less poison chunk still
    dead-letters exactly as before."""
    j = Journal(tmp_path / "dp.db")
    fs = FakeStorage()
    healing = _c1(fs, chunk_id="heal-loop", stream_id="s", sequence=0)
    poison = _c1(fs, chunk_id="poison", stream_id="s", sequence=1)
    j.mark_processed(healing, ["rid"], "pv-1", "t0",
                     statuses={"asr": "ok", "acoustic": "failed"})
    j.accept(healing, "t1")                          # heal claim crash-looping
    j.accept(poison, "t1")                           # record-less poison
    for i in range(3):                               # both attempted past cap=2
        j.note_redrive_attempt("heal-loop", f"t{i}")
        j.note_redrive_attempt("poison", f"t{i}")

    rows, finalized = j.pending_for_redrive(2, "tN")
    assert rows == []                                # neither survives the cap
    assert [f["chunk_id"] for f in finalized] == ["heal-loop"]
    assert j.counts() == {"pending": 0, "dead_letter": 1, "processed": 1}
    row = j.done_row("heal-loop")
    assert row["done_final"] is True                 # holes permanent, visible
    assert row["record_ids"] == ["rid"]              # done never regressed
    # newly_final reported so the caller can fire the permanent-holes metric once.
    assert finalized[0]["newly_final"] is True
    assert finalized[0]["stage_status"] == {"asr": "ok", "acoustic": "failed"}


# ---- Restart drills (two apps over one var dir) --------------------------------

class _GateProcessor(FakeGraphProcessor):
    """Blocks the graph run on an Event so a test can hold a chunk un-processed
    (the callable runs in the threadpool — v0's blocking-gate idiom carries over)."""

    def __init__(self) -> None:
        self.gate = threading.Event()
        self.calls = 0

        def run(c1, blob, span):
            self.calls += 1
            self.gate.wait(timeout=10)
            return {"asr": {"version": MOCK_AUDIO_PV,
                            "value": f"ok {c1['chunk_id']}"}}

        super().__init__(run=run)


def _async_env(monkeypatch, tmp_path, **extra):
    """Env + the mock audio registry (create_app-based tests bypass the `client`
    fixture, so the registry install happens here)."""
    install_mock_audio_registry(monkeypatch)
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
    from app.stagegraph.processor import graph_processor as real_graph_processor
    from tests.conftest import SAMPLE_AUDIO

    fs1 = FakeStorage()
    gated = _GateProcessor()
    gate_on = {"v": True}  # phase toggle: app1 sees the gate, app2 the mock registry
    monkeypatch.setattr(
        main_mod, "graph_processor",
        lambda modality: gated if gate_on["v"] else real_graph_processor(modality),
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
