from __future__ import annotations

import pytest

from app.policy import GatePolicy
from app.recipe import Recipe
from app.synth import synth_records
from tests._helpers import make_window


@pytest.fixture
def var_dir(tmp_path, monkeypatch):
    """A self-contained local sandbox for one test: temp var/ tree, no storage service.

    It pins `CONTINUUM_STORAGE_CLIENTS=local` EXPLICITLY rather than leaning on the
    default, and that is the point rather than boilerplate: the shipped default is
    `http` (the seam is what the D18 slice built, and it is what `scripts/seam_check.py`
    proves against the real service). A suite that runs without a storage service must
    say so out loud — if it instead inherited the production default, the day the
    default changes the suite either starts making real HTTP calls or silently proves
    something about a configuration nobody ships.
    """
    d = tmp_path / "var"
    monkeypatch.setenv("CONTINUUM_VAR_DIR", str(d))
    monkeypatch.setenv("CONTINUUM_STORAGE_CLIENTS", "local")
    monkeypatch.delenv("MOCK_GATE", raising=False)
    monkeypatch.delenv("TRAINER_BACKEND", raising=False)
    return d


@pytest.fixture
def small_recipe():
    """Real TRAINING recipe shape, small numbers — tests stay fast and readable.

    Carries no gate thresholds: those are publish policy and live separately, so a
    threshold change cannot fork recipe_id and invalidate the training caches."""
    return Recipe(
        recipe_id="test-recipe-v0",
        variants=4, neg_frac=0.25, ok_rate_min=0.85,
        replay_frac=0.30, replay_source="amp", replay_neg_boost=0.0,
        lora_r=8, lora_alpha=16, lr=1e-4, epochs=1, batch_size=2, chunk_tokens=256,
        objective="next-token CPT (never QA-SFT)",
        quality_min=0.5, block_segments=12, segment_seconds=10,
        boundary_local_time="04:00",
    )


@pytest.fixture
def small_policy():
    """Ratified gate policy at test scale — thresholds real, probe floor small."""
    return GatePolicy(
        policy_id="test-policy-v0",
        new_day_recall_min=0.15, traps_pass_min=0.15,
        heldout_alpha=0.01, heldout_backstop=0.15, heldout_probes=60,
        min_probes=100, decay_retention_min=0.5,
        consecutive_fail_freeze=2, snapshot_retention=3,
    )


@pytest.fixture
def win():
    """One window, as storage would have minted and returned it."""
    return make_window("u-test", 20, "America/Los_Angeles")


@pytest.fixture
def day_records(win):
    return synth_records(win, seed=7, events=30)
