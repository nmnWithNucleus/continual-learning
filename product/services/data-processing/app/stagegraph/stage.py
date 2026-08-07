"""The uniform Stage + per-modality registry (Slot Law L4/L5).

A stage is ONE disjoint file under ``app/stages/<modality>/`` decorated with
``@register_stage``. The declaration IS the contract surface:

  * ``name`` / ``modality`` — who this is; ``slot`` (default = ``name``) — the ONE
    record slot it may produce. One enabled producer per slot (registration + resolve
    both enforce it); no stage edits another stage's slot — derived views are new
    slots from ordinary stages whose ``needs`` reference the inputs (L5).
  * ``stage_version`` (vS) bumps on CONTRACT changes: needs, slot name/shape, emit
    semantics, budget. ``backend`` (name + vB, resolved in code) bumps on
    IMPLEMENTATION changes: model, weights, prompts, thresholds, any behavior (L4).
    The stage's dialect segment is ``<name>.v<S>-<backend>.v<B>[.exp-<code>]``; the
    executor composes ``pipeline_version`` from the enabled stages' segments,
    sorted, '+'-joined. No output-affecting env knobs exist anywhere — a stage
    never even sees Settings; behavior lives in code or it does not exist.
  * Mock backends are client-level fakes selected the same way real backends are —
    by name, in the version string: construct the SAME stage class with
    ``backend=Backend("mock", 1)`` and hand the context a fake client under the
    stage's ``server`` key. The stage body runs identically either way.
  * ``required`` (L7): a required stage's failure fails the chunk attempt (no
    record — worker retry then dead-letter); an optional stage's failure leaves its
    slot a hole, cancels its downstream cone, and the record ships.
  * ``byte_budget`` (L5): the ceiling on the emitted slot's serialized UTF-8
    JSON bytes at assembly — the measure includes the executor-stamped
    ``version`` key, i.e. exactly the bytes the record carries for the slot.
    Raising it is a stage-version bump; exceeding it is a stage failure, never
    truncation. Declared here, in the stage file, so review sees it.
  * ``consumer`` (L10): every slot ships
    with a named consumer-today or an explicit speculative marker —
    ``"daylog:<line>"`` (a C10 renderer route), ``"stage:<name>"`` (a downstream
    stage consumes it in-run), or ``"speculative:<why>"``. Mandatory at
    registration; declaration-only (no runtime behavior); pinned by the T-3
    snapshot. Rough chars-per-second-of-life costs stay docstring-grade.
  * Exactly one of ``run_sync`` (executed in a worker thread — blocking work can
    never freeze the event loop by accident) or ``run_async`` (await-native IO:
    the thin-client path, freeing threadpool tokens). Both receive ``StageContext``
    and return ``StageOutput``.

``server`` is operational routing only (which model-server client pool the stage
calls, e.g. ``"whisper"``); it is deliberately NOT part of the identity segment —
endpoints/replicas/timeouts are operational, the model behind them is pinned by
the server's own code + manifest identity (L9).

Deleted with their subject matter (D23/D26): primary/mutate/sidecar kinds,
``writes``/``mutable_slots``, SlotView capability proxies, ``best_effort`` policy
machinery, ``order``, per-stage ``version_fragment``/``enabled`` resolvers, the R1
fork rider + its frozen exemption, ``assemble``.
"""
from __future__ import annotations

import importlib
import pkgutil
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

# Grammar shared with the C2 v1 contract's pipeline_version/slot_version patterns:
# stage names, backend names, slot names and experiment codes are all [a-z0-9_]+.
_NAME_RE = re.compile(r"[a-z0-9_]+\Z")


class StageRegistrationError(Exception):
    """A malformed stage declaration — raised at import/registration so a bad
    drop-in fails loudly before it can ever run."""


@dataclass(frozen=True)
class Backend:
    """A stage's implementation identity: ``name`` selects it (in code — the same
    way for real and mock), ``version`` (vB) bumps on any behavior change."""

    name: str
    version: int


@dataclass
class StageOutput:
    """What a stage's run returns.

    ``value`` — the JSON slot value committed into the record under the stage's
    slot name (the executor adds the ``version`` key). ``None`` = this stage emits
    no record slot (e.g. a prep stage whose whole output is transient bytes).
    An empty-but-present value is the honest empty claim (L11) — distinct from
    ``None`` and from a hole.

    ``bytes`` — an in-run-only payload (raw frames, decoded audio…) handed to
    dependents on the blackboard and FREED by the executor after the last consumer
    finishes (L5: within a run the blackboard carries {ref, bytes}; binary never
    lands in a slot).
    """

    value: Any = None
    bytes: Any = None


