"""WP-C1 — the uniform Stage + registration checks (Slot Law L4/L5 surface).

A stage is one file under ``app/stages/<modality>/`` declaring exactly:
name, modality, stage_version (vS), backend (name + vB, resolved in code), needs,
slot (default = stage name), required, byte_budget, and exactly one of
run_sync | run_async. Registration enforces the declaration hard at import:
unique slot per modality, one-of run methods, segment grammar.

The dead concepts (kinds, mutate/writes/mutable_slots, SlotView, best_effort,
policies, R1 machinery, order) must not exist on the module at all.
"""
from __future__ import annotations

import pytest

from app.stagegraph import stage as stage_mod
from app.stagegraph.stage import (
    Backend,
    Stage,
    StageOutput,
    StageRegistrationError,
    register_stage,
)


@pytest.fixture()
def clean_reg(monkeypatch):
    """An empty registry; discovery disabled so app/stages/ files are not imported."""
    monkeypatch.setattr(stage_mod, "_REGISTRY", {})
    monkeypatch.setattr(stage_mod, "_discovered", True)
    return stage_mod._REGISTRY


def _stage_cls(**overrides):
    """A minimal valid stage class; overrides replace class attributes."""
    attrs = {
        "name": "asr",
        "modality": "audio",
        "stage_version": 1,
        "backend": Backend("fw", 1),
        "byte_budget": 65536,
        "consumer": "speculative:test_fixture",
        "run_sync": lambda self, ctx: StageOutput(value={"value": "hi"}),
    }
    attrs.update(overrides)
    return type("TestStage", (Stage,), attrs)


# ---------------------------------------------------------------------------
# The uniform declaration
# ---------------------------------------------------------------------------

def test_minimal_stage_registers_and_slot_defaults_to_name(clean_reg):
    cls = _stage_cls()
    register_stage(cls)
    registered = stage_mod.stages_for("audio")
    assert [s.name for s in registered] == ["asr"]
    assert registered[0].slot_name == "asr"
    assert registered[0].required is True


def test_explicit_slot_name_is_honoured(clean_reg):
    register_stage(_stage_cls(name="diarize", slot="diarization",
                              backend=Backend("pyannote", 1)))
    (s,) = stage_mod.stages_for("audio")
    assert s.slot_name == "diarization"


def test_segment_composition():
    s = _stage_cls(name="asr", stage_version=2, backend=Backend("fw", 3))()
    assert s.segment == "asr.v2-fw.v3"


def test_segment_with_experiment_code():
    s = _stage_cls(experiment="b7")()
    assert s.segment == "asr.v1-fw.v1.exp-b7"


def test_backend_override_at_construction_names_mock_in_segment():
    """Mock backends are selected the same way real ones are — by name, in the
    version string (plan §3): a test constructs the SAME stage class with a mock
    Backend and the dialect segment says so."""
    s = _stage_cls()(backend=Backend("mock", 1))
    assert s.segment == "asr.v1-mock.v1"


# ---------------------------------------------------------------------------
# Registration checks
# ---------------------------------------------------------------------------

def test_duplicate_slot_per_modality_is_registration_error(clean_reg):
    register_stage(_stage_cls())
    with pytest.raises(StageRegistrationError, match="slot"):
        register_stage(_stage_cls(name="asr2", slot="asr",
                                  backend=Backend("other", 1)))


def test_duplicate_stage_name_is_registration_error(clean_reg):
    register_stage(_stage_cls())
    with pytest.raises(StageRegistrationError, match="duplicate"):
        register_stage(_stage_cls(byte_budget=1))


def test_same_slot_in_other_modality_is_fine(clean_reg):
    register_stage(_stage_cls())
    register_stage(_stage_cls(modality="video"))
    assert [s.name for s in stage_mod.stages_for("video")] == ["asr"]


def test_both_run_methods_is_registration_error(clean_reg):
    async def _run_async(self, ctx):  # pragma: no cover - never runs
        return StageOutput()

    with pytest.raises(StageRegistrationError, match="exactly one"):
        register_stage(_stage_cls(run_async=_run_async))


