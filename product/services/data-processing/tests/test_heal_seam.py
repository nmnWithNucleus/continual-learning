"""WP-D2 seam wiring — the four verdicts routed at both ingest paths (L8).

Inline and async /ingest route fresh / version-forward / skip / heal from the
claim tree; the async path heal-claims via the queue like any job. Heal
containment at the seam: a heal re-runs the FULL graph off the redelivery's own
envelope, re-POSTs the SAME record_id (L3: same chunk_id + pv), and NEVER
regresses done — a failed heal (even a required stage: fleet down) keeps the
existing record and done-row, increments the budget, and answers 200 with the
existing record_id; it never dead-letters. The D16 wire shapes stay
byte-identical throughout (202/200/503).
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.journal import HEAL_MAX_ATTEMPTS, Journal
from app.stagegraph import stage as stage_mod
from app.stagegraph.stage import Backend, Stage, StageOutput
from tests.conftest import MockAsrStage, install_mock_audio_registry, make_c1
from tests.fake_storage import FakeStorage


def _wait(pred, timeout: float = 10.0, interval: float = 0.01) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return False


class _FlakyAcoustic(Stage):
    """Optional stage with a class-level kill switch — the 'model server down'
    stand-in. Slot value is a valid contract `acoustic` slot."""

    name = "acoustic"
    modality = "audio"
    stage_version = 1
    backend = Backend("mock", 1)
    required = False
    byte_budget = 1024
    consumer = "speculative:test_fixture"
    down = False

    def run_sync(self, ctx):
        if type(self).down:
            raise RuntimeError("acoustic server down")
        return StageOutput(value={"values": ["keyboard typing"], "confidence": 0.9})


@pytest.fixture()
def mock_registry(monkeypatch):
    """The conftest `client` fixture picks this override up: mock asr (required)
    + the flaky optional acoustic."""
    reg = install_mock_audio_registry(monkeypatch)
    _FlakyAcoustic.down = False
    stage_mod.register_stage(_FlakyAcoustic)
    return reg


def _journal(client) -> Journal:
    return client.app.state.journal


# ---- Inline seam ---------------------------------------------------------------

def test_inline_heal_fills_the_hole_same_record_id(client):
    fs = client.fake_storage
    c1 = make_c1(fs, chunk_id="seam-heal")

    _FlakyAcoustic.down = True
    first = client.post("/ingest", json=c1)
    assert first.status_code == 200
    (rid,) = first.json()["record_ids"]
    assert "acoustic" not in fs.record_posts[0]["content"]["slots"]   # the hole
    row = _journal(client).done_row("seam-heal")
    assert row["stage_status"]["acoustic"] == "failed"

    _FlakyAcoustic.down = False
    healed = client.post("/ingest", json=c1)                # redelivery -> heal
    assert healed.status_code == 200
    assert healed.json()["record_ids"] == [rid]             # SAME record_id
    assert len(fs.record_posts) == 2                        # upsert replaces holey
    assert fs.record_posts[1]["record_id"] == rid
    assert "acoustic" in fs.record_posts[1]["content"]["slots"]       # fuller
    row = _journal(client).done_row("seam-heal")
    assert all(v == "ok" for v in row["stage_status"].values())
    assert row["heal_attempts"] == 0                        # green heal: no charge

    third = client.post("/ingest", json=c1)                 # now skip (L8 case 3)
    assert third.status_code == 200 and third.json() == healed.json()
    assert len(fs.record_posts) == 2
    assert len(fs.blob_gets) == 2                           # skip never re-pulls


def test_inline_heal_failure_never_regresses_done(client, monkeypatch):
    """The containment law: mid-heal REQUIRED failure (asr down too) keeps the
    record + done-row, charges the budget, answers 200 + the existing id."""
    fs = client.fake_storage
    c1 = make_c1(fs, chunk_id="seam-hf")
    _FlakyAcoustic.down = True
    first = client.post("/ingest", json=c1)
    (rid,) = first.json()["record_ids"]

    def boom(self, ctx):
        raise RuntimeError("asr fleet down")

    with monkeypatch.context() as m:
        m.setattr(MockAsrStage, "run_sync", boom)
        resp = client.post("/ingest", json=c1)              # heal attempt fails
    assert resp.status_code == 200                          # NOT a 5xx, no re-raise
    assert resp.json()["record_ids"] == [rid]               # the durable record
    assert len(fs.record_posts) == 1                        # nothing new POSTed
    row = _journal(client).done_row("seam-hf")
    assert row["record_ids"] == [rid]                       # done never regressed
    assert row["stage_status"]["acoustic"] == "failed"      # statuses untouched
    assert row["heal_attempts"] == 1                        # the budget charged
    assert _journal(client).counts()["dead_letter"] == 0    # never dead-letters


def test_inline_heal_exhaustion_finalizes_then_skips(client):
    """Still-holey heals charge the budget; at HEAL_MAX_ATTEMPTS the row goes
    done_final and every later delivery is a pure skip (no graph, no blob)."""
    fs = client.fake_storage
    c1 = make_c1(fs, chunk_id="seam-exh")
    _FlakyAcoustic.down = True
    first = client.post("/ingest", json=c1)
    (rid,) = first.json()["record_ids"]

    for i in range(HEAL_MAX_ATTEMPTS):                      # heals: holey again
        resp = client.post("/ingest", json=c1)
        assert resp.status_code == 200 and resp.json()["record_ids"] == [rid]
        assert _journal(client).done_row("seam-exh")["heal_attempts"] == i + 1

    row = _journal(client).done_row("seam-exh")
    assert row["done_final"] is True                        # holes permanent
    posts_before, gets_before = len(fs.record_posts), len(fs.blob_gets)
    assert posts_before == 1 + HEAL_MAX_ATTEMPTS            # each heal re-POSTed

    after = client.post("/ingest", json=c1)                 # budget spent -> skip
    assert after.status_code == 200 and after.json()["record_ids"] == [rid]
    assert len(fs.record_posts) == posts_before             # no POST
    assert len(fs.blob_gets) == gets_before                 # no blob pull


def test_inline_heal_repost_is_byte_identical_while_holes_persist(client):
    """A still-holey heal re-POSTs the same bytes (sorted assembly + L3 identity):
    storage's byte-compare sees a no-op, `updated_at` must not re-window."""
    fs = client.fake_storage
    c1 = make_c1(fs, chunk_id="seam-noop")
    _FlakyAcoustic.down = True
    client.post("/ingest", json=c1)
    client.post("/ingest", json=c1)                         # heal, same holes
    assert len(fs.record_posts_raw) == 2
    assert fs.record_posts_raw[0] == fs.record_posts_raw[1]