@dataclass
class StageContext:
    """The per-stage view of one chunk's run. No Settings anywhere: a stage that
    wants a knob must put it in code and bump vB (L4).

    ``inputs`` holds exactly the declared ``needs``' outputs, keyed by stage name.
    ``clients`` maps server names (``Stage.server``) to model clients — real
    ModelClients in production, client-level fakes under a mock dialect.
    """

    c1: Mapping[str, Any]
    blob: bytes
    span_seconds: float
    inputs: dict[str, StageOutput] = field(default_factory=dict)
    clients: Mapping[str, Any] = field(default_factory=dict)
    metrics: Any = None


class Stage:
    """Subclass + ``@register_stage`` in a file under ``app/stages/<modality>/``."""

    name: str = ""
    modality: str = ""
    stage_version: int = 0                 # vS; >= 1
    backend: Optional[Backend] = None      # resolved in code; vB >= 1
    needs: tuple[str, ...] = ()            # stage names this one awaits
    slot: Optional[str] = None             # record slot name; default = name
    required: bool = True                  # L7 failure policy
    byte_budget: int = 0                   # L5 ceiling on the emitted slot's bytes
    consumer: str = ""                     # L10: daylog:<line> | stage:<name> | speculative:<why>
    server: str = ""                       # operational: which client pool run() uses
    experiment: str = ""                   # non-empty -> .exp-<code> on the segment

    def __init__(self, *, backend: Optional[Backend] = None,
                 experiment: Optional[str] = None) -> None:
        # The in-code selection point: tests / an offline harness construct the
        # same stage class with a mock or experimental Backend, and the dialect
        # says so (never an env knob).
        if backend is not None:
            self.backend = backend
        if experiment is not None:
            self.experiment = experiment

    # ---- identity --------------------------------------------------------------
    @property
    def slot_name(self) -> str:
        return self.slot if self.slot else self.name

    @property
    def segment(self) -> str:
        """This stage's dialect segment: ``<stage>.v<S>-<backend>.v<B>[.exp-<code>]``."""
        seg = f"{self.name}.v{self.stage_version}-{self.backend.name}.v{self.backend.version}"
        if self.experiment:
            seg += f".exp-{self.experiment}"
        return seg

    # ---- execution (exactly one defined) ----------------------------------------
    def run_sync(self, ctx: StageContext) -> StageOutput:  # pragma: no cover - abstract
        raise NotImplementedError

    async def run_async(self, ctx: StageContext) -> StageOutput:  # pragma: no cover
        raise NotImplementedError

    # ---- introspection -----------------------------------------------------------
    @classmethod
    def _defines(cls, method: str) -> bool:
        return getattr(cls, method) is not getattr(Stage, method)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Stage {self.modality}/{self.name} {self.segment}>"


