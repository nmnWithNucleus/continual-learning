"""The consolidation recipe: every knob that turns a day log into a passing adapter.

A recipe is CONFIG, NOT CODE — the versioned set of numbers the research locked
(amplification factor, negatives fraction, replay mix, LoRA shape, gate
thresholds). Whoever executes a stage reads the pinned recipe; tuning it is this
service's job and every change forks `recipe_id` (same posture as DP's
`pipeline_version`: an artifact trained under recipe A is never silently
comparable to one trained under recipe B).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Recipe:
    recipe_id: str
    # amplify
    variants: int
    neg_frac: float
    ok_rate_min: float
    # replay
    replay_frac: float
    replay_source: str        # "amp" (reservoir of amplified corpora) | "rawlog"
    replay_neg_boost: float
    # train
    lora_r: int
    lora_alpha: int
    lr: float
    epochs: int
    batch_size: int
    chunk_tokens: int
    objective: str            # "next-token CPT (never QA-SFT)" — the recipe's core invariant
    # corpus
    quality_min: float
    block_segments: int
    segment_seconds: int
    # gate
    new_day_recall_min: float
    traps_pass_min: float
    heldout_recall_max: float
    decay_retention_min: float
    min_probes: int
    consecutive_fail_freeze: int
    # publish
    snapshot_retention: int
    # window
    boundary_local_time: str  # "HH:MM" user-local consolidation boundary


def load_recipe(path: str | Path) -> Recipe:
    raw = json.loads(Path(path).read_text())
    return Recipe(
        recipe_id=raw["recipe_id"],
        variants=int(raw["amplify"]["variants"]),
        neg_frac=float(raw["amplify"]["neg_frac"]),
        ok_rate_min=float(raw["amplify"]["ok_rate_min"]),
        replay_frac=float(raw["replay"]["frac"]),
        replay_source=str(raw["replay"]["source"]),
        replay_neg_boost=float(raw["replay"]["neg_boost"]),
        lora_r=int(raw["train"]["lora_r"]),
        lora_alpha=int(raw["train"]["lora_alpha"]),
        lr=float(raw["train"]["lr"]),
        epochs=int(raw["train"]["epochs"]),
        batch_size=int(raw["train"]["batch_size"]),
        chunk_tokens=int(raw["train"]["chunk_tokens"]),
        objective=str(raw["train"]["objective"]),
        quality_min=float(raw["corpus"]["quality_min"]),
        block_segments=int(raw["corpus"]["block_segments"]),
        segment_seconds=int(raw["corpus"]["segment_seconds"]),
        new_day_recall_min=float(raw["gate"]["new_day_recall_min"]),
        traps_pass_min=float(raw["gate"]["traps_pass_min"]),
        heldout_recall_max=float(raw["gate"]["heldout_recall_max"]),
        decay_retention_min=float(raw["gate"]["decay_retention_min"]),
        min_probes=int(raw["gate"]["min_probes"]),
        consecutive_fail_freeze=int(raw["gate"]["consecutive_fail_freeze"]),
        snapshot_retention=int(raw["publish"]["snapshot_retention"]),
        boundary_local_time=str(raw["window"]["boundary_local_time"]),
    )
