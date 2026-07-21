"""Stage-graph core (app/stagegraph/) — the executor + resolver semantics on synthetic
graphs, plus the registration guardrails. No registry pollution: executor/resolver tests
build Stage instances directly and call resolve()/run_graph(); registration-error tests
use a throwaway modality cleaned up after.
"""
from __future__ import annotations

import asyncio

import pytest

from app.ingest_core import ProcessingError
from app.processing.base import ProcessedContent, ProcessedUnit
from app.stagegraph import stage as stage_mod
from app.stagegraph.executor import (
    GraphResolutionError, resolve, run_graph,
)
from app.stagegraph.stage import (
    SKIPPED, SlotAccessError, Stage, StageContext, StageRegistrationError, StageResult,
    register_stage,
)


def _ctx():
    return StageContext(c1={"chunk_id": "c", "t_start": "2026-07-20T00:00:00Z"},
                        blob=b"", settings=None, span_seconds=1.0)


def _unit(disc: str) -> ProcessedUnit:
    return ProcessedUnit(content=ProcessedContent(kind="caption", text=disc), discriminator=disc)


def _run(stages, settings=None, ctx=None):
    resolved = resolve("m", stages, settings)
    return asyncio.run(run_graph(resolved, ctx or _ctx()))


# ---- a minimal, valid primary used across tests -------------------------------

class _Primary(Stage):
    name = "primary"; modality = "m"; kind = "primary"; order = 0
    mutable_slots = ("shared",)
    def version_fragment(self, settings): return "base-v0"
    def run_sync(self, ctx): return StageResult(slots={"shared": []})
    def assemble(self, ctx):
        return [_unit("")] + [_unit(f"x{i}") for i in ctx.slots.get("shared", [])]


# ---- concurrency ---------------------------------------------------------------

def test_independent_stages_run_concurrently():
    """Two independent stages that RENDEZVOUS (each sets its event then awaits the
    other's) complete only if the executor runs them concurrently; sequential
    execution would deadlock -> wait_for timeout -> failure."""
    ev_a, ev_b = asyncio.Event(), asyncio.Event()

    class A(Stage):
        name = "a"; modality = "m"; kind = "sidecar"; order = 10
        async def run_async(self, ctx):
            ev_a.set()
            await asyncio.wait_for(ev_b.wait(), 1.0)
            return StageResult(units=[_unit("a")])

    class B(Stage):
        name = "b"; modality = "m"; kind = "sidecar"; order = 20
        async def run_async(self, ctx):
            ev_b.set()
            await asyncio.wait_for(ev_a.wait(), 1.0)
            return StageResult(units=[_unit("b")])

    units = _run([_Primary(), A(), B()])
    assert [u.discriminator for u in units] == ["", "a", "b"]  # primary, then order


# ---- version composition -------------------------------------------------------

def test_pipeline_version_mutates_in_chain_order_then_sorted_sidecars():
    """Mutate fragments compose in (order, name) chain order — the dialect encodes the
    mutate execution SEQUENCE (the records genuinely differ under a different order) —
    then non-mutate fragments append sorted (declaration order never perturbs ids)."""
    class Diar(Stage):
        name = "diar"; modality = "m"; kind = "mutate"; needs = ("primary",); order = 10
        writes = ("shared",)
        def version_fragment(self, settings): return "+diar"
        def run_sync(self, ctx): return StageResult()

    class Z(Stage):  # sidecar with a fragment that sorts BEFORE the mutate's
        name = "z"; modality = "m"; kind = "sidecar"; order = 5
        def version_fragment(self, settings): return "+aaa"
        def run_sync(self, ctx): return StageResult()

    resolved = resolve("m", [_Primary(), Diar(), Z()], None)
    assert resolved.pipeline_version == "base-v0+diar+aaa"  # mutate chain, then sorted

    # A disabled mutate contributes nothing (its enabledness IS its fragment) — and is
    # exempt from the `writes` requirement (it will never run).
    class OffDiar(Stage):
        name = "diar"; modality = "m"; kind = "mutate"; needs = ("primary",); order = 10
        def version_fragment(self, settings): return ""   # off
        def run_sync(self, ctx): return StageResult()

    assert resolve("m", [_Primary(), OffDiar()], None).pipeline_version == "base-v0"


