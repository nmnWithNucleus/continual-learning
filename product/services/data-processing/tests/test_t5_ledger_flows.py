"""T-5 — ledger/heal flows: the FULL L7/L8 law + the crash table.

The redelivery matrix: skip (case 3) / version-forward (case 2) /
heal fills-the-hole byte-identically (case 4) / heal-exhaustion -> done_final +
permanent-holes metric + skip thereafter / poison -> dead-letter, visible,
redelivery re-arms. Plus the crash rows this file owns directly:
post-POST-pre-mark (redelivery reprocesses -> byte-identical upsert no-op ->
converges) and the statuses round-trip (failed vs cancelled DISTINCT after a
kill-9 of the journal). Every other crash row is owned by a named test
elsewhere, and this map is the record of which:

  * before journal.accept        -> test_async_ingest.test_failed_journal_accept_releases_claim
  * after accept, before work    -> test_journal.test_kill_recovery_startup_redrive
  * mid-graph                    -> test_async_ingest dead-letter/retry suite
  * epoch fencing                -> test_journal.test_stale_worker_epoch_writes_no_op
                                    + test_heal_failed_pending_delete_is_epoch_guarded…
  * model server crash           -> servers/common kill-one drill + model_client
                                    re-verify presentation tests

Seam-level heal containment (200-on-failure, never dead-letter, D16 bytes) is
pinned in test_heal_seam.py; this file owns the LAW-level matrix.
"""
from __future__ import annotations

import asyncio
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from app.journal import HEAL_MAX_ATTEMPTS, Journal
from app.stagegraph import stage as stage_mod
from app.stagegraph.stage import Backend, Stage, StageOutput
from tests.conftest import (
    MOCK_AUDIO_PV,
    MockAsrStage,
    install_mock_audio_registry,
    make_c1,
)
from tests.fake_storage import FakeStorage


def _wait(pred, timeout: float = 10.0, interval: float = 0.01) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return False


def test_same_dialect_redelivery_skips(client, monkeypatch):
    """L8 case 3 demands ALL GREEN: with this module's always-failing optional
    stage patched healthy, a same-dialect redelivery is a pure skip. (A HOLEY
    record's redelivery is a heal, not a skip — that flow is pinned below and in
    test_heal_seam.py.)"""
    monkeypatch.setattr(
        _OptionalBoom, "run_sync",
        lambda self, ctx: StageOutput(value={"values": ["keyboard typing"],
                                             "confidence": 0.9}),
    )
    fs = client.fake_storage
    c1 = make_c1(fs, chunk_id="t5-skip")
    first = client.post("/ingest", json=c1)
    again = client.post("/ingest", json=c1)
    assert first.status_code == again.status_code == 200
    assert again.json() == first.json()
    assert len(fs.record_posts) == 1
    assert len(fs.blob_gets) == 1  # the skip never re-pulled the blob


def test_version_forward_reprocess_lands_beside(client, monkeypatch):
    """A dialect change between deliveries: the redelivery is NOT served the
    stale receipt — it reprocesses under the new version and the new record
    lands beside the old (distinct record_id, both retained by the upsert)."""
    fs = client.fake_storage
    c1 = make_c1(fs, chunk_id="t5-vfwd")
    first = client.post("/ingest", json=c1)
    assert first.status_code == 200
    (old_id,) = first.json()["record_ids"]

    # The dialect moves: vB bump on the mock asr backend (a code change,
    # simulated the only way a test can — by swapping the registered instance).
    bumped = type("MockAsrV2", (MockAsrStage,), {"backend": Backend("mock", 2)})
    stage_mod._REGISTRY["audio"]["asr"] = bumped()
    # The in-memory dedup map would still answer; drop it to model a redelivery
    # arriving at a restarted process (the durable journal is what must judge —
    # its pipeline_version check is the version-forward gate).
    client.app.state.dedup._done.clear()

    second = client.post("/ingest", json=c1)
    assert second.status_code == 200
    (new_id,) = second.json()["record_ids"]
    assert new_id != old_id
    assert len(fs.record_posts) == 2           # beside, not instead
    assert {r["record_id"] for r in fs.record_posts} == {old_id, new_id}
    assert fs.record_posts[1]["pipeline_version"] == \
        "acoustic.v1-mock.v1+asr.v1-mock.v2"
    # Ledger v2: the done-row is keyed per chunk_id — latest attempt wins, the
    # replaced dialect recorded, the heal budget reset (the new version's own).
    row = client.app.state.journal.done_row("t5-vfwd")
    assert row["record_ids"] == [new_id]
    assert row["pipeline_version"] == "acoustic.v1-mock.v1+asr.v1-mock.v2"
    assert row["superseded_pv"] == "acoustic.v1-mock.v1+asr.v1-mock.v1"
    assert row["heal_attempts"] == 0 and row["done_final"] is False


