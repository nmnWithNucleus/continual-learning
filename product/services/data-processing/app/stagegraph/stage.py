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
                   diarization filling speakers). **Enabledness IS
                   ``version_fragment(settings) != ''``** — one resolver drives both, so
                   a mutate stage physically cannot run without forking the dialect (the
                   silent-overwrite bug class the audio slice once caught by review is
                   now structural). Overriding ``enabled`` on a mutate stage is a
                   registration error. Implicitly depends on the primary.
  * ``sidecar``  — returns ADDITIONAL units (own discriminators); never touches the
                   primary. May be ``best_effort``.

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
    provides: tuple[str, ...] = ()        # slot keys this stage commits (documentation + review surface)
    mutable_slots: tuple[str, ...] = ()   # PRIMARY only: slots mutate stages may write in place
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
