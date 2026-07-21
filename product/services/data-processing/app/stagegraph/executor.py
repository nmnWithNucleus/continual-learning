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
    exist yet), must declare ``writes`` ⊆ the primary's ``mutable_slots``, and two
    enabled mutates with INTERSECTING writes are chained deterministically by
    ``(order, name)`` — never concurrent, so the mutated record is a deterministic
    function of the config (C2 idempotency), and an explicit ``needs`` contradicting
    that order is a loud cycle error, not a silent reorder;
  * ``pipeline_version = primary_fragment + mutate fragments in CHAIN order +
    sorted(other enabled fragments)`` — reduces exactly to the shipped dialects
    (``asr-mock-v0``, ``asr-mock-v0+diar-mock-v1``, ``vidproc-vlm-v0``); every future
    mutating stage forks it automatically, and the fragment SEQUENCE encodes the
    mutate execution order (diarize→speaker_id is a different dialect than the
    reverse — their records genuinely differ);
  * ``provides`` declarations of enabled stages must be disjoint (two stages committing
    the same slot would be a silent last-writer-wins).

EXECUTION is readiness-driven, not level-barriered: one task per enabled stage inside an
``asyncio.TaskGroup``, each awaiting its needs' futures — a slow sidecar never gates an
independent stage. ``run_sync`` stages execute in worker threads (the event loop is
structurally protected); a required failure cancels AND awaits every sibling before
propagating (no stage task from attempt N survives into attempt N+1 — the worker's
retry loop can never overlap attempts); a best_effort failure resolves its future to
``SKIPPED`` and its (necessarily best_effort) dependents cascade, counted per stage.