# ---- Async seam (heal-claims via the queue like any job) -----------------------

@pytest.fixture()
def async_client(monkeypatch, tmp_path):
    install_mock_audio_registry(monkeypatch)
    _FlakyAcoustic.down = False
    stage_mod.register_stage(_FlakyAcoustic)
    fs = FakeStorage()
    monkeypatch.setenv("STORAGE_URL", "http://storage.test")
    monkeypatch.setenv("DP_VAR_DIR", str(tmp_path / "var"))
    monkeypatch.setenv("INGEST_ASYNC", "1")
    monkeypatch.setenv("INGEST_WORKERS", "2")
    monkeypatch.setenv("INGEST_MAX_RETRIES", "0")
    monkeypatch.setenv("INGEST_RETRY_BACKOFF", "0")
    from app.main import create_app

    app = create_app()
    app.state.storage._transport = fs.transport()
    with TestClient(app) as c:
        c.fake_storage = fs  # type: ignore[attr-defined]
        yield c


def test_async_heal_claim_rides_the_queue(async_client):
    fs = async_client.fake_storage
    c1 = make_c1(fs, chunk_id="aseam-heal")

    _FlakyAcoustic.down = True
    r1 = async_client.post("/ingest", json=c1)
    assert r1.status_code == 202
    assert r1.json() == {"ok": True, "accepted": True, "chunk_id": "aseam-heal"}
    assert _wait(lambda: len(fs.record_posts) == 1)
    (rid,) = [fs.record_posts[0]["record_id"]]

    _FlakyAcoustic.down = False
    r2 = async_client.post("/ingest", json=c1)              # heal claim
    # D16 wire byte-identical: a heal claim ACKs exactly like any accepted job.
    assert r2.status_code == 202
    assert r2.json() == {"ok": True, "accepted": True, "chunk_id": "aseam-heal"}
    assert _wait(lambda: len(fs.record_posts) == 2)
    assert fs.record_posts[1]["record_id"] == rid           # same id, fuller
    assert "acoustic" in fs.record_posts[1]["content"]["slots"]

    r3 = async_client.post("/ingest", json=c1)              # now skip: 200 + ids
    assert r3.status_code == 200
    assert r3.json() == {"ok": True, "record_ids": [rid]}


def test_async_heal_failure_no_dead_letter_no_retry_loop(async_client, monkeypatch):
    fs = async_client.fake_storage
    c1 = make_c1(fs, chunk_id="aseam-hf")
    _FlakyAcoustic.down = True
    async_client.post("/ingest", json=c1)
    assert _wait(lambda: len(fs.record_posts) == 1)
    rid = fs.record_posts[0]["record_id"]
    j = async_client.app.state.journal

    def boom(self, ctx):
        raise RuntimeError("asr fleet down")

    with monkeypatch.context() as m:
        m.setattr(MockAsrStage, "run_sync", boom)
        r = async_client.post("/ingest", json=c1)           # heal claim
        assert r.status_code == 202
        assert _wait(lambda: (j.done_row("aseam-hf") or {}).get("heal_attempts") == 1)

    row = j.done_row("aseam-hf")
    assert row["record_ids"] == [rid]                       # done never regressed
    assert j.counts() == {"pending": 0, "dead_letter": 0, "processed": 1}
    report = async_client.get("/continuity").json()
    assert all(s["dead_lettered"] == [] for s in report["streams"])
    # The claim was released: a later redelivery heals again (budget permitting).
    r2 = async_client.post("/ingest", json=c1)
    assert r2.status_code == 202
    assert _wait(lambda: len(fs.record_posts) == 2)         # healed this time
    assert fs.record_posts[1]["record_id"] == rid