def test_two_mutates_version_encodes_chain_order():
    def mut(nm, ordr, frag):
        return type(nm, (Stage,), {
            "name": nm, "modality": "m", "kind": "mutate", "order": ordr,
            "writes": ("shared",),
            "version_fragment": lambda self, s, f=frag: f,
            "run_sync": lambda self, ctx: StageResult(),
        })()

    # Declared out of order on purpose: composition follows (order, name), not the list.
    resolved = resolve("m", [mut("later", 20, "+bbb"), _Primary(), mut("earlier", 10, "+zzz")], None)
    assert resolved.pipeline_version == "base-v0+zzz+bbb"  # chain order, NOT sorted


# ---- failure semantics ---------------------------------------------------------

def test_required_failure_surfaces_the_leaf_exception():
    class Boom(Stage):
        name = "boom"; modality = "m"; kind = "sidecar"; order = 10  # required
        def run_sync(self, ctx): raise RuntimeError("kaboom")

    with pytest.raises(RuntimeError, match="kaboom"):
        _run([_Primary(), Boom()])


def test_required_processingerror_is_preferred_and_preserved():
    class Term(Stage):
        name = "term"; modality = "m"; kind = "sidecar"; order = 10
        def run_sync(self, ctx):
            raise ProcessingError({"error": "bad"}, http_status=500, transient=False)

    with pytest.raises(ProcessingError):
        _run([_Primary(), Term()])


def test_best_effort_failure_skips_and_cascades_and_commits_nothing():
    ran = {"c": False}

    class Flaky(Stage):
        name = "flaky"; modality = "m"; kind = "sidecar"; policy = "best_effort"; order = 10
        provides = ("flaky_slot",)
        def run_sync(self, ctx): raise ValueError("nope")

    class DependsOnFlaky(Stage):
        name = "dep"; modality = "m"; kind = "sidecar"; policy = "best_effort"
        needs = ("flaky",); order = 20
        def run_sync(self, ctx):
            ran["c"] = True  # must NOT run — its dep was skipped
            return StageResult(units=[_unit("dep")])

    units = _run([_Primary(), Flaky(), DependsOnFlaky()])
    assert [u.discriminator for u in units] == [""]   # only the primary
    assert ran["c"] is False                          # cascade-skipped


def test_commit_on_success_only_and_no_partial_leak():
    class Good(Stage):
        name = "good"; modality = "m"; kind = "sidecar"; policy = "best_effort"; order = 10
        provides = ("k",)
        def run_sync(self, ctx): return StageResult(slots={"k": "v"})

    class Bad(Stage):
        name = "bad"; modality = "m"; kind = "sidecar"; policy = "best_effort"; order = 20
        provides = ("j",)
        def run_sync(self, ctx):
            ctx.slots["leaked"] = True   # direct write: REFUSED by the SlotView
            raise ValueError("x")        # (never reached)

    resolved = resolve("m", [_Primary(), Good(), Bad()], None)
    ctx = _ctx()
    asyncio.run(run_graph(resolved, ctx))
    assert ctx.slots.get("k") == "v"          # succeeded stage committed
    assert "j" not in ctx.slots               # failed stage's provides absent
    # The old partial-leak hole is CLOSED: a direct ctx.slots write from a stage raises
    # SlotAccessError at the call site (here: best_effort -> the stage just skips).
    assert "leaked" not in ctx.slots


# ---- guards --------------------------------------------------------------------

def test_duplicate_discriminator_is_terminal():
    class Dup(Stage):
        name = "dup"; modality = "m"; kind = "sidecar"; order = 10
        def run_sync(self, ctx): return StageResult(units=[_unit("")])  # collides w/ primary

    with pytest.raises(RuntimeError, match="duplicate discriminator"):
        _run([_Primary(), Dup()])