Slot ownership is enforced BY CONSTRUCTION: each stage runs against a ``SlotView``
that refuses illegal access at the offending line (a sidecar cannot even READ the
primary's mutable slots — no reference, no mutation), and a stage's committed
``StageResult.slots`` keys must be declared (``provides``, or the primary's
``mutable_slots``). This replaced the order-dependent end-of-run fingerprint guard.

Assembly is LAST and deterministic regardless of completion order: the primary's
``assemble(ctx)`` emits the primary units, then sidecar units append sorted by
``(order, name)``. One residual runtime guard: discriminators must be unique
(colliding record identities would silently upsert over each other).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from time import perf_counter
from types import MappingProxyType
from typing import Any, Optional

from starlette.concurrency import run_in_threadpool

from ..ingest_core import ProcessingError
from ..processing.base import ProcessedUnit
from .stage import SKIPPED, SlotView, Stage, StageContext, StageResult


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

    # Mutate write-set discipline (finding #7): every enabled mutate must declare WHICH
    # mutable slots it edits, the declaration must be a subset of what the primary
    # offers, and two mutates whose writes INTERSECT are chained by (order, name) —
    # an implicit dep from each writer to its predecessor in the slot's chain — so
    # overlapping mutates can never run concurrently (a nondeterministic last-writer-
    # wins would break C2 idempotency). Disjoint mutates still run concurrently.
    mutates = sorted((s for s in enabled.values() if s.kind == "mutate"),
                     key=lambda s: (s.order, s.name))
    mutable = set(primary.mutable_slots)
    for m in mutates:
        if not m.writes:
            raise GraphResolutionError(
                f"{modality}/{m.name}: an enabled mutate must declare `writes` (which "
                "primary mutable_slots it edits) — required for write access and for "
                "deterministic ordering against sibling mutates"
            )
        illegal = [w for w in m.writes if w not in mutable]
        if illegal:
            raise GraphResolutionError(
                f"{modality}/{m.name}: writes {illegal} not in primary "
                f"{primary.name!r} mutable_slots {sorted(mutable)} — a mutate may only "
                "edit slots the primary explicitly offered up"
            )
    for slot in primary.mutable_slots:
        writers = [m for m in mutates if slot in m.writes]
        for prev, nxt in zip(writers, writers[1:]):
            if prev.name not in needs[nxt.name]:
                needs[nxt.name] = (*needs[nxt.name], prev.name)

    # Committed-slot ownership must be unambiguous: two enabled stages declaring the
    # same `provides` key would be a silent last-writer-wins on the blackboard. The
    # primary's mutable_slots are SEEDED as primary-owned whether or not the primary
    # repeats them in `provides` — otherwise a sidecar declaring provides=('segments',)
    # would pass resolution and blind-clobber the mutate cohort's output via a
    # perfectly "declared" StageResult commit (review-confirmed hole).
    seen_provides: dict[str, str] = {slot: primary.name for slot in primary.mutable_slots}
    for s in enabled.values():
        for key in s.provides:
            owner = seen_provides.get(key)
            if owner is not None and not (s is primary and owner == primary.name):
                raise GraphResolutionError(
                    f"{modality}: stage {s.name!r} declares provides={key!r} already "
                    f"owned by {owner!r} — slot commits must have one owner (the "
                    "primary's mutable_slots are primary-owned by definition)"
                )
            seen_provides[key] = s.name

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

    # Version composition: base + mutate fragments in CHAIN order (the (order, name)
    # sequence that also drives overlap chaining — the dialect encodes WHO mutated and
    # in WHAT order, because the records genuinely differ under a different order) +
    # the remaining fragments sorted (declaration order must never perturb identity).
    mutate_fragments = [
        f for m in mutates for f in [m.version_fragment(settings)] if f
    ]
    other_fragments = sorted(
        f for s in enabled.values() if s is not primary and s.kind != "mutate"
        for f in [s.version_fragment(settings)] if f
    )
    return ResolvedGraph(
        modality=modality,
        primary=primary,
        enabled=sorted(enabled.values(), key=lambda s: s.order),
        needs=needs,
        pipeline_version=base + "".join(mutate_fragments) + "".join(other_fragments),
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


def _slot_view(ctx: StageContext, stage: Stage, primary: Stage) -> StageContext:
    """The stage's capability-scoped context: same chunk fields, slots wrapped in a
    ``SlotView`` and ``c1`` wrapped read-only. Mutation power follows the REFERENCE:

      * sidecar — denied even a READ of every primary mutable slot;
      * mutate  — denied a read of mutable slots OUTSIDE its declared ``writes``
        (reading one would hand it an aliased object it could scribble on without a
        chain edge ordering it against that slot's real writers — an in-place write
        through a read reference is invisible to ``__setitem__`` enforcement, so the
        reference itself is what must be withheld); direct rebinds allowed for
        ``writes`` only;
      * primary — full access (it owns the slots); it also runs ``assemble`` on the
        real dict.

    ``c1`` is a read-only mapping view: chunk identity fields feed ``record_id`` and
    the journal row AFTER the graph runs — a stage (esp. a best_effort one that then
    "skips") must never be able to corrupt them."""
    if stage.kind == "sidecar":
        deny_read = frozenset(primary.mutable_slots)
    elif stage.kind == "mutate":
        deny_read = frozenset(primary.mutable_slots) - frozenset(stage.writes)
    else:
        deny_read = frozenset()
    allow_write = frozenset(stage.writes) if stage.kind == "mutate" else frozenset()
    return replace(ctx,
                   slots=SlotView(ctx.slots, stage.name, deny_read, allow_write),
                   c1=MappingProxyType(ctx.c1))


def _allowed_commits(stage: Stage, primary: Stage) -> frozenset:
    """Slot keys a stage may commit via ``StageResult.slots``: its declared
    ``provides`` (+ the primary's ``mutable_slots``, which the primary owns)."""
    allowed = set(stage.provides)
    if stage.kind == "primary":
        allowed |= set(primary.mutable_slots)
    return frozenset(allowed)


async def run_graph(resolved: ResolvedGraph, ctx: StageContext) -> list[ProcessedUnit]:
    metrics = getattr(ctx.resources, "metrics", None)
    loop = asyncio.get_running_loop()
    futures: dict[str, asyncio.Future] = {s.name: loop.create_future() for s in resolved.enabled}
    sidecar_units: dict[str, list[ProcessedUnit]] = {}

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
        stage_ctx = _slot_view(ctx, stage, resolved.primary)
        t0 = perf_counter()
        try:
            if type(stage)._defines("run_async"):
                result = await stage.run_async(stage_ctx)
            else:
                result = await run_in_threadpool(stage.run_sync, stage_ctx)
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
        # Committed keys must be DECLARED (provides / the primary's mutable_slots) —
        # an undeclared commit is a stage-file bug and fails the chunk loudly no matter
        # the stage's policy (a best_effort stage may skip, never scribble).
        undeclared = set(result.slots) - _allowed_commits(stage, resolved.primary)
        if undeclared:
            raise RuntimeError(
                f"{resolved.modality}/{stage.name}: committed undeclared slot(s) "
                f"{sorted(undeclared)} — declare them in `provides` (slot ownership "
                "must be reviewable, not emergent)"
            )
        if result.units and stage.kind != "sidecar":
            raise RuntimeError(
                f"{resolved.modality}/{stage.name}: a {stage.kind} stage returned "
                f"{len(result.units)} unit(s) — only sidecars emit units from run; "
                "the primary emits via assemble(), a mutate edits in place"
            )
        ctx.slots.update(result.slots)
        if result.units:
            sidecar_units[stage.name] = result.units
        fut.set_result(True)

    try:
        async with asyncio.TaskGroup() as tg:
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

    # (The old post-run mutable-slots fingerprint guard lived here. It was order-
    # dependent — an illegal sidecar write landing before the last mutate finished was
    # baked into its reference snapshot and missed. The SlotView capability scoping
    # above made it redundant: a sidecar can no longer OBTAIN a mutable-slot reference,
    # so the illegal write raises at its own call site instead.)

    units = list(resolved.primary.assemble(ctx))
    for stage in sorted((s for s in resolved.enabled if s.name in sidecar_units),
                        key=lambda s: (s.order, s.name)):
        units.extend(sidecar_units[stage.name])

    # Runtime guard: record identities must be distinct within the chunk.
    seen: set[str] = set()
    for u in units:
        if u.discriminator in seen:
            raise RuntimeError(
                f"{resolved.modality}: duplicate discriminator {u.discriminator!r} — two "
                "stages claimed the same record identity (their upserts would collide)"
            )
        seen.add(u.discriminator)
    return units
