"""T-4 — the slot law (L5): one producer per slot; budgets enforced (never
truncation); binary-in-slot = red; no stage edits another stage's slot; arrays
inside slots that are keyframe-like key on the declared-span grid, never model
output (L12 — held here as the executable statement beside the law text).

The mechanics are proven at the executor level in test_executor.py; this file
owns the record-level statements.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app import schemas
from app.pipeline import build_c2
from app.stagegraph.executor import (
    GraphResolutionError,
    SlotEmitError,
    emitted_slot_bytes,
    resolve,
    run_graph,
)
from app.stagegraph.stage import Backend, Stage, StageOutput

C1 = {
    "chunk_id": "t4-c", "modality": "audio", "user_id": "u", "device_id": "d",
    "stream_id": "s", "blob_ref": "raw/x",
    "t_start": "2026-08-06T00:00:00Z", "t_end": "2026-08-06T00:00:05Z",
}


def _stage(name, *, slot=None, budget=2048, value=None, required=True, run=None):
    return type(f"S_{name}", (Stage,), {
        "name": name, "modality": "audio", "stage_version": 1,
        "backend": Backend("mock", 1), "slot": slot, "required": required,
        "byte_budget": budget, "consumer": "speculative:test_fixture",
        "run_sync": run or (lambda self, ctx: StageOutput(
            value=value if value is not None else {"value": name})),
    })()


def _run(stages):
    return asyncio.run(run_graph(resolve("audio", stages), c1=C1, blob=b"",
                                 span_seconds=5.0, clients={}))


def test_one_producer_per_slot_is_a_resolve_time_hard_error():
    with pytest.raises(GraphResolutionError, match="one enabled producer"):
        resolve("audio", [_stage("a"), _stage("b", slot="a")])


def test_no_stage_can_edit_anothers_slot():
    """Structural: a stage's output lands under ITS OWN slot name only — there
 is no API surface through which stage B can write slot A (a slot write proxy
 capability machinery was deleted WITH the capability)."""
    result = _run([
        _stage("asr", value={"value": "original"}),
        # 'edit' consumes asr but its output lands under 'edit', never over 'asr'.
        type("S_edit", (Stage,), {
            "name": "edit", "modality": "audio", "stage_version": 1,
            "backend": Backend("mock", 1), "needs": ("asr",), "byte_budget": 2048,
            "consumer": "speculative:test_fixture",
            "run_sync": lambda self, ctx: StageOutput(
                value={"value": ctx.inputs["asr"].value["value"] + " (derived)"}),
        })(),
    ])
    assert result.slots["asr"]["value"] == "original"
    assert result.slots["edit"]["value"] == "original (derived)"


def test_budget_is_enforced_never_truncated():
    fat_value = {"value": "y" * 5000}
    with pytest.raises(SlotEmitError, match="never a truncation"):
        _run([_stage("fat", budget=64, value=fat_value)])
    # And the failing path never emitted a shortened variant anywhere: an
    # optional breach leaves a HOLE, not a trimmed slot.
    result = _run([
        _stage("asr"),
        _stage("fat", budget=64, value=dict(fat_value), required=False),
    ])
    assert "fat" not in result.slots
    assert result.statuses["fat"] == "failed"


def test_budget_measure_is_the_emitted_utf8_json_bytes():
    emitted = {"version": "x.v1-mock.v1", "value": "héllo"}
    assert emitted_slot_bytes(emitted) == len(
        json.dumps(emitted, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def test_binary_in_slot_is_red():
    with pytest.raises(SlotEmitError, match="JSON"):
        _run([_stage("bin", value={"value": b"\xff\xd8jpeg-bytes"})])


def test_bytes_ride_the_blackboard_not_the_record():
    """L5's split: heavy payloads flow to consumers in-run and are freed; the
 record carries only JSON slots."""
    held = {}

    def producer(self, ctx):
        out = StageOutput(value={"value": ""}, bytes=b"\xff\xd8heavy")
        held["out"] = out
        return out

    result = _run([
        _stage("prep", run=producer),
        type("S_cons", (Stage,), {
            "name": "cons", "modality": "audio", "stage_version": 1,
            "backend": Backend("mock", 1), "needs": ("prep",), "byte_budget": 2048,
            "consumer": "speculative:test_fixture",
            "run_sync": lambda self, ctx: StageOutput(
                value={"value": str(len(ctx.inputs["prep"].bytes))}),
        })(),
    ])
    assert result.slots["cons"]["value"] == "7"        # consumer saw the bytes
    assert held["out"].bytes is None                   # freed after the run
    record = build_c2(dict(C1), result.slots, "prep.v1-mock.v1")
    assert b"heavy" not in json.dumps(record).encode()  # never on the record


def test_unknown_slot_fails_closed_at_the_contract():
    record = build_c2(dict(C1), {"sneaky": {"version": "s.v1-m.v1", "value": ""}},
                      "s.v1-m.v1")
    assert schemas.validate_c2(record) != []


def test_l12_grid_rule_is_stated():
    """L12 lives in the law + here (nothing is ported from vision/emit.py):
 arrays inside slots that are keyframe-like structures key their elements on
 a grid derived from the declared C1 span — never on model output, survivor
 ordinals, or decoder frame indices. splits[] are NOT that: they are sub-span
 timings that legitimately carry model-found boundaries as absolute times.
 No v1 slot carries a keyframe-like array today; the first producer that
 ships one must add its grid-keying test HERE."""
    # Executable placeholder: the v1 slot set carries no keyframe-like arrays.
    from app.models import C2SlotsV1
    assert set(C2SlotsV1.model_fields) == {
        "asr", "diarization", "transcript", "acoustic", "caption", "ocr"}
