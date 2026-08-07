"""T-2 — the one-record guard (L2): the executor cannot emit ≠ 1 record;
exactly one POST /context per completed graph attempt (L6); schema-hard.

Structural, not conventional: run_graph returns ONE GraphResult (there is no
list to be non-singular), process_chunk performs ONE POST, and the C2 v1
contract itself rejects the prior multi-record vocabulary (discriminator) — so
adding a second record per chunk requires forking the contract.
"""
from __future__ import annotations

import asyncio

from app import schemas
from app.stagegraph.executor import GraphResult, resolve, run_graph
from app.stagegraph.stage import Backend, Stage, StageOutput
from tests.conftest import make_c1

C1 = {
    "chunk_id": "t2-c", "modality": "audio", "user_id": "u", "device_id": "d",
    "stream_id": "s", "blob_ref": "raw/x",
    "t_start": "2026-08-06T00:00:00Z", "t_end": "2026-08-06T00:00:05Z",
}


def _stage(name, slot=None, value=None):
    return type(f"S_{name}", (Stage,), {
        "name": name, "modality": "audio", "stage_version": 1,
        "backend": Backend("mock", 1), "slot": slot, "byte_budget": 2048,
        "consumer": "speculative:test_fixture",
        "run_sync": lambda self, ctx: StageOutput(
            value=value if value is not None else {"value": name}),
    })()


def test_many_stages_still_one_result():
    """However many stages run, the graph yields ONE GraphResult — slots fan IN,
 records never fan out."""
    stages = [_stage("asr"), _stage("acoustic", value={"values": []}),
              _stage("diarize", slot="diarization", value={"splits": []})]
    result = asyncio.run(run_graph(resolve("audio", stages), c1=C1, blob=b"",
                                   span_seconds=5.0, clients={}))
    assert isinstance(result, GraphResult)
    assert len(result.slots) == 3  # three slots, one record


def test_exactly_one_post_per_chunk_through_the_service(client):
    fs = client.fake_storage
    client.post("/ingest", json=make_c1(fs, chunk_id="t2-one-post"))
    assert len(fs.record_posts) == 1
    assert len(fs.record_posts_raw) == 1


def test_contract_rejects_the_multi_record_vocabulary():
    """Schema-hard: a record carrying the prior discriminator (the multi-record
 marker) fails closed against C2 v1."""
    from app.pipeline import build_c2
    record = build_c2(dict(C1), {}, "asr.v1-mock.v1")
    record["discriminator"] = "keyframe-3"
    assert schemas.validate_c2(record) != []
