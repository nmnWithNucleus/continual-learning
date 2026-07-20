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
    SKIPPED, Stage, StageContext, StageRegistrationError, StageResult, register_stage,
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

def test_pipeline_version_composes_sorted_fragments():
    class Diar(Stage):
        name = "diar"; modality = "m"; kind = "mutate"; needs = ("primary",); order = 10
        def version_fragment(self, settings): return "+diar"
        def run_sync(self, ctx): return StageResult()

    class Z(Stage):  # sidecar with a fragment, out of order to prove sorting
        name = "z"; modality = "m"; kind = "sidecar"; order = 5
        def version_fragment(self, settings): return "+aaa"
        def run_sync(self, ctx): return StageResult()

    resolved = resolve("m", [_Primary(), Diar(), Z()], None)
    assert resolved.pipeline_version == "base-v0+aaa+diar"  # base + sorted(fragments)

    # A disabled mutate contributes nothing (its enabledness IS its fragment).
    class OffDiar(Stage):
        name = "diar"; modality = "m"; kind = "mutate"; needs = ("primary",); order = 10
        def version_fragment(self, settings): return ""   # off
        def run_sync(self, ctx): return StageResult()

    assert resolve("m", [_Primary(), OffDiar()], None).pipeline_version == "base-v0"


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


def test_commit_on_success_only():
    class Good(Stage):
        name = "good"; modality = "m"; kind = "sidecar"; policy = "best_effort"; order = 10
        provides = ("k",)
        def run_sync(self, ctx): return StageResult(slots={"k": "v"})

    class Bad(Stage):
        name = "bad"; modality = "m"; kind = "sidecar"; policy = "best_effort"; order = 20
        provides = ("j",)
        def run_sync(self, ctx):
            ctx.slots["leaked"] = True   # a partial write BEFORE raising
            raise ValueError("x")

    resolved = resolve("m", [_Primary(), Good(), Bad()], None)
    ctx = _ctx()
    asyncio.run(run_graph(resolved, ctx))
    assert ctx.slots.get("k") == "v"          # succeeded stage committed
    assert "j" not in ctx.slots               # failed stage's provides absent
    # (a direct ctx.slots mutation still lands — that's why the fingerprint guard exists
    #  for the mutable primary slots; non-mutable keys are the stage's own business.)


# ---- guards --------------------------------------------------------------------

def test_duplicate_discriminator_is_terminal():
    class Dup(Stage):
        name = "dup"; modality = "m"; kind = "sidecar"; order = 10
        def run_sync(self, ctx): return StageResult(units=[_unit("")])  # collides w/ primary

    with pytest.raises(RuntimeError, match="duplicate discriminator"):
        _run([_Primary(), Dup()])


def test_sidecar_mutating_primary_slot_is_caught():
    class Sneak(Stage):
        name = "sneak"; modality = "m"; kind = "sidecar"; needs = ("primary",); order = 10
        async def run_async(self, ctx):
            await asyncio.sleep(0)          # let the fingerprint snapshot settle first
            ctx.slots["shared"].append(1)  # illegal: mutate the primary's mutable slot
            return StageResult()

    with pytest.raises(RuntimeError, match="mutable_slots changed"):
        _run([_Primary(), Sneak()])


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


def test_registration_rejects_duplicate_order(clean_reg):
    register_stage(type("A", (Stage,), {
        "name": "a", "modality": "zz", "kind": "sidecar", "order": 5,
        "run_sync": lambda self, ctx: StageResult()}))
    with pytest.raises(StageRegistrationError, match="order 5 already used"):
        register_stage(type("B", (Stage,), {
            "name": "b", "modality": "zz", "kind": "sidecar", "order": 5,
            "run_sync": lambda self, ctx: StageResult()}))