def test_required_failure_no_record_attempt_fails(client, monkeypatch):
    def boom(self, ctx):
        raise RuntimeError("required stage down")

    fs = client.fake_storage
    with monkeypatch.context() as m:
        m.setattr(MockAsrStage, "run_sync", boom)
        # Inline mode surfaces the leaf exception (the executor's re-raise);
        # the TestClient re-raises what would be the 500.
        with pytest.raises(RuntimeError, match="required stage down"):
            client.post("/ingest", json=make_c1(fs, chunk_id="t5-reqfail"))
    assert fs.record_posts == []               # NO record (L7)
    # The chunk is not falsely done: a redelivery with the stage healthy
    # processes it fully.
    resp2 = client.post("/ingest", json=make_c1(fs, chunk_id="t5-reqfail"))
    assert resp2.status_code == 200
    assert len(fs.record_posts) == 1


class _OptionalBoom(Stage):
    name = "acoustic"
    modality = "audio"
    stage_version = 1
    backend = Backend("mock", 1)
    required = False
    byte_budget = 1024
    consumer = "speculative:test_fixture"

    def run_sync(self, ctx):
        raise RuntimeError("optional stage down")


@pytest.fixture()
def mock_registry(monkeypatch):
    from tests.conftest import install_mock_audio_registry
    reg = install_mock_audio_registry(monkeypatch)
    stage_mod.register_stage(_OptionalBoom)
    return reg


def test_optional_failure_ships_holey_record(client):
    """L7's second half: hole, statuses recorded, record ships. The heal that
    fills the hole is pinned below and in test_heal_seam.py."""
    fs = client.fake_storage
    resp = client.post("/ingest", json=make_c1(fs, chunk_id="t5-hole"))
    assert resp.status_code == 200
    (record,) = fs.record_posts
    assert "acoustic" in record["pipeline_version"]      # attempted (in dialect)
    assert "acoustic" not in record["content"]["slots"]  # failed (a hole)
    assert "asr" in record["content"]["slots"]           # the record still ships


# ---- The full law: heal byte-identity, exhaustion, crash rows -----------------

def test_healed_record_byte_identical_to_never_holed_run(monkeypatch, tmp_path):
    """L8 case 4's strongest form: heal fills the hole and the healed record is
    BYTE-IDENTICAL to a never-holed run of the same version — provable because
    assembly is sorted and no wall-clock field exists in the record."""
    from app.main import create_app
    from tests.test_heal_seam import _FlakyAcoustic

    install_mock_audio_registry(monkeypatch)
    stage_mod.register_stage(_FlakyAcoustic)
    monkeypatch.setenv("STORAGE_URL", "http://storage.test")

    # App 1: hole, then heal.
    monkeypatch.setenv("DP_VAR_DIR", str(tmp_path / "var1"))
    fs1 = FakeStorage()
    app1 = create_app()
    app1.state.storage._transport = fs1.transport()
    with TestClient(app1) as c1:
        _FlakyAcoustic.down = True
        assert c1.post("/ingest", json=make_c1(fs1, chunk_id="t5-byte")).status_code == 200
        _FlakyAcoustic.down = False
        assert c1.post("/ingest", json=make_c1(fs1, chunk_id="t5-byte")).status_code == 200
    assert len(fs1.record_posts_raw) == 2
    holey, healed = fs1.record_posts_raw

    # App 2 (fresh journal, healthy fleet): the never-holed reference run.
    monkeypatch.setenv("DP_VAR_DIR", str(tmp_path / "var2"))
    fs2 = FakeStorage()
    app2 = create_app()
    app2.state.storage._transport = fs2.transport()
    with TestClient(app2) as c2:
        assert c2.post("/ingest", json=make_c1(fs2, chunk_id="t5-byte")).status_code == 200

    assert healed == fs2.record_posts_raw[0]     # byte-identical to never-holed
    assert holey != healed                       # and the hole genuinely differed


