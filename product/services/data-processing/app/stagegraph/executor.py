"""Graph resolution + the readiness executor.

RESOLUTION (per call — cheap string work over a handful of stages) turns the registered
stage set + the active settings into the enabled DAG, enforcing the config-shaped rules
loudly instead of skipping silently:

  * exactly one enabled primary, with a non-empty base fragment;
  * an enabled REQUIRED stage needing a disabled stage is an error (your config asked for
    something that cannot run); an enabled best_effort stage needing a disabled one
    auto-disables with a log — the documented, observable degradation;
  * no required/primary stage may sit (transitively) downstream of a best_effort stage —
    its "required" promise would be hollow;
  * mutate stages implicitly depend on the primary (you cannot mutate slots that don't
    exist yet);
  * ``pipeline_version = primary_fragment + ''.join(sorted(other enabled fragments))`` —
    reduces exactly to the shipped dialects (``asr-mock-v0``, ``asr-mock-v0+diar-mock-v1``,
    ``vidproc-vlm-v0``), and every future mutating stage forks it automatically.

EXECUTION is readiness-driven, not level-barriered: one task per enabled stage inside an
``asyncio.TaskGroup``, each awaiting its needs' futures — a slow sidecar never gates an
independent stage. ``run_sync`` stages execute in worker threads (the event loop is
structurally protected); a required failure cancels AND awaits every sibling before
propagating (no stage task from attempt N survives into attempt N+1 — the worker's
retry loop can never overlap attempts); a best_effort failure resolves its future to
``SKIPPED`` and its (necessarily best_effort) dependents cascade, counted per stage.

Assembly is LAST and deterministic regardless of completion order: the primary's
``assemble(ctx)`` emits the primary units, then sidecar units append sorted by
``(order, name)``. Two runtime guards back the static rules: a mutable-slots hash taken
when the primary+mutate cohort finishes must be unchanged after all stages (a sidecar
mutating the primary is a bug, not a feature), and discriminators must be unique
(colliding record identities would silently upsert over each other).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Optional

from starlette.concurrency import run_in_threadpool

from ..ingest_core import ProcessingError
from ..processing.base import ProcessedUnit
from .stage import SKIPPED, Stage, StageContext, StageResult


def _flatten_exceptions(eg: BaseException) -> list[BaseException]:
    """Depth-first leaves of a (possibly nested) ExceptionGroup."""
    if isinstance(eg, BaseExceptionGroup):
        out: list[BaseException] = []
        for exc in eg.exceptions:
            out.extend(_flatten_exceptions(exc))
        return out
    return [eg]

logger = logging.getLogger("data-processing.stagegraph")


class GraphResolutionError(Exception):
    """Config-shaped graph error (terminal: retrying the same config cannot help)."""


@dataclass
class ResolvedGraph:
    modality: str
    primary: Stage
    enabled: list[Stage]              # declared-order
    needs: dict[str, tuple[str, ...]]  # effective needs (incl. implicit primary for mutate)
    pipeline_version: str


def resolve(modality: str, stages: list[Stage], settings) -> ResolvedGraph:
    if not stages:
        raise GraphResolutionError(f"no stages registered for modality {modality!r}")

    enabled = {s.name: s for s in stages if s.is_enabled(settings)}

    # Auto-disable best_effort stages whose needs are disabled (fixpoint); a REQUIRED
    # stage in the same position is a hard error.
    changed = True
    while changed:
        changed = False
        for s in list(enabled.values()):
            missing = [n for n in s.needs if n not in enabled]
            if not missing:
                continue
            if s.policy == "best_effort":
                logger.warning("stage %s/%s auto-disabled: needs disabled stage(s) %s",
                               modality, s.name, missing)
                del enabled[s.name]
                changed = True
            else:
                raise GraphResolutionError(
                    f"{modality}/{s.name} is enabled+required but needs disabled "
                    f"stage(s) {missing} — fix the config, don't ship silent holes"
                )

    primaries = [s for s in enabled.values() if s.kind == "primary"]
    if len(primaries) != 1:
        raise GraphResolutionError(
            f"{modality}: exactly one enabled primary stage required, found "
            f"{[s.name for s in primaries]}"
        )
    primary = primaries[0]
    base = primary.version_fragment(settings)
    if not base:
        raise GraphResolutionError(
            f"{modality}/{primary.name}: an enabled primary must have a non-empty "
            "version fragment (the base dialect)"
        )

    # Effective needs: mutate stages implicitly depend on the primary.
    needs: dict[str, tuple[str, ...]] = {}
    for s in enabled.values():
        n = tuple(s.needs)
        if s.kind == "mutate" and primary.name not in n:
            n = (primary.name, *n)
        needs[s.name] = n

    # Cycle check (also validates the DAG is executable).
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
        for dep in needs[name]:
            _visit(dep, (*trail, name))
        seen[name] = 1

    for name in needs:
        _visit(name, ())

    # best_effort cone: nothing required/primary may depend (transitively) on best_effort.
    dependents: dict[str, list[str]] = {n: [] for n in enabled}
    for name, deps in needs.items():
        for dep in deps:
            dependents[dep].append(name)
    for s in enabled.values():
        if s.policy != "best_effort":
            continue
        frontier = list(dependents[s.name])
        while frontier:
            d = frontier.pop()
            ds = enabled[d]
            if ds.policy != "best_effort":
                raise GraphResolutionError(
                    f"{modality}/{d} is {ds.kind}/required but sits downstream of "
                    f"best_effort stage {s.name!r} — its promise would be hollow"
                )
            frontier.extend(dependents[d])

    fragments = sorted(
        f for s in enabled.values() if s is not primary
        for f in [s.version_fragment(settings)] if f
    )
    return ResolvedGraph(
        modality=modality,
        primary=primary,
        enabled=sorted(enabled.values(), key=lambda s: s.order),
        needs=needs,
        pipeline_version=base + "".join(fragments),
    )


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


def _mutables_fingerprint(ctx: StageContext, primary: Stage) -> Optional[str]:
    if not primary.mutable_slots:
        return None
    return repr(tuple(ctx.slots.get(k) for k in primary.mutable_slots))


async def run_graph(resolved: ResolvedGraph, ctx: StageContext) -> list[ProcessedUnit]:
    metrics = getattr(ctx.resources, "metrics", None)
    loop = asyncio.get_running_loop()
    futures: dict[str, asyncio.Future] = {s.name: loop.create_future() for s in resolved.enabled}
    sidecar_units: dict[str, list[ProcessedUnit]] = {}
    fingerprint: dict[str, Optional[str]] = {"at_mutate_done": None}

    async def _run_stage(stage: Stage) -> None:
        fut = futures[stage.name]
        try:
            deps = [await futures[n] for n in resolved.needs[stage.name]]
        except asyncio.CancelledError:
            if not fut.done():
                fut.cancel()
            raise
        if any(d is SKIPPED for d in deps):
            _count(metrics, "dp_graph_stage_failures_total",
                   {"modality": resolved.modality, "stage": stage.name, "reason": "skipped"})
            fut.set_result(SKIPPED)
            return
        t0 = perf_counter()
        try:
            if type(stage)._defines("run_async"):
                result = await stage.run_async(ctx)
            else:
                result = await run_in_threadpool(stage.run_sync, ctx)
        except asyncio.CancelledError:
            if not fut.done():
                fut.cancel()
            raise
        except Exception as exc:
            _observe(metrics, "dp_graph_stage_seconds", perf_counter() - t0,
                     {"modality": resolved.modality, "stage": stage.name})
            if stage.policy == "best_effort":
                _count(metrics, "dp_graph_stage_failures_total",
                       {"modality": resolved.modality, "stage": stage.name, "reason": "failed"})
                logger.warning("best_effort stage %s/%s failed (skipped): %s",
                               resolved.modality, stage.name, exc)
                fut.set_result(SKIPPED)
                return
            if not fut.done():
                fut.cancel()
            raise  # required: TaskGroup cancels + awaits all siblings, then propagates
        _observe(metrics, "dp_graph_stage_seconds", perf_counter() - t0,
                 {"modality": resolved.modality, "stage": stage.name})
        result = result if isinstance(result, StageResult) else StageResult()
        # Commit-on-success: a failed stage contributed nothing; a succeeded one merges
        # its slots atomically-enough (single-threaded loop) before dependents wake.
        ctx.slots.update(result.slots)
        if result.units:
            sidecar_units[stage.name] = result.units
        fut.set_result(True)

    async def _fingerprint_watch() -> None:
        """Snapshot the primary's mutable slots the moment the primary+mutate cohort is
        done — the reference the post-run guard compares against (a sidecar mutating
        primary state after that point is a caught bug)."""
        cohort = [futures[s.name] for s in resolved.enabled if s.kind in ("primary", "mutate")]
        try:
            await asyncio.gather(*cohort)
        except (asyncio.CancelledError, Exception):
            return  # a failing cohort fails the chunk anyway; no snapshot needed
        fingerprint["at_mutate_done"] = _mutables_fingerprint(ctx, resolved.primary)

    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(_fingerprint_watch())
            for stage in resolved.enabled:
                tg.create_task(_run_stage(stage))
    except BaseExceptionGroup as eg:
        # A required stage failed: TaskGroup cancelled + awaited every sibling, then
        # wraps in an ExceptionGroup. Re-raise the ACTUAL cause (RuntimeError,
        # ValueError, ProcessingError…) so the worker taxonomy + the inline HTTP
        # mapping + tests that ``raises(RuntimeError)`` see the exact exception, not a
        # wrapper. Prefer a non-Cancelled leaf; a ProcessingError wins if present.
        leaves = _flatten_exceptions(eg)
        real = [e for e in leaves if not isinstance(e, asyncio.CancelledError)]
        preferred = next((e for e in real if isinstance(e, ProcessingError)), None)
        raise (preferred or (real[0] if real else leaves[0]))

    if futures[resolved.primary.name].result() is SKIPPED:  # defensive; unreachable by rules
        raise GraphResolutionError(f"{resolved.modality}: primary stage was skipped")

    # Guard 1: nothing outside the primary+mutate cohort touched the mutable slots.
    ref = fingerprint["at_mutate_done"]
    if ref is not None and _mutables_fingerprint(ctx, resolved.primary) != ref:
        raise RuntimeError(
            f"{resolved.modality}: primary mutable_slots changed after the mutate cohort "
            "finished — a sidecar is mutating primary state (bug in a stage file)"
        )

    units = list(resolved.primary.assemble(ctx))
    for stage in sorted((s for s in resolved.enabled if s.name in sidecar_units),
                        key=lambda s: (s.order, s.name)):
        units.extend(sidecar_units[stage.name])

    # Guard 2: record identities must be distinct within the chunk.
    seen: set[str] = set()
    for u in units:
        if u.discriminator in seen:
            raise RuntimeError(
                f"{resolved.modality}: duplicate discriminator {u.discriminator!r} — two "
                "stages claimed the same record identity (their upserts would collide)"
            )
        seen.add(u.discriminator)
    return units
