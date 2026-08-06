"""WP-C2 — graph resolution + the readiness executor under the Slot Law.

KEPT from the v0 executor: readiness TaskGroup (independent stages overlap),
commit-on-success, cancel-and-await-siblings on required failure, leaf re-raise
(the exact exception, ProcessingError preferred), threadpooled run_sync.

NEW: sorted '+'-joined version composition resolved pre-run; one-producer-per-slot
and required-never-downstream-of-optional resolve checks; single-record assembly
(slots map, each slot stamped with its stage's segment); byte-budget enforcement
at slot emission (exceed = stage failure, never truncation); blackboard values as
{value, bytes} with bytes freed after the last consumer; L7 hole + cancelled-cone
semantics with statuses recorded.
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from app.ingest_core import ProcessingError
from app.stagegraph.executor import (
    GraphResolutionError,
    GraphResult,
    SlotEmitError,
    resolve,
    run_graph,
)
from app.stagegraph.stage import Backend, Stage, StageContext, StageOutput

C1 = {
    "chunk_id": "chunk-1", "modality": "audio", "user_id": "u",
    "device_id": "d", "stream_id": "s", "blob_ref": "raw/x",
    "t_start": "2026-08-06T00:00:00Z", "t_end": "2026-08-06T00:00:06Z",
}


def make_stage(name, *, needs=(), slot=None, required=True, budget=4096,
               backend=None, run=None, run_async=None, modality="audio",
               version=1, server=""):
    """Build a one-off Stage subclass around a run callable."""
    attrs = {
        "name": name, "modality": modality, "stage_version": version,
        "backend": backend or Backend("mock", 1), "needs": tuple(needs),
        "slot": slot, "required": required, "byte_budget": budget,
        "server": server,
    }
    if run_async is not None:
        attrs["run_async"] = run_async
    else:
        attrs["run_sync"] = run or (lambda self, ctx: StageOutput(value={"value": name}))
    return type(f"S_{name}", (Stage,), attrs)()


def execute(stages, *, c1=C1, blob=b"bytes", span=6.0, clients=None):
    resolved = resolve(c1["modality"], stages)
    return asyncio.run(run_graph(resolved, c1=c1, blob=blob, span_seconds=span,
                                 clients=clients or {}))


# ---------------------------------------------------------------------------
# Resolution: version composition + graph-shape checks
# ---------------------------------------------------------------------------

def test_pipeline_version_is_sorted_plus_joined_segments():
    stages = [
        make_stage("diarize", slot="diarization", backend=Backend("pyannote", 1)),
        make_stage("asr", backend=Backend("fw", 3), version=2),
        make_stage("acoustic", backend=Backend("ast", 1)),
    ]
    resolved = resolve("audio", stages)
    assert resolved.pipeline_version == (
        "acoustic.v1-ast.v1+asr.v2-fw.v3+diarize.v1-pyannote.v1"
    )


def test_experiment_code_rides_the_composed_string():
    stages = [make_stage("asr")]
    stages[0].experiment = "b7"
    assert resolve("audio", stages).pipeline_version == "asr.v1-mock.v1.exp-b7"


def test_empty_stage_set_is_resolution_error():
    with pytest.raises(GraphResolutionError, match="no stages"):
        resolve("audio", [])


def test_duplicate_slot_is_resolution_error():
    stages = [make_stage("a"), make_stage("b", slot="a")]
    with pytest.raises(GraphResolutionError, match="slot"):
        resolve("audio", stages)


def test_duplicate_name_is_resolution_error():
    with pytest.raises(GraphResolutionError, match="duplicate"):
        resolve("audio", [make_stage("a"), make_stage("a", slot="a2")])


def test_unknown_need_is_resolution_error():
    with pytest.raises(GraphResolutionError, match="unknown"):
        resolve("audio", [make_stage("a", needs=("ghost",))])


def test_cycle_is_resolution_error():
    stages = [make_stage("a", needs=("b",)), make_stage("b", needs=("a",))]
    with pytest.raises(GraphResolutionError, match="cycle"):
        resolve("audio", stages)


def test_wrong_modality_stage_is_resolution_error():
    with pytest.raises(GraphResolutionError, match="modality"):
        resolve("audio", [make_stage("a", modality="video")])


def test_required_needing_optional_is_resolution_error():
    stages = [
        make_stage("opt", required=False),
        make_stage("req", needs=("opt",), required=True),
    ]
    with pytest.raises(GraphResolutionError, match="optional"):
        resolve("audio", stages)


def test_required_transitively_downstream_of_optional_is_resolution_error():
    stages = [
        make_stage("opt", required=False),
        make_stage("mid", needs=("opt",), required=False),
        make_stage("req", needs=("mid",), required=True),
    ]
    with pytest.raises(GraphResolutionError, match="optional"):
        resolve("audio", stages)


# ---------------------------------------------------------------------------
# Execution: readiness, commit-on-success, failure semantics
# ---------------------------------------------------------------------------

def test_single_result_with_version_stamped_slots():
    result = execute([
        make_stage("asr", run=lambda self, ctx: StageOutput(value={"value": "text"})),
        make_stage("acoustic", backend=Backend("ast", 2),
                   run=lambda self, ctx: StageOutput(value={"values": ["tap"]})),
    ])
    assert isinstance(result, GraphResult)
    assert result.slots == {
        "asr": {"version": "asr.v1-mock.v1", "value": "text"},
        "acoustic": {"version": "acoustic.v1-ast.v2", "values": ["tap"]},
    }
    assert result.statuses == {"asr": "ok", "acoustic": "ok"}


def test_value_none_emits_no_slot_but_status_ok():
    result = execute([
        make_stage("clipprep", run=lambda self, ctx: StageOutput(bytes=b"frames")),
        make_stage("asr"),
    ])
    assert set(result.slots) == {"asr"}
    assert result.statuses["clipprep"] == "ok"


def test_independent_stages_run_concurrently():
    """Readiness scheduling: two 0.15s sleeps overlap (well under 0.3s total)."""
    def slow(self, ctx):
        time.sleep(0.15)
        return StageOutput(value={"value": self.name})

    t0 = time.perf_counter()
    result = execute([make_stage("a", run=slow), make_stage("b", run=slow)])
    elapsed = time.perf_counter() - t0
    assert set(result.slots) == {"a", "b"}
    assert elapsed < 0.28, f"stages serialized: {elapsed:.2f}s"


def test_needs_delivers_inputs_and_only_declared_inputs():
    seen = {}

    def consumer(self, ctx):
        seen.update(dict(ctx.inputs))
        return StageOutput(value={"value": ctx.inputs["prep"].value["value"] + "!"})

    result = execute([
        make_stage("prep", run=lambda self, ctx: StageOutput(value={"value": "p"})),
        make_stage("other", run=lambda self, ctx: StageOutput(value={"value": "o"})),
        make_stage("cons", needs=("prep",), run=consumer),
    ])
    assert list(seen) == ["prep"]
    assert result.slots["cons"]["value"] == "p!"


def test_required_failure_surfaces_the_leaf_exception():
    def boom(self, ctx):
        raise ValueError("the leaf")

    with pytest.raises(ValueError, match="the leaf"):
        execute([make_stage("a", run=boom), make_stage("b")])


def test_required_processingerror_is_preferred():
    def boom_pe(self, ctx):
        raise ProcessingError({"error": "pe"}, http_status=502, transient=True)

    def boom_rt(self, ctx):
        raise RuntimeError("other")

    with pytest.raises(ProcessingError):
        execute([make_stage("a", run=boom_pe), make_stage("b", run=boom_rt)])


def test_required_failure_cancels_and_awaits_siblings():
    events = []

    def boom(self, ctx):
        raise RuntimeError("down")

    async def slow_async(self, ctx):
        events.append("start")
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            events.append("cancelled")
            raise
        return StageOutput()  # pragma: no cover

    async def _main():
        resolved = resolve("audio", [
            make_stage("boom", run=boom),
            make_stage("slow", run_async=slow_async),
        ])
        with pytest.raises(RuntimeError, match="down"):
            await run_graph(resolved, c1=C1, blob=b"", span_seconds=1.0, clients={})

    t0 = time.perf_counter()
    asyncio.run(_main())
    assert time.perf_counter() - t0 < 2.0  # never waited the 5s
    assert events == ["start", "cancelled"]


def test_optional_failure_is_a_hole_with_cancelled_cone():
    def boom(self, ctx):
        raise RuntimeError("opt down")

    ran = []

    def track(name):
        def _run(self, ctx):
            ran.append(name)
            return StageOutput(value={"value": name})
        return _run

    result = execute([
        make_stage("asr", run=track("asr")),
        make_stage("opt", required=False, run=boom),
        make_stage("dep", required=False, needs=("opt",), run=track("dep")),
        make_stage("dep2", required=False, needs=("dep",), run=track("dep2")),
    ])
    assert result.statuses == {
        "asr": "ok", "opt": "failed", "dep": "cancelled", "dep2": "cancelled",
    }
    assert set(result.slots) == {"asr"}          # holes: opt, dep, dep2 absent
    assert ran == ["asr"]                        # the cone never ran


def test_commit_on_success_only():
    """A failed optional stage's value never reaches the slots map."""
    def boom(self, ctx):
        return StageOutput(value={"value": "will fail downstream of emit"})

    # Fail AFTER constructing output: budget breach makes emission itself fail.
    result = execute([
        make_stage("asr"),
        make_stage("fat", required=False, budget=4,
                   run=lambda self, ctx: StageOutput(value={"value": "x" * 100})),
    ])
    assert set(result.slots) == {"asr"}
    assert result.statuses["fat"] == "failed"