def test_heal_exhaustion_fires_metrics_and_skips(client):
    """Heal-exhaustion: done_final + the permanent-holes metric (once, per holed
    stage) + skip thereafter. The attempts metric counts every heal re-run."""
    fs = client.fake_storage
    c1 = make_c1(fs, chunk_id="t5-mexh")
    client.post("/ingest", json=c1)                       # holey ship (boom stage)
    for _ in range(HEAL_MAX_ATTEMPTS):
        client.post("/ingest", json=c1)                   # still-holey heals

    row = client.app.state.journal.done_row("t5-mexh")
    assert row["done_final"] is True and row["heal_attempts"] == HEAL_MAX_ATTEMPTS
    rendered = client.get("/metrics").text
    assert (f'dp_heal_attempts_total{{modality="audio"}} {HEAL_MAX_ATTEMPTS}'
            in rendered)
    assert ('dp_records_finalized_with_permanent_holes_total{stage="acoustic"} 1'
            in rendered)

    posts = len(fs.record_posts)
    skip = client.post("/ingest", json=c1)                # budget spent -> skip
    assert skip.status_code == 200
    assert len(fs.record_posts) == posts                  # no POST, no re-run
    rendered = client.get("/metrics").text                # metrics unchanged
    assert (f'dp_heal_attempts_total{{modality="audio"}} {HEAL_MAX_ATTEMPTS}'
            in rendered)


