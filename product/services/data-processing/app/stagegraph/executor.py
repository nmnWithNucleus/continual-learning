"""Graph resolution + the readiness executor (DP rebuild Stage C, Slot Law core).

RESOLUTION (cheap string work over a handful of stages, run BEFORE any stage) turns
an explicit stage set into the executable DAG, enforcing the graph-shaped law
loudly instead of skipping silently:

  * every declaration re-validated (explicit mock sets fail like bad drop-ins);
  * unique stage names; ONE producer per slot (L5, hard error);
  * every ``needs`` resolves inside the set; no cycles;
  * a required stage may not sit (transitively) downstream of an optional one —
    its "required" promise would be hollow under L7's cancelled-cone rule;
  * ``pipeline_version`` = the '+'-joined SORTED list of every stage's segment
    ``<stage>.v<S>-<backend>.v<B>[.exp-<code>]`` (L4) — composed here, before any
    stage runs, so the string states the ATTEMPTED dialect (L11).

EXECUTION is readiness-driven, not level-barriered: one task per stage inside an
``asyncio.TaskGroup``, each awaiting its needs' futures. ``run_sync`` stages
execute in worker threads (the event loop is structurally protected); ``run_async``
stages await natively (the thin-client path). A REQUIRED failure cancels AND
awaits every sibling before propagating the exact leaf exception (ProcessingError
preferred) — no record, the worker's retry taxonomy owns what happens next (L7).
An OPTIONAL failure records status ``failed``, leaves its slot a HOLE, and its
downstream cone resolves ``cancelled`` without running; the record ships.

SLOT EMISSION (the single point where a stage's value becomes record bytes):
the executor stamps the stage's segment as the slot's ``version`` key (a stage
cannot forge it), requires a JSON object, serializes canonically
(utf-8, no ascii escaping, compact separators) and enforces the stage file's
``byte_budget`` on the emitted bytes — exceeding it is a stage failure, NEVER
truncation (L5). Exactly one ``GraphResult`` leaves this module per run: the
slots map + per-stage statuses — the single-record assembly L2 rides on.

BLACKBOARD: each stage's ``StageOutput{value, bytes}`` lands keyed by stage name;
consumers (declared ``needs``) receive exactly their inputs. ``bytes`` — the
in-run transient payload (frames, decoded audio) — is freed the moment its last
consumer finishes (and unconditionally by end of run), so heavy artifacts never
outlive their consumers (L5: binary rides refs, never slots).
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from time import perf_counter
from types import MappingProxyType
from typing import Any, Mapping, Optional

from starlette.concurrency import run_in_threadpool

from ..ingest_core import ProcessingError
from .stage import Stage, StageContext, StageOutput, validate_stage

logger = logging.getLogger("data-processing.stagegraph")


class GraphResolutionError(Exception):
    """Config-shaped graph error (terminal: retrying the same code cannot help)."""


class SlotEmitError(RuntimeError):
    """A stage's slot emission broke the law (budget, shape, forged version) —
    a stage-file bug surfacing as a stage failure: required ⇒ the chunk attempt
    fails; optional ⇒ a hole. Never a truncation, never a silent fix-up."""


# Sentinel a failed-or-cancelled stage's future resolves to; dependents cascade.
HOLE = object()


@dataclass
class ResolvedGraph:
    modality: str
    stages: list[Stage]                  # name-sorted
    by_name: dict[str, Stage]
    consumers: dict[str, int]            # stage name -> number of dependents
    pipeline_version: str


@dataclass
class GraphResult:
    """One chunk's single-record payload: the slots map (each value stamped with
    its producer's segment) + the per-stage statuses (L8's vocabulary)."""

    slots: dict[str, Any]
    statuses: dict[str, str]             # stage name -> ok | failed | cancelled
    pipeline_version: str = ""


def resolve(modality: str, stages: list[Stage]) -> ResolvedGraph:
    if not stages:
        raise GraphResolutionError(f"no stages for modality {modality!r}")

    by_name: dict[str, Stage] = {}
    slots_seen: dict[str, str] = {}
    for s in stages:
        validate_stage(s)
        if s.modality != modality:
            raise GraphResolutionError(
                f"stage {s.name!r} declares modality {s.modality!r}, resolving "
                f"{modality!r} — one graph per modality"
            )
        if s.name in by_name:
            raise GraphResolutionError(f"{modality}: duplicate stage name {s.name!r}")
        owner = slots_seen.get(s.slot_name)
        if owner is not None:
            raise GraphResolutionError(
                f"{modality}: slot {s.slot_name!r} has two producers "
                f"({owner!r} and {s.name!r}) — one enabled producer per slot (L5)"
            )
        by_name[s.name] = s
        slots_seen[s.slot_name] = s.name

    for s in by_name.values():
        for need in s.needs:
            if need not in by_name:
                raise GraphResolutionError(
                    f"{modality}/{s.name}: needs unknown stage {need!r}"
                )

    # Cycle check (DFS; also proves the DAG is executable).
    seen: dict[str, int] = {}  # 0=visiting, 1=done

    def _visit(name: str, trail: tuple[str, ...]) -> None:
        state = seen.get(name)
        if state == 1:
            return
        if state == 0:
            raise GraphResolutionError(
                f"{modality}: stage dependency cycle {' -> '.join((*trail, name))}"
            )
        seen[name] = 0
        for dep in by_name[name].needs:
            _visit(dep, (*trail, name))
        seen[name] = 1

    for name in by_name:
        _visit(name, ())

    # L7 shape rule: nothing required may depend (transitively) on an optional
    # stage — an optional failure cancels its cone, and a cancelled required
    # stage would mean "no record", making the optional stage required in fact.
    dependents: dict[str, list[str]] = {n: [] for n in by_name}
    for s in by_name.values():
        for dep in s.needs:
            dependents[dep].append(s.name)
    for s in by_name.values():
        if s.required:
            continue
        frontier = list(dependents[s.name])
        while frontier:
            d = frontier.pop()
            if by_name[d].required:
                raise GraphResolutionError(
                    f"{modality}/{d} is required but sits downstream of optional "
                    f"stage {s.name!r} — its promise would be hollow (L7)"
                )
            frontier.extend(dependents[d])

    ordered = sorted(by_name.values(), key=lambda s: s.name)
    return ResolvedGraph(
        modality=modality,
        stages=ordered,
        by_name=by_name,
        consumers={n: len(dependents[n]) for n in by_name},
        # L4: the sorted '+'-join, composed pre-run — the attempted dialect.
        pipeline_version="+".join(sorted(s.segment for s in ordered)),
    )


# ---------------------------------------------------------------------------
# Slot emission (L5) — the one place a stage's value becomes record bytes
# ---------------------------------------------------------------------------

def emitted_slot_bytes(emitted: dict) -> int:
    """The budget measure: len(utf8(json(emitted slot))) under the canonical
    serialization (no ascii escaping, compact separators)."""
    return len(json.dumps(emitted, ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8"))


def _emit_slot(stage: Stage, value: Any) -> Optional[dict]:
    if value is None:
        return None  # this stage emits no record slot (transient-output stage)
    if not isinstance(value, dict):
        raise SlotEmitError(
            f"{stage.modality}/{stage.name}: slot value must be a JSON object, "
            f"got {type(value).__name__} — every v1 slot is an object under the "
            "contract's typed sub-schemas"
        )
    if "version" in value:
        raise SlotEmitError(
            f"{stage.modality}/{stage.name}: slot value carries its own 'version' "
            "key — the executor stamps the producing stage's segment; a stage "
            "cannot forge (or drift from) its own identity"
        )
    emitted = {"version": stage.segment, **value}
    try:
        size = emitted_slot_bytes(emitted)
    except (TypeError, ValueError) as exc:
        raise SlotEmitError(
            f"{stage.modality}/{stage.name}: slot value is not JSON-serializable "
            f"({exc}) — binary rides refs, never slots (L5)"
        ) from None
    if size > stage.byte_budget:
        raise SlotEmitError(
            f"{stage.modality}/{stage.name}: emitted slot is {size} bytes, over the "
            f"declared byte_budget {stage.byte_budget} — a budget breach is a stage "
            "failure, never a truncation (L5); raising the budget is a vS bump"
        )
    return emitted


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def _flatten_exceptions(eg: BaseException) -> list[BaseException]:
    """Depth-first leaves of a (possibly nested) ExceptionGroup."""
    if isinstance(eg, BaseExceptionGroup):
        out: list[BaseException] = []
        for exc in eg.exceptions:
            out.extend(_flatten_exceptions(exc))
        return out
    return [eg]


def _observe(metrics, name: str, value: float, labels: dict) -> None:
    if metrics is not None:
        try:
            metrics.observe(name, value, labels)
        except Exception:  # metrics must never fail a chunk
            logger.exception("metrics observe failed for %s", name)


def _count(metrics, name: str, labels: dict) -> None:
    if metrics is not None:
        try:
            metrics.inc(name, labels)
        except Exception:
            logger.exception("metrics inc failed for %s", name)


async def run_graph(
    resolved: ResolvedGraph,
    *,
    c1: Mapping[str, Any],
    blob: bytes,
    span_seconds: float,
    clients: Optional[Mapping[str, Any]] = None,
    metrics: Any = None,
) -> GraphResult:
    """Run one chunk through the resolved graph. Returns exactly ONE GraphResult
    (the single-record assembly L2 rides on) or raises the leaf exception of a
    required-stage failure (no record — L7)."""
    loop = asyncio.get_running_loop()
    futures: dict[str, asyncio.Future] = {s.name: loop.create_future()
                                          for s in resolved.stages}
    blackboard: dict[str, StageOutput] = {}
    statuses: dict[str, str] = {}
    slots: dict[str, Any] = {}
    remaining = dict(resolved.consumers)
    c1_view = MappingProxyType(dict(c1))

    def _release_inputs(stage: Stage) -> None:
        """One consumer (ran, failed or cancelled) is done with its inputs; free
        a producer's transient bytes the moment its last consumer finishes."""
        for need in stage.needs:
            remaining[need] -= 1
            if remaining[need] == 0 and need in blackboard:
                blackboard[need].bytes = None

    async def _run_stage(stage: Stage) -> None:
        fut = futures[stage.name]
        try:
            deps = [await futures[n] for n in stage.needs]
        except asyncio.CancelledError:
            if not fut.done():
                fut.cancel()
            raise
        if any(d is HOLE for d in deps):
            # L7: the failed optional's downstream cone is cancelled, recorded.
            statuses[stage.name] = "cancelled"
            _count(metrics, "dp_graph_stage_failures_total",
                   {"modality": resolved.modality, "stage": stage.name,
                    "reason": "cancelled"})
            fut.set_result(HOLE)
            _release_inputs(stage)
            return
        ctx = StageContext(
            c1=c1_view, blob=blob, span_seconds=span_seconds,
            inputs={n: blackboard[n] for n in stage.needs},
            clients=clients or {}, metrics=metrics,
        )
        t0 = perf_counter()
        try:
            if type(stage)._defines("run_async"):
                out = await stage.run_async(ctx)
            else:
                out = await run_in_threadpool(stage.run_sync, ctx)
            if not isinstance(out, StageOutput):
                raise SlotEmitError(
                    f"{resolved.modality}/{stage.name}: run must return a "
                    f"StageOutput, got {type(out).__name__}"
                )
            emitted = _emit_slot(stage, out.value)
        except asyncio.CancelledError:
            if not fut.done():
                fut.cancel()
            raise
        except Exception as exc:
            _observe(metrics, "dp_graph_stage_seconds", perf_counter() - t0,
                     {"modality": resolved.modality, "stage": stage.name})
            statuses[stage.name] = "failed"
            _count(metrics, "dp_graph_stage_failures_total",
                   {"modality": resolved.modality, "stage": stage.name,
                    "reason": "failed"})
            if stage.required:
                if not fut.done():
                    fut.cancel()
                raise  # TaskGroup cancels + awaits all siblings, then propagates
            logger.warning("optional stage %s/%s failed (hole): %s",
                           resolved.modality, stage.name, exc)
            fut.set_result(HOLE)
            _release_inputs(stage)
            return
        _observe(metrics, "dp_graph_stage_seconds", perf_counter() - t0,
                 {"modality": resolved.modality, "stage": stage.name})
        # Commit-on-success: value + bytes land only now, before dependents wake.
        statuses[stage.name] = "ok"
        blackboard[stage.name] = out
        if emitted is not None:
            slots[stage.slot_name] = emitted
        if remaining[stage.name] == 0:
            out.bytes = None  # no consumers — free the transient payload now
        fut.set_result(out)
        _release_inputs(stage)

    try:
        async with asyncio.TaskGroup() as tg:
            for stage in resolved.stages:
                tg.create_task(_run_stage(stage))
    except BaseExceptionGroup as eg:
        # A required stage failed: TaskGroup cancelled + awaited every sibling.
        # Re-raise the ACTUAL leaf (ProcessingError preferred) so the worker
        # taxonomy + the HTTP mapping see the exact exception, not a wrapper.
        leaves = _flatten_exceptions(eg)
        real = [e for e in leaves if not isinstance(e, asyncio.CancelledError)]
        preferred = next((e for e in real if isinstance(e, ProcessingError)), None)
        raise (preferred or (real[0] if real else leaves[0]))
    finally:
        # End of run = every consumer is done: transient payloads never outlive
        # the run, whatever path ended it.
        for out in blackboard.values():
            out.bytes = None

    return GraphResult(slots=slots, statuses=statuses,
                       pipeline_version=resolved.pipeline_version)
