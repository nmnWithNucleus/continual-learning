from __future__ import annotations

from datetime import date

import pytest

from app.recipe import Recipe
from app.synth import synth_records
from app.window import window_for


@pytest.fixture
def var_dir(tmp_path, monkeypatch):
    d = tmp_path / "var"
    monkeypatch.setenv("CONTINUUM_VAR_DIR", str(d))
    monkeypatch.delenv("MOCK_GATE", raising=False)
    monkeypatch.delenv("TRAINER_BACKEND", raising=False)
    return d


@pytest.fixture
def small_recipe():
    """Real recipe SHAPE, small numbers — tests stay fast and readable."""
    return Recipe(
        recipe_id="test-recipe-v0",
        variants=4, neg_frac=0.25, ok_rate_min=0.85,
        replay_frac=0.30, replay_source="amp", replay_neg_boost=0.0,
        lora_r=8, lora_alpha=16, lr=1e-4, epochs=1, batch_size=2, chunk_tokens=256,
        objective="next-token CPT (never QA-SFT)",
        quality_min=0.5, block_segments=12, segment_seconds=10,
        new_day_recall_min=0.15, traps_pass_min=0.40, heldout_recall_max=0.05,
        decay_retention_min=0.5, min_probes=100, consecutive_fail_freeze=2,
        snapshot_retention=3, boundary_local_time="04:00",
    )


@pytest.fixture
def win():
    return window_for("u-test", date(2026, 7, 20), "America/Los_Angeles")


@pytest.fixture
def day_records(win):
    return synth_records(win, seed=7, events=30)
