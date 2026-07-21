"""The Stage protocol + per-modality registry (zero-edit discovery, like processors/).

A stage is ONE disjoint file under ``app/stages/<modality>/`` decorated with
``@register_stage``. The class declares everything the executor needs; the body is the
transform. Discovery auto-imports every module under ``app/stages/`` on first use, so a
new stage is a new file and NOTHING else.

Kinds (who may touch what — enforced):
  * ``primary``  — exactly one enabled per modality; fills slots via ``run``; its
                   ``assemble(ctx)`` (pure, sync, called after ALL stages finish) emits
                   the modality's primary units in order. Its ``version_fragment`` is the
                   BASE dialect (non-empty whenever enabled).
  * ``mutate``   — mutates the primary's declared ``mutable_slots`` in place (e.g.
                   diarization filling speakers), declaring exactly WHICH in ``writes``
                   (must be a subset of the primary's ``mutable_slots`` — resolution
                   errors otherwise). **Enabledness IS
                   ``version_fragment(settings) != ''``** — one resolver drives both, so
                   a mutate stage physically cannot run without forking the dialect (the
                   silent-overwrite bug class the audio slice once caught by review is
                   now structural). Overriding ``enabled`` on a mutate stage is a
                   registration error. Implicitly depends on the primary; two mutates
                   whose ``writes`` intersect are CHAINED deterministically by ``order``
                   (never concurrent — see executor.resolve), and their fragments
                   compose in that same chain order, so the dialect encodes the sequence.
  * ``sidecar``  — returns ADDITIONAL units (own discriminators); never touches the
                   primary. May be ``best_effort``.

Slot ownership is CAPABILITY-SCOPED at runtime, not just declared: each stage's run
receives a ``SlotView`` of the shared slots that (a) refuses writes to any key the stage
does not own, and (b) refuses to even HAND a sidecar a reference to the primary's
``mutable_slots`` — so an illegal mutation is impossible by construction (you cannot
scribble on an object you were never given), raising ``SlotAccessError`` at the exact
offending line, order-independently. This replaced the old end-of-run fingerprint guard,
which was order-dependent (an illegal write landing before the mutate cohort finished
was baked into the reference and missed).

Policies: ``required`` (default — failure fails the chunk, worker taxonomy applies) or
``best_effort`` (failure skips the stage + its downstream best_effort cone, counted;
sidecar-only — a required/primary stage downstream of a best_effort one is a resolution
error, because its "required" promise would be hollow).

Execution contract — structural, not conventional: a stage defines EXACTLY ONE of
``run_sync`` (always executed in a worker thread — blocking model/subprocess/CPU work
can never freeze the event loop by accident) or ``run_async`` (await-native IO only:
shared HTTP pools). Both receive the ``StageContext`` and return a ``StageResult``.
"""
from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass, field
from typing import Any, Optional

from ..config import Settings
from ..processing.base import ProcessedUnit


class StageRegistrationError(Exception):
    """A malformed stage file — raised at import/discovery so a bad drop-in fails loudly."""


class SlotAccessError(RuntimeError):
    """A stage touched a slot it does not own — a stage-file bug, raised AT the offending
    read/write (synchronously, order-independently), never deferred to an end-of-run
    check. Subclasses RuntimeError so existing required-failure taxonomy applies."""