def validate_stage(stage: Stage) -> None:
    """The declaration checks, hard. Shared by registration and by explicit
    stage-set construction (mock dialects), so a malformed test set fails the
    same way a malformed drop-in file does."""
    cls_name = type(stage).__name__

    def _bad(msg: str) -> StageRegistrationError:
        return StageRegistrationError(f"{cls_name}: {msg}")

    for label, value in (("name", stage.name), ("modality", stage.modality)):
        if not isinstance(value, str) or not _NAME_RE.fullmatch(value or ""):
            raise _bad(f"{label} must match [a-z0-9_]+ (got {value!r})")
    if stage.slot is not None and (
        not isinstance(stage.slot, str) or not _NAME_RE.fullmatch(stage.slot)
    ):
        raise _bad(f"slot must match [a-z0-9_]+ (got {stage.slot!r})")
    if not isinstance(stage.stage_version, int) or isinstance(stage.stage_version, bool) \
            or stage.stage_version < 1:
        raise _bad(f"stage_version (vS) must be an int >= 1 (got {stage.stage_version!r})")
    if not isinstance(stage.backend, Backend):
        raise _bad("backend must be a Backend(name, version), resolved in code (L4)")
    if not isinstance(stage.backend.name, str) or not _NAME_RE.fullmatch(stage.backend.name or ""):
        raise _bad(f"backend.name must match [a-z0-9_]+ (got {stage.backend.name!r})")
    if not isinstance(stage.backend.version, int) or isinstance(stage.backend.version, bool) \
            or stage.backend.version < 1:
        raise _bad(f"backend.version (vB) must be an int >= 1 (got {stage.backend.version!r})")
    if not isinstance(stage.experiment, str) or (
        stage.experiment and not _NAME_RE.fullmatch(stage.experiment)
    ):
        raise _bad(f"experiment code must match [a-z0-9_]+ (got {stage.experiment!r})")
    if not isinstance(stage.required, bool):
        raise _bad(f"required must be a bool (got {stage.required!r})")
    if not isinstance(stage.byte_budget, int) or isinstance(stage.byte_budget, bool) \
            or stage.byte_budget < 1:
        raise _bad(
            f"byte_budget must be a positive int (got {stage.byte_budget!r}) — every "
            "slot ships under a byte budget declared in the stage file (L5/L10)"
        )
    if not isinstance(stage.consumer, str) or not re.fullmatch(
            r"(daylog|stage|speculative):.+", stage.consumer):
        raise _bad(
            f"consumer must be 'daylog:<line>' | 'stage:<name>' | "
            f"'speculative:<why>' (got {stage.consumer!r}) — a slot ships with a "
            "named consumer-today or an explicit speculative marker (L10)"
        )
    if not isinstance(stage.needs, tuple):
        raise _bad(f"needs must be a tuple of stage names (got {type(stage.needs).__name__})")
    if stage.name in stage.needs:
        raise _bad("needs may not include the stage itself")
    if len(set(stage.needs)) != len(stage.needs):
        raise _bad(f"needs contains duplicates: {stage.needs}")
    cls = type(stage)
    if cls._defines("run_sync") == cls._defines("run_async"):
        raise _bad("define exactly one of run_sync (threadpooled) or run_async "
                   "(loop-native IO)")


# modality -> {name: Stage instance}
_REGISTRY: dict[str, dict[str, Stage]] = {}
_discovered = False


def register_stage(cls: type[Stage]) -> type[Stage]:
    """Class decorator: validate the declaration hard at import, then register
    the stage instance (constructed with its in-code default backend)."""
    stage = cls()
    validate_stage(stage)

    by_name = _REGISTRY.setdefault(stage.modality, {})
    existing = by_name.get(stage.name)
    if existing is not None and type(existing) is not cls:
        raise StageRegistrationError(
            f"duplicate stage {stage.modality}/{stage.name}: {cls.__name__} conflicts "
            f"with {type(existing).__name__}"
        )
    for other in by_name.values():
        if other.name != stage.name and other.slot_name == stage.slot_name:
            raise StageRegistrationError(
                f"{cls.__name__}: slot {stage.slot_name!r} already produced by "
                f"{stage.modality}/{other.name} — one enabled producer per slot (L5)"
            )
    by_name[stage.name] = stage
    return cls


def _discover() -> None:
    """Import every module under ``app/stages/`` exactly once (recursive) so each
    stage file self-registers — a new stage is a new file and NOTHING else."""
    global _discovered
    if _discovered:
        return
    from .. import stages  # the drop-in package

    for mod in pkgutil.walk_packages(stages.__path__, stages.__name__ + "."):
        importlib.import_module(mod.name)
    # Post-discovery: every declared need must reference a registered stage.
    # (Explicit stage sets — mock dialects — are re-checked at resolve time.)
    for modality, by_name in _REGISTRY.items():
        for stage in by_name.values():
            for need in stage.needs:
                if need not in by_name:
                    raise StageRegistrationError(
                        f"{modality}/{stage.name}: needs unknown stage {need!r}"
                    )
    # Flipped only AFTER the needs check: a failed discovery must not leave a
    # half-validated registry marked done (cleanup round).
    _discovered = True


def stages_for(modality: str) -> list[Stage]:
    """All registered stages for a modality, sorted by name (deterministic;
    execution order is readiness-driven, record slots are a map — nothing else
    consumes an ordering). Empty if none."""
    _discover()
    return sorted(_REGISTRY.get(modality, {}).values(), key=lambda s: s.name)


def registered_modalities() -> list[str]:
    """Sorted modalities that currently have at least one registered stage —
    the modality routing the retired processors/ registry used to own."""
    _discover()
    return sorted(m for m, by_name in _REGISTRY.items() if by_name)