def test_crash_after_post_before_mark_redelivery_converges(client, monkeypatch):
    """Crash after POST, before mark_processed. The redelivery finds no
    receipt, reprocesses FULLY, and the re-POST is byte-identical — storage's
    upsert is a no-op and the flow converges (then skips, all green here)."""
    monkeypatch.setattr(
        _OptionalBoom, "run_sync",
        lambda self, ctx: StageOutput(value={"values": [], }),
    )
    j = client.app.state.journal
    real = j.mark_processed
    calls = {"n": 0}

    def crash_once(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("simulated crash after POST, before mark")
        return real(*a, **kw)

    monkeypatch.setattr(j, "mark_processed", crash_once)
    fs = client.fake_storage
    c1 = make_c1(fs, chunk_id="t5-crash")
    with pytest.raises(sqlite3.OperationalError):         # the crash surfaces
        client.post("/ingest", json=c1)
    assert len(fs.record_posts) == 1                      # the POST had landed
    assert j.done_row("t5-crash") is None                 # the receipt had not

    second = client.post("/ingest", json=c1)              # redelivery: full rerun
    assert second.status_code == 200
    assert len(fs.record_posts_raw) == 2
    assert fs.record_posts_raw[0] == fs.record_posts_raw[1]   # upsert no-op
    third = client.post("/ingest", json=c1)               # converged: pure skip
    assert third.status_code == 200 and len(fs.record_posts) == 2


class _ConeAlign(Stage):
    """Optional stage downstream of the always-failing acoustic — its status is
    CANCELLED (never ran), and the ledger must keep that distinct from failed."""

    name = "speaker_align"
    modality = "audio"
    stage_version = 1
    backend = Backend("mock", 1)
    required = False
    byte_budget = 4096
    slot = "transcript"
    needs = ("acoustic",)
    consumer = "speculative:test_fixture"

    def run_sync(self, ctx):  # never reached — the cone is cancelled
        return StageOutput(value={"splits": []})


def test_statuses_roundtrip_failed_vs_cancelled_after_kill9(monkeypatch, tmp_path):
    """The ledger keeps failed vs cancelled DISTINCT across a kill-9: post-hoc
    inspection through a FRESH Journal handle (the recovery-test idiom — the
    sqlite file is all that survives) and a restarted app both read the same
    evidence, and the claim tree heals off it."""
    from app.main import create_app
    from app.stagegraph.processor import graph_processor

    install_mock_audio_registry(monkeypatch)
    stage_mod.register_stage(_OptionalBoom)
    stage_mod.register_stage(_ConeAlign)
    monkeypatch.setenv("STORAGE_URL", "http://storage.test")
    monkeypatch.setenv("DP_VAR_DIR", str(tmp_path / "var"))

    fs = FakeStorage()
    app1 = create_app()
    app1.state.storage._transport = fs.transport()
    with TestClient(app1) as c:
        assert c.post("/ingest", json=make_c1(fs, chunk_id="t5-rt")).status_code == 200

    # Kill-9: no clean handoff — a brand-new Journal handle over the file.
    row = Journal(tmp_path / "var" / "dp.db").done_row("t5-rt")
    assert row["stage_status"] == {
        "acoustic": "failed",            # ran and failed
        "asr": "ok",
        "speaker_align": "cancelled",    # never ran (the cancelled cone)
    }

    # A restarted app judges the SAME evidence: holes + budget -> heal.
    app2 = create_app()
    app2.state.storage._transport = fs.transport()
    with TestClient(app2):
        pv = graph_processor("audio").pipeline_version()
    claim = asyncio.run(app2.state.dedup.classify("t5-rt", pv))
    assert claim.verdict == "heal"
    assert claim.row["stage_status"]["speaker_align"] == "cancelled"


def test_poison_chunk_dead_letters_then_redelivery_rearms(monkeypatch, tmp_path):
    """The matrix's poison row (L7): a terminal failure dead-letters — VISIBLE
    in the journal and continuity, never a silent drop, and never a record —
    and an external redelivery re-arms it for a clean run."""
    from app.main import create_app

    install_mock_audio_registry(monkeypatch)
    monkeypatch.setenv("STORAGE_URL", "http://storage.test")
    monkeypatch.setenv("DP_VAR_DIR", str(tmp_path / "var"))
    monkeypatch.setenv("INGEST_ASYNC", "1")
    monkeypatch.setenv("INGEST_WORKERS", "2")
    monkeypatch.setenv("INGEST_MAX_RETRIES", "0")
    monkeypatch.setenv("INGEST_RETRY_BACKOFF", "0")

    fs = FakeStorage()
    app = create_app()
    app.state.storage._transport = fs.transport()
    with TestClient(app) as c:
        j = app.state.journal
        c1 = make_c1(fs, chunk_id="t5-poison", register_blob=False)  # blob 404: terminal
        assert c.post("/ingest", json=c1).status_code == 202
        assert _wait(lambda: j.counts()["dead_letter"] == 1)
        assert fs.record_posts == []                     # no record (L7)
        report = c.get("/continuity").json()["streams"][0]
        assert report["dead_lettered"] == [[0, 0]]       # visible loss, never clean

        fs.add_blob(c1["blob_ref"], b"RIFF\x00\x00\x00\x00WAVEfmt mock-audio-chunk-bytes")
        assert c.post("/ingest", json=c1).status_code == 202   # redelivery re-arms
        assert _wait(lambda: j.counts() ==
                     {"pending": 0, "dead_letter": 0, "processed": 1})
        assert len(fs.record_posts) == 1                 # processed clean