class SlotView:
    """A stage-scoped capability view over the shared slot dict.

    Reads pass through EXCEPT keys in ``deny_read`` (a sidecar asking for the primary's
    mutable slots — refusing the reference is what makes illegal mutation structurally
    impossible: you cannot scribble on an object you were never handed). Direct writes
    are allowed only for keys in ``allow_write`` (a mutate's declared ``writes``); all
    other slot production goes through ``StageResult.slots`` so commit-on-success holds.
    """

    __slots__ = ("_slots", "_stage", "_deny_read", "_allow_write")

    def __init__(self, slots: dict, stage_name: str,
                 deny_read: frozenset, allow_write: frozenset) -> None:
        self._slots = slots
        self._stage = stage_name
        self._deny_read = deny_read
        self._allow_write = allow_write

    def _check_read(self, key: str) -> None:
        if key in self._deny_read:
            raise SlotAccessError(
                f"stage {self._stage!r} may not read primary mutable slot {key!r} — "
                "a reference is mutation power (in-place writes bypass __setitem__): "
                "sidecars never see primary mutable state, and a mutate sees only the "
                "slots it declared in `writes` (declare it there — that also chains "
                "you deterministically against the slot's other writers)"
            )

    def __getitem__(self, key: str):
        self._check_read(key)
        return self._slots[key]

    def get(self, key: str, default=None):
        self._check_read(key)
        return self._slots.get(key, default)

    def __setitem__(self, key: str, value) -> None:
        if key not in self._allow_write:
            raise SlotAccessError(
                f"stage {self._stage!r} may not write slot {key!r} directly — return it "
                "via StageResult.slots (commit-on-success), or declare it in `writes` "
                "on a mutate stage"
            )
        self._slots[key] = value

    def __delitem__(self, key: str) -> None:
        raise SlotAccessError(f"stage {self._stage!r} may not delete slot {key!r}")

    def __contains__(self, key: str) -> bool:
        return key in self._slots and key not in self._deny_read

    def __iter__(self):
        return (k for k in self._slots if k not in self._deny_read)

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"SlotView(stage={self._stage!r}, keys={sorted(self)!r})"


# Sentinel a skipped stage's future resolves to; dependents cascade-skip on it.
SKIPPED = object()

_KINDS = ("primary", "mutate", "sidecar")
_POLICIES = ("required", "best_effort")


@dataclass
class StageResult:
    """What a stage's run returns. ``slots`` are committed into the context ONLY on
    success (a failed stage contributes nothing); ``units`` are a sidecar's additional
    records (primary emits its units later, in ``assemble``)."""

    units: list[ProcessedUnit] = field(default_factory=list)
    slots: dict[str, Any] = field(default_factory=dict)


@dataclass
class StageContext:
    """The per-chunk blackboard stages read and write (via declared slots)."""

    c1: dict[str, Any]
    blob: bytes
    settings: Settings
    span_seconds: float
    slots: dict[str, Any] = field(default_factory=dict)
    resources: Any = None  # app-level handles: .metrics, .vlm_pool (may be None)


class Stage:
    """Subclass + ``@register_stage`` in a file under ``app/stages/<modality>/``."""

    name: str = ""
    modality: str = ""
    kind: str = "sidecar"                 # primary | mutate | sidecar
    policy: str = "required"              # required | best_effort (sidecar only)
    needs: tuple[str, ...] = ()           # stage names this one awaits
    provides: tuple[str, ...] = ()        # slot keys this stage commits via StageResult (AUTHORITATIVE:
                                          # committing an undeclared key is a runtime error)
    mutable_slots: tuple[str, ...] = ()   # PRIMARY only: slots mutate stages may write in place
    writes: tuple[str, ...] = ()          # MUTATE only: which mutable_slots it edits in place
                                          # (drives overlap-chaining; ⊆ primary.mutable_slots)
    order: int = 0                        # deterministic assembly order (unique per modality)

    # ---- resolution-time switches -------------------------------------------------
    def enabled(self, settings: Settings) -> bool:
        """primary/sidecar only — mutate enabledness derives from the fragment."""
        return True

    def version_fragment(self, settings: Settings) -> str:
        """primary: the BASE dialect (non-empty whenever enabled). mutate: '+tag' or ''
        — THE single resolver (drives enabledness too). sidecar: usually ''."""
        return ""

    def is_enabled(self, settings: Settings) -> bool:
        """The executor's view: one rule per kind, no second decision site."""
        if self.kind == "mutate":
            return bool(self.version_fragment(settings))
        return self.enabled(settings)

    # ---- execution (exactly one defined) -------------------------------------------
    def run_sync(self, ctx: StageContext) -> StageResult:  # pragma: no cover - abstract
        raise NotImplementedError

    async def run_async(self, ctx: StageContext) -> StageResult:  # pragma: no cover
        raise NotImplementedError

    # ---- primary only ----------------------------------------------------------------
    def assemble(self, ctx: StageContext) -> list[ProcessedUnit]:  # pragma: no cover
        raise NotImplementedError

    # ---- introspection ---------------------------------------------------------------
    @classmethod
    def _defines(cls, method: str) -> bool:
        return getattr(cls, method) is not getattr(Stage, method)