def test_sidecar_cannot_even_read_a_mutable_slot():
    """The capability model: a sidecar is refused the REFERENCE (read raises), so
    mutation is impossible by construction — caught at the offending line."""
    class Sneak(Stage):
        name = "sneak"; modality = "m"; kind = "sidecar"; needs = ("primary",); order = 10
        async def run_async(self, ctx):
            ctx.slots["shared"].append(1)  # illegal: raises on the READ of 'shared'
            return StageResult()

    with pytest.raises(SlotAccessError, match="may not read primary mutable slot"):
        _run([_Primary(), Sneak()])


def test_illegal_sidecar_access_caught_even_while_mutate_still_running():
    """THE order-dependence hole the old fingerprint guard had: an illegal write landing
    BEFORE the mutate cohort finished was baked into its reference snapshot and missed.
    The SlotView catches it at the call site regardless of what else is in flight."""
    class SlowMut(Stage):
        name = "slowmut"; modality = "m"; kind = "mutate"; writes = ("shared",); order = 10
        def version_fragment(self, settings): return "+slow"
        async def run_async(self, ctx):
            await asyncio.sleep(30)  # never finishes on its own; cancelled by the failure
            return StageResult()

    class Sneak(Stage):
        name = "sneak"; modality = "m"; kind = "sidecar"; needs = ("primary",); order = 20
        async def run_async(self, ctx):
            ctx.slots["shared"].append(1)  # lands while slowmut is mid-flight
            return StageResult()

    with pytest.raises(SlotAccessError, match="may not read primary mutable slot"):
        _run([_Primary(), SlowMut(), Sneak()])


def test_sidecar_direct_slot_write_is_refused():
    class Bad(Stage):
        name = "bad"; modality = "m"; kind = "sidecar"; order = 10
        def run_sync(self, ctx):
            ctx.slots["mine"] = 1   # even a NON-mutable key: commits go via StageResult
            return StageResult()

    with pytest.raises(SlotAccessError, match="may not write slot"):
        _run([_Primary(), Bad()])


def test_mutate_write_outside_declared_writes_is_refused():
    class TwoSlot(_Primary):
        mutable_slots = ("shared", "other")
        def run_sync(self, ctx): return StageResult(slots={"shared": [], "other": []})

    class Mut(Stage):
        name = "mut"; modality = "m"; kind = "mutate"; writes = ("shared",); order = 10
        def version_fragment(self, settings): return "+m"
        def run_sync(self, ctx):
            ctx.slots["other"] = [1]   # declared writes say 'shared' only
            return StageResult()

    with pytest.raises(SlotAccessError, match="may not write slot"):
        _run([TwoSlot(), Mut()])


def test_undeclared_stageresult_commit_is_loud_even_for_best_effort():
    class Sloppy(Stage):
        name = "sloppy"; modality = "m"; kind = "sidecar"; policy = "best_effort"; order = 10
        # provides NOT declared — the commit below is a stage-file bug, not skippable
        def run_sync(self, ctx): return StageResult(slots={"surprise": 1})

    with pytest.raises(RuntimeError, match="committed undeclared slot"):
        _run([_Primary(), Sloppy()])


# ---- mutate overlap chaining (finding #7) --------------------------------------

def _mk_mutate(nm: str, ordr: int, frag: str, run):
    return type(nm, (Stage,), {
        "name": nm, "modality": "m", "kind": "mutate", "order": ordr,
        "writes": ("shared",),
        "version_fragment": lambda self, s, f=frag: f,
        "run_async": run,
    })()