def test_neither_run_method_is_registration_error(clean_reg):
    cls = type("NoRun", (Stage,), {
        "name": "x", "modality": "audio", "stage_version": 1,
        "backend": Backend("b", 1), "byte_budget": 10,
        "consumer": "speculative:test_fixture",
    })
    with pytest.raises(StageRegistrationError, match="exactly one"):
        register_stage(cls)


@pytest.mark.parametrize("budget", [0, -1, None, 1.5, "1024"])
def test_byte_budget_must_be_positive_int(clean_reg, budget):
    with pytest.raises(StageRegistrationError, match="byte_budget"):
        register_stage(_stage_cls(byte_budget=budget))


def test_missing_backend_is_registration_error(clean_reg):
    with pytest.raises(StageRegistrationError, match="backend"):
        register_stage(_stage_cls(backend=None))


@pytest.mark.parametrize("bad", ["FW", "f-w", "", "fw.1", "fw v1"])
def test_backend_name_grammar_enforced(clean_reg, bad):
    with pytest.raises(StageRegistrationError):
        register_stage(_stage_cls(backend=Backend(bad, 1)))


@pytest.mark.parametrize("bad_version", [0, -2, None, "1", 1.0])
def test_backend_version_must_be_positive_int(clean_reg, bad_version):
    with pytest.raises(StageRegistrationError):
        register_stage(_stage_cls(backend=Backend("fw", bad_version)))


@pytest.mark.parametrize("bad", ["Asr", "a-sr", "", "asr stage", "asr.v1"])
def test_stage_name_grammar_enforced(clean_reg, bad):
    with pytest.raises(StageRegistrationError):
        register_stage(_stage_cls(name=bad))


@pytest.mark.parametrize("bad", [0, -1, None, "1"])
def test_stage_version_must_be_positive_int(clean_reg, bad):
    with pytest.raises(StageRegistrationError, match="stage_version"):
        register_stage(_stage_cls(stage_version=bad))


@pytest.mark.parametrize("bad", ["B7", "e-x", "exp code"])
def test_experiment_code_grammar_enforced(clean_reg, bad):
    with pytest.raises(StageRegistrationError, match="experiment"):
        register_stage(_stage_cls(experiment=bad))


def test_required_must_be_bool(clean_reg):
    with pytest.raises(StageRegistrationError, match="required"):
        register_stage(_stage_cls(required="yes"))


def test_self_need_is_registration_error(clean_reg):
    with pytest.raises(StageRegistrationError, match="needs"):
        register_stage(_stage_cls(needs=("asr",)))


def test_duplicate_needs_is_registration_error(clean_reg):
    with pytest.raises(StageRegistrationError, match="needs"):
        register_stage(_stage_cls(name="align", slot="transcript",
                                  needs=("asr", "asr")))


# ---------------------------------------------------------------------------
# Dead concepts stay dead (D23/D26 — deleted with their subject matter)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("dead", [
    "SlotView", "SlotAccessError", "SKIPPED", "R1_EXEMPT_SIDECARS",
    "StageResult", "StageContext_kinds", "_KINDS", "_POLICIES",
])
def test_dead_concepts_are_gone_from_module(dead):
    assert not hasattr(stage_mod, dead)


@pytest.mark.parametrize("dead_attr", [
    "kind", "policy", "provides", "mutable_slots", "writes", "order",
    "version_fragment", "enabled", "is_enabled", "assemble",
])
def test_dead_attrs_are_gone_from_stage(dead_attr):
    assert not hasattr(Stage, dead_attr)


@pytest.mark.parametrize("bad", ["", "heard", "daylog", "consumer:x", "daylog:"])
def test_consumer_declaration_is_mandatory_with_grammar(clean_reg, bad):
    """L10's ruled-in minimal form (cleanup round): a slot ships with a named
    consumer-today or an explicit speculative marker — absence or a malformed
    marker is a registration error."""
    with pytest.raises(StageRegistrationError, match="consumer"):
        register_stage(_stage_cls(consumer=bad))


@pytest.mark.parametrize("good", ["daylog:heard", "stage:speaker_align",
                                  "speculative:why not"])
def test_consumer_grammar_accepts_the_three_prefixes(clean_reg, good):
    register_stage(_stage_cls(consumer=good))
    (s,) = stage_mod.stages_for("audio")
    assert s.consumer == good