# modality -> {name: Stage instance}
_REGISTRY: dict[str, dict[str, Stage]] = {}
_discovered = False


def register_stage(cls: type[Stage]) -> type[Stage]:
    """Class decorator: validate the declaration hard at import, then register."""
    stage = cls()
    if not stage.name or not stage.modality:
        raise StageRegistrationError(f"{cls.__name__}: name and modality are required")
    if stage.kind not in _KINDS:
        raise StageRegistrationError(f"{cls.__name__}: kind must be one of {_KINDS}")
    if stage.policy not in _POLICIES:
        raise StageRegistrationError(f"{cls.__name__}: policy must be one of {_POLICIES}")
    if stage.kind in ("primary", "mutate") and stage.policy == "best_effort":
        raise StageRegistrationError(
            f"{cls.__name__}: {stage.kind} stages cannot be best_effort — a lost mutation "
            "or primary under an unchanged pipeline_version would be a silent lie"
        )
    if stage.kind == "mutate" and cls._defines("enabled"):
        raise StageRegistrationError(
            f"{cls.__name__}: mutate stages must NOT override enabled() — enabledness IS "
            "version_fragment(settings) != '' (single resolver)"
        )
    defines_sync, defines_async = cls._defines("run_sync"), cls._defines("run_async")
    if defines_sync == defines_async:
        raise StageRegistrationError(
            f"{cls.__name__}: define exactly one of run_sync (threadpooled) or "
            f"run_async (loop-native IO)"
        )
    if stage.kind == "primary" and not cls._defines("assemble"):
        raise StageRegistrationError(f"{cls.__name__}: primary stages must define assemble()")
    if stage.kind != "primary" and stage.mutable_slots:
        raise StageRegistrationError(f"{cls.__name__}: only primary declares mutable_slots")
    if stage.kind == "mutate" and not stage.writes:
        raise StageRegistrationError(
            f"{cls.__name__}: mutate stages must declare `writes` (which of the "
            "primary's mutable_slots they edit in place) — it drives write access AND "
            "the deterministic chaining of overlapping mutates"
        )
    if stage.kind != "mutate" and stage.writes:
        raise StageRegistrationError(
            f"{cls.__name__}: only mutate stages declare `writes`; a {stage.kind} "
            "produces slots via StageResult (see `provides`), it never edits in place"
        )

    by_name = _REGISTRY.setdefault(stage.modality, {})
    existing = by_name.get(stage.name)
    if existing is not None and type(existing) is not cls:
        raise StageRegistrationError(
            f"duplicate stage {stage.modality}/{stage.name}: {cls.__name__} conflicts "
            f"with {type(existing).__name__}"
        )
    for other in by_name.values():
        if other.order == stage.order and other.name != stage.name:
            raise StageRegistrationError(
                f"{cls.__name__}: order {stage.order} already used by "
                f"{stage.modality}/{other.name} — orders are the deterministic assembly "
                "sequence and must be unique per modality"
            )
    by_name[stage.name] = stage
    return cls


def _discover() -> None:
    """Import every module under ``app/stages/`` exactly once (recursive) so each stage
    file self-registers — the processors-registry pattern, one level deeper."""
    global _discovered
    if _discovered:
        return
    from .. import stages  # the drop-in package

    for mod in pkgutil.walk_packages(stages.__path__, stages.__name__ + "."):
        importlib.import_module(mod.name)
    _discovered = True
    # Post-discovery: every declared need must reference a stage that EXISTS (enabled-ness
    # is settings-dependent and checked at resolution; existence is static).
    for modality, by_name in _REGISTRY.items():
        for stage in by_name.values():
            for need in stage.needs:
                if need not in by_name:
                    raise StageRegistrationError(
                        f"{modality}/{stage.name}: needs unknown stage {need!r}"
                    )


def stages_for(modality: str) -> list[Stage]:
    """All registered stages for a modality, in declared order. Empty if none."""
    _discover()
    return sorted(_REGISTRY.get(modality, {}).values(), key=lambda s: s.order)