def test_overlapping_mutates_are_chained_by_order_never_concurrent():
    """Two mutates writing the same slot execute strictly in (order, name) sequence —
    the slower earlier one always lands first, so the mutated record is deterministic.
    (Concurrent execution would interleave nondeterministically — the #7 race.)"""
    async def run_a(self, ctx):
        await asyncio.sleep(0.05)          # slower; concurrency would let B beat it
        ctx.slots["shared"] = ctx.slots["shared"] + ["a"]
        return StageResult()

    async def run_b(self, ctx):
        ctx.slots["shared"] = ctx.slots["shared"] + ["b"]
        return StageResult()

    a = _mk_mutate("A", 10, "+a", run_a)
    b = _mk_mutate("B", 20, "+b", run_b)
    resolved = resolve("m", [_Primary(), b, a], None)   # declared out of order
    assert "A" in resolved.needs["B"]                    # the implicit chain edge
    assert resolved.pipeline_version == "base-v0+a+b"    # dialect encodes the sequence
    ctx = _ctx()
    asyncio.run(run_graph(resolved, ctx))
    assert ctx.slots["shared"] == ["a", "b"]             # order held despite A being slow


def test_disjoint_mutates_are_not_chained():
    class Wide(_Primary):
        mutable_slots = ("shared", "other")
        def run_sync(self, ctx): return StageResult(slots={"shared": [], "other": []})

    async def run_x(self, ctx): return StageResult()
    x = _mk_mutate("X", 10, "+x", run_x)
    y = type("Y", (Stage,), {
        "name": "Y", "modality": "m", "kind": "mutate", "order": 20,
        "writes": ("other",),                    # disjoint from X's ('shared',)
        "version_fragment": lambda self, s: "+y",
        "run_async": run_x,
    })()
    resolved = resolve("m", [Wide(), x, y], None)
    assert "X" not in resolved.needs["Y"]        # no chain edge — free to run concurrently


def test_enabled_mutate_without_writes_is_resolution_error():
    bad = type("NoWrites", (Stage,), {
        "name": "nw", "modality": "m", "kind": "mutate", "order": 10,
        "version_fragment": lambda self, s: "+nw",
        "run_sync": lambda self, ctx: StageResult(),
    })()
    with pytest.raises(GraphResolutionError, match="must declare `writes`"):
        resolve("m", [_Primary(), bad], None)


def test_mutate_writes_outside_primary_mutables_is_resolution_error():
    bad = type("Rogue", (Stage,), {
        "name": "rogue", "modality": "m", "kind": "mutate", "order": 10,
        "writes": ("not_offered",),
        "version_fragment": lambda self, s: "+r",
        "run_sync": lambda self, ctx: StageResult(),
    })()
    with pytest.raises(GraphResolutionError, match="not in primary"):
        resolve("m", [_Primary(), bad], None)


def test_explicit_needs_contradicting_chain_order_is_a_cycle_error():
    """order says A(10) then B(20); A explicitly needing B contradicts the chain —
    loud error, never a silent reorder."""
    async def run(self, ctx): return StageResult()
    a = type("A", (Stage,), {
        "name": "A", "modality": "m", "kind": "mutate", "order": 10,
        "writes": ("shared",), "needs": ("B",),
        "version_fragment": lambda self, s: "+a", "run_async": run,
    })()
    b = _mk_mutate("B", 20, "+b", run)
    with pytest.raises(GraphResolutionError, match="cycle"):
        resolve("m", [_Primary(), a, b], None)


def test_duplicate_provides_is_resolution_error():
    class S1(Stage):
        name = "s1"; modality = "m"; kind = "sidecar"; order = 10; provides = ("dup",)
        def run_sync(self, ctx): return StageResult(slots={"dup": 1})

    class S2(Stage):
        name = "s2"; modality = "m"; kind = "sidecar"; order = 20; provides = ("dup",)
        def run_sync(self, ctx): return StageResult(slots={"dup": 2})

    with pytest.raises(GraphResolutionError, match="both\\s+declare provides"):
        resolve("m", [_Primary(), S1(), S2()], None)


# ---- resolution errors ---------------------------------------------------------

def test_resolution_requires_exactly_one_primary():
    class P2(_Primary):
        name = "primary2"; order = 99
    with pytest.raises(GraphResolutionError, match="one enabled primary"):
        resolve("m", [_Primary(), P2()], None)