# ---------------------------------------------------------------------------
# Slot emission law: budgets, shape, version stamping (L5)
# ---------------------------------------------------------------------------

def test_budget_breach_on_required_stage_fails_the_chunk():
    with pytest.raises(SlotEmitError, match="byte_budget"):
        execute([make_stage("fat", budget=8,
                            run=lambda self, ctx: StageOutput(value={"value": "y" * 100}))])


def test_budget_counts_the_emitted_slot_utf8_bytes():
    """The measure is len(utf8(json(emitted slot incl. version))) — a value that
    fits exactly passes, one byte more fails."""
    value = {"value": "abc"}
    emitted = {"version": "fit.v1-mock.v1", **value}
    exact = len(json.dumps(emitted, ensure_ascii=False,
                           separators=(",", ":")).encode("utf-8"))
    result = execute([make_stage("fit", budget=exact,
                                 run=lambda self, ctx: StageOutput(value=dict(value)))])
    assert result.slots["fit"]["value"] == "abc"
    with pytest.raises(SlotEmitError, match="byte_budget"):
        execute([make_stage("fit", budget=exact - 1,
                            run=lambda self, ctx: StageOutput(value=dict(value)))])


def test_binary_in_slot_value_is_loud_failure():
    with pytest.raises(SlotEmitError, match="JSON"):
        execute([make_stage("bin",
                            run=lambda self, ctx: StageOutput(value={"value": b"\x00"}))])


