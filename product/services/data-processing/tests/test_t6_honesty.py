"""T-6 — honesty (L11): hole vs empty-claim vs not-attempted are distinguishable
from the record + its dialect ALONE. No journal peek, no logs, no side channel —
a consumer holding one record can always tell the three states apart:

 * stage named in pipeline_version + slot ABSENT = attempted and failed (hole)
 * slot PRESENT with an empty value = honest empty claim
 * stage name NOT in the dialect = never attempted

Plus the provenance corollary: a slot derived from another slot is one witness
on two channels — agreement is not corroboration (held as documentation on the
transcript slot; the executable part is that derivation is visible via version
segments in the one dialect string).
"""
from __future__ import annotations

import asyncio

from app.pipeline import build_c2
from app.stagegraph.executor import resolve, run_graph
from app.stagegraph.stage import Backend, Stage, StageOutput

C1 = {
    "chunk_id": "t6-c", "modality": "audio", "user_id": "u", "device_id": "d",
    "stream_id": "s", "blob_ref": "raw/x",
    "t_start": "2026-08-06T00:00:00Z", "t_end": "2026-08-06T00:00:05Z",
}


def _stage(name, *, value=None, required=False, run=None, slot=None):
    return type(f"S_{name}", (Stage,), {
        "name": name, "modality": "audio", "stage_version": 1,
        "backend": Backend("mock", 1), "slot": slot, "required": required,
        "byte_budget": 2048, "consumer": "speculative:test_fixture",
        "run_sync": run or (lambda self, ctx: StageOutput(value=value)),
    })()


def _record(stages):
    resolved = resolve("audio", stages)
    result = asyncio.run(run_graph(resolved, c1=C1, blob=b"", span_seconds=5.0,
                                   clients={}))
    return build_c2(dict(C1), result.slots, resolved.pipeline_version)


def _reader(record):
    """The L11 read, implemented exactly as a consumer would: from the record
 + dialect alone."""
    attempted = {seg.split(".")[0] for seg in record["pipeline_version"].split("+")}
    slots = record["content"]["slots"]

    def classify(stage_name, slot_name, empty_check):
        if stage_name not in attempted:
            return "never-attempted"
        if slot_name not in slots:
            return "hole"
        return "empty-claim" if empty_check(slots[slot_name]) else "value"

    return classify


def _boom(self, ctx):
    raise RuntimeError("down")


def test_three_states_distinguishable_from_record_plus_dialect():
    record = _record([
        _stage("asr", value={"value": "real words"}, required=True),
        _stage("ocr_like", value={"value": ""}),          # ran, screen was empty
        _stage("acoustic", run=_boom),                    # attempted, failed
        # translate: never registered -> never attempted, absent from dialect
    ])
    classify = _reader(record)
    assert classify("asr", "asr", lambda s: not s["value"]) == "value"
    assert classify("ocr_like", "ocr_like", lambda s: not s["value"]) == "empty-claim"
    assert classify("acoustic", "acoustic", lambda s: not s.get("values")) == "hole"
    assert classify("translate", "translation", lambda s: True) == "never-attempted"


def test_consumer_never_infers_a_negative_under_an_unattempting_dialect():
    """The record without acoustic in its dialect says NOTHING about acoustic
 events — 'never-attempted' is a distinct state, not evidence of silence."""
    record = _record([_stage("asr", value={"value": "words"}, required=True)])
    classify = _reader(record)
    assert classify("acoustic", "acoustic", lambda s: True) == "never-attempted"
    #...and that is a different answer than an attempted-and-empty claim:
    record2 = _record([
        _stage("asr", value={"value": "words"}, required=True),
        _stage("acoustic", value={"values": []}),
    ])
    classify2 = _reader(record2)
    assert classify2("acoustic", "acoustic", lambda s: not s["values"]) == "empty-claim"


def test_empty_slots_map_is_honest_under_an_all_optional_failure():
    record = _record([
        _stage("a", run=_boom),
        _stage("b", run=_boom),
    ])
    assert record["content"]["slots"] == {}
    # The dialect still states both attempts — the record is not "empty", it is
    # two holes.
    classify = _reader(record)
    assert classify("a", "a", lambda s: True) == "hole"
    assert classify("b", "b", lambda s: True) == "hole"


def test_cancelled_cone_reads_as_holes_too():
    """A cancelled downstream stage was attempted (its name is in the dialect —
 the dialect states the ATTEMPTED graph) and produced nothing: a hole, by the
 same read. The statuses ledger will additionally record
 failed-vs-cancelled; the record alone honestly says 'no slot'."""
    stages = [
        _stage("asr", value={"value": "w"}, required=True),
        _stage("gate", run=_boom),
        type("S_dep", (Stage,), {
            "name": "dep", "modality": "audio", "stage_version": 1,
            "backend": Backend("mock", 1), "needs": ("gate",), "required": False,
            "byte_budget": 2048, "consumer": "speculative:test_fixture",
            "run_sync": lambda self, ctx: StageOutput(value={"value": "never"}),
        })(),
    ]
    record = _record(stages)
    classify = _reader(record)
    assert classify("gate", "gate", lambda s: True) == "hole"
    assert classify("dep", "dep", lambda s: True) == "hole"