def test_required_stage_needing_disabled_is_error():
    class Off(Stage):
        name = "off"; modality = "m"; kind = "sidecar"; order = 10
        def enabled(self, settings): return False
        def run_sync(self, ctx): return StageResult()

    class NeedsOff(Stage):
        name = "needsoff"; modality = "m"; kind = "sidecar"; needs = ("off",); order = 20
        def run_sync(self, ctx): return StageResult()

    with pytest.raises(GraphResolutionError, match="needs disabled"):
        resolve("m", [_Primary(), Off(), NeedsOff()], None)


def test_required_downstream_of_best_effort_is_error():
    class BE(Stage):
        name = "be"; modality = "m"; kind = "sidecar"; policy = "best_effort"; order = 10
        def run_sync(self, ctx): return StageResult()

    class Req(Stage):
        name = "req"; modality = "m"; kind = "sidecar"; needs = ("be",); order = 20  # required
        def run_sync(self, ctx): return StageResult()

    with pytest.raises(GraphResolutionError, match="hollow"):
        resolve("m", [_Primary(), BE(), Req()], None)


# ---- registration guardrails (throwaway modality, cleaned up) ------------------

@pytest.fixture()
def clean_reg():
    yield
    stage_mod._REGISTRY.pop("zz", None)


def _reg(**attrs):
    body = {"modality": "zz", **attrs}
    return register_stage(type(attrs.get("name", "S"), (Stage,), body))


def test_registration_rejects_mutate_with_enabled_override(clean_reg):
    with pytest.raises(StageRegistrationError, match="must NOT override enabled"):
        register_stage(type("Bad", (Stage,), {
            "name": "b", "modality": "zz", "kind": "mutate", "order": 0,
            "version_fragment": lambda self, s: "+t",
            "enabled": lambda self, s: True,
            "run_sync": lambda self, ctx: StageResult(),
        }))


def test_registration_rejects_best_effort_primary(clean_reg):
    with pytest.raises(StageRegistrationError, match="cannot be best_effort"):
        register_stage(type("Bad", (Stage,), {
            "name": "b", "modality": "zz", "kind": "primary", "policy": "best_effort",
            "order": 0, "version_fragment": lambda self, s: "v",
            "run_sync": lambda self, ctx: StageResult(),
            "assemble": lambda self, ctx: [],
        }))


def test_registration_requires_exactly_one_run_method(clean_reg):
    with pytest.raises(StageRegistrationError, match="exactly one of run_sync"):
        register_stage(type("Bad", (Stage,), {
            "name": "b", "modality": "zz", "kind": "sidecar", "order": 0,
        }))  # neither run_sync nor run_async defined


def test_registration_requires_writes_on_mutate(clean_reg):
    with pytest.raises(StageRegistrationError, match="must declare `writes`"):
        register_stage(type("Bad", (Stage,), {
            "name": "b", "modality": "zz", "kind": "mutate", "order": 0,
            "version_fragment": lambda self, s: "+t",
            "run_sync": lambda self, ctx: StageResult(),
        }))


def test_registration_rejects_writes_on_non_mutate(clean_reg):
    with pytest.raises(StageRegistrationError, match="only mutate stages declare"):
        register_stage(type("Bad", (Stage,), {
            "name": "b", "modality": "zz", "kind": "sidecar", "order": 0,
            "writes": ("x",),
            "run_sync": lambda self, ctx: StageResult(),
        }))


def test_registration_rejects_duplicate_order(clean_reg):
    register_stage(type("A", (Stage,), {
        "name": "a", "modality": "zz", "kind": "sidecar", "order": 5,
        "run_sync": lambda self, ctx: StageResult()}))
    with pytest.raises(StageRegistrationError, match="order 5 already used"):
        register_stage(type("B", (Stage,), {
            "name": "b", "modality": "zz", "kind": "sidecar", "order": 5,
            "run_sync": lambda self, ctx: StageResult()}))
