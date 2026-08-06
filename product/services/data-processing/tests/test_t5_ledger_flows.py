"""T-5 — ledger flows, the Stage C subset (L7/L8 on the v0-shaped ledger).

Ledger semantics stay v0-shaped through Stage C (done-row extension, claim
tree, heal budgets and the crash-table replay are Stage D — WP-D1..D3 build the
full T-5). What already holds NOW and is pinned here:

  * redelivery of a done chunk under the SAME dialect -> skip (200 + record_id,
    no reprocess, no second POST) — L8 case 3;
  * redelivery under a CHANGED dialect -> version-forward reprocess; the new
    record lands BESIDE the old (different record_id, both in storage) — L8
    case 2;
  * required failure -> no record, the attempt fails (worker retry taxonomy) —
    L7's first half;
  * optional failure -> hole, statuses recorded, record ships — L7's second
    half (the heal that FILLS the hole is Stage D).
"""
from __future__ import annotations

import pytest

from app.stagegraph import stage as stage_mod
from app.stagegraph.stage import Backend, Stage, StageOutput
from tests.conftest import MOCK_AUDIO_PV, MockAsrStage, make_c1


def test_same_dialect_redelivery_skips(client):
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

    def run_sync(self, ctx):
        raise RuntimeError("optional stage down")


@pytest.fixture()
def mock_registry(monkeypatch):
    from tests.conftest import install_mock_audio_registry
    reg = install_mock_audio_registry(monkeypatch)
    stage_mod.register_stage(_OptionalBoom)
    return reg


def test_optional_failure_ships_holey_record(client):
    """L7's second half. The heal that later FILLS this hole (full-graph re-run,
    same record_id, upsert replaces holey with fuller) is Stage D's — noted so
    the reader does not look for it here."""
    fs = client.fake_storage
    resp = client.post("/ingest", json=make_c1(fs, chunk_id="t5-hole"))
    assert resp.status_code == 200
    (record,) = fs.record_posts
    assert "acoustic" in record["pipeline_version"]      # attempted (in dialect)
    assert "acoustic" not in record["content"]["slots"]  # failed (a hole)
    assert "asr" in record["content"]["slots"]           # the record still ships