def test_non_dict_slot_value_is_loud_failure():
    with pytest.raises(SlotEmitError, match="object"):
        execute([make_stage("s", run=lambda self, ctx: StageOutput(value="bare string"))])


def test_stage_supplied_version_key_is_loud_failure():
    with pytest.raises(SlotEmitError, match="version"):
        execute([make_stage("s",
                            run=lambda self, ctx: StageOutput(
                                value={"version": "forged", "value": "x"}))])


def test_wrong_return_type_is_loud_failure():
    with pytest.raises(SlotEmitError, match="StageOutput"):
        execute([make_stage("s", run=lambda self, ctx: {"value": "raw dict"})])


# ---------------------------------------------------------------------------
# Blackboard bytes: delivered to consumers, freed after the last one (L5)
# ---------------------------------------------------------------------------

def test_bytes_delivered_to_consumer_and_freed_after_run():
    held = {}

    def producer(self, ctx):
        out = StageOutput(value={"value": "p"}, bytes=b"heavy-frames")
        held["out"] = out
        return out

    saw = {}

    def consumer(self, ctx):
        saw["bytes"] = ctx.inputs["prep"].bytes
        return StageOutput(value={"value": "c"})

    result = execute([
        make_stage("prep", run=producer),
        make_stage("cons", needs=("prep",), run=consumer),
    ])
    assert saw["bytes"] == b"heavy-frames"      # consumer saw the payload
    assert held["out"].bytes is None            # freed after the last consumer
    assert result.slots["prep"]["value"] == "p"  # the slot value survives


def test_bytes_freed_even_when_consumer_cone_is_cancelled():
    held = {}

    def producer(self, ctx):
        out = StageOutput(value={"value": "p"}, bytes=b"payload")
        held["out"] = out
        return out

    def boom(self, ctx):
        raise RuntimeError("opt fail")

    result = execute([
        make_stage("prep", required=False, run=producer),
        make_stage("gate", required=False, run=boom),
        make_stage("cons", required=False, needs=("prep", "gate"),
                   run=lambda self, ctx: StageOutput(value={"value": "c"})),
    ])
    assert result.statuses == {"prep": "ok", "gate": "failed", "cons": "cancelled"}
    assert held["out"].bytes is None
