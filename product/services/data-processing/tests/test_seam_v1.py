"""WP-C3 — the rebuilt seam end-to-end: /ingest -> stage graph -> ONE C2 v1 POST.

Drives the real HTTP surface with a mock-dialect audio stage set (client-level
fakes registered in code — the dialect names them) against the MockTransport
fake storage: proves the one-record/one-POST path (L2/L6), the v1 record shape
on the wire, and the dedup fast path answering a redelivery without a second
POST. The full spine (T-1…T-6) lands at WP-C6; this is the seam's own smoke.
"""
from __future__ import annotations

import hashlib

import pytest

from app.stagegraph import stage as stage_mod
from app.stagegraph.stage import Backend, Stage, StageOutput

from .conftest import make_c1


class MockAsrStage(Stage):
    name = "asr"
    modality = "audio"
    stage_version = 1
    backend = Backend("mock", 1)
    byte_budget = 4096
    consumer = "speculative:test_fixture"

    def run_sync(self, ctx):
        # A client-level fake: canned output, deliberately DISTINCT from any
        # real-backend golden (the mock-dialect fixture rule).
        return StageOutput(value={
            "language": "zx",
            "value": "mock-dialect canned transcript",
        })


class MockAcousticStage(Stage):
    name = "acoustic"
    modality = "audio"
    stage_version = 1
    backend = Backend("mock", 1)
    required = False
    byte_budget = 1024
    consumer = "speculative:test_fixture"

    def run_sync(self, ctx):
        return StageOutput(value={"values": ["mock-tag"], "confidence": 0.5})


MOCK_PV = "acoustic.v1-mock.v1+asr.v1-mock.v1"


@pytest.fixture()
def mock_registry(monkeypatch):
    """A registry holding only the mock audio set; discovery disabled so real
    stage files (once they exist) cannot leak into the mock dialect."""
    monkeypatch.setattr(stage_mod, "_REGISTRY", {})
    monkeypatch.setattr(stage_mod, "_discovered", True)
    stage_mod.register_stage(MockAsrStage)
    stage_mod.register_stage(MockAcousticStage)
    return stage_mod._REGISTRY


def test_ingest_emits_exactly_one_v1_record(mock_registry, client):
    fs = client.fake_storage
    c1 = make_c1(fs)
    resp = client.post("/ingest", json=c1)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    expected_rid = hashlib.sha256(
        f"{c1['chunk_id']}\x00{MOCK_PV}".encode()).hexdigest()
    assert body == {"ok": True, "record_ids": [expected_rid]}

    # Exactly ONE POST to /context (L6), carrying the ONE record (L2).
    assert len(fs.record_posts) == 1
    record = fs.record_posts[0]
    assert record["contract"] == "C2" and record["version"] == "1"
    assert record["record_id"] == expected_rid
    assert record["modality"] == "audio"
    assert record["pipeline_version"] == MOCK_PV
    assert record["t_start"] == c1["t_start"] and record["t_end"] == c1["t_end"]
    assert set(record["content"]["slots"]) == {"asr", "acoustic"}
    assert record["content"]["slots"]["asr"] == {
        "version": "asr.v1-mock.v1",
        "language": "zx",
        "value": "mock-dialect canned transcript",
    }
    # Dead concepts never ride the wire.
    for dead in ("enrichments", "discriminator", "processed_at"):
        assert dead not in record


def test_redelivery_is_deduped_without_a_second_post(mock_registry, client):
    fs = client.fake_storage
    c1 = make_c1(fs, chunk_id="chunk-dedup-1")
    first = client.post("/ingest", json=c1)
    assert first.status_code == 200
    again = client.post("/ingest", json=c1)
    assert again.status_code == 200
    assert again.json() == first.json()
    assert len(fs.record_posts) == 1  # the redelivery never re-POSTed


def test_optional_failure_ships_record_with_hole(mock_registry, client,
                                                 monkeypatch):
    def boom(self, ctx):
        raise RuntimeError("acoustic down")

    monkeypatch.setattr(MockAcousticStage, "run_sync", boom)
    fs = client.fake_storage
    c1 = make_c1(fs, chunk_id="chunk-hole-1")
    resp = client.post("/ingest", json=c1)
    assert resp.status_code == 200
    record = fs.record_posts[0]
    # The dialect still names acoustic (attempted); the slot is absent (a hole).
    assert record["pipeline_version"] == MOCK_PV
    assert set(record["content"]["slots"]) == {"asr"}
