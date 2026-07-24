"""The publish gate policy — SEPARATE from the training recipe, deliberately.

Two artifacts, two lifecycles:

  recipes/*.json   WHAT WE TRAIN. Parity-critical and frozen: 48x amplification,
                   15% negatives, LoRA r128/a256, 30% replay. `recipe_id` is
                   hashed into the amplify and train stage keys, so changing it
                   correctly invalidates hours of GPU work — an artifact trained
                   under recipe A is not comparable to one trained under B.

  policies/*.json  WHETHER WE SHIP IT. Tunable and expected to move as we measure
                   more nights. `policy_id` is recorded in the report and MUST NOT
                   enter a stage key.

Keeping them in one file was a real trap, not a tidiness point: a threshold edit
would fork `recipe_id`, invalidate the amplify and train caches, re-run a night of
GPU time, and imply the trained artifact had changed when only our willingness to
publish it did. Re-deciding what is good enough to ship must never re-train
anything.

Thresholds ratified by the cofounders 2026-07-24 from measured distributions
(handoff/gate-threshold-proposal.md). Every previous value blocked ~everything,
including the validated recipe's own output.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from math import comb
from pathlib import Path


@dataclass(frozen=True)
class GatePolicy:
    policy_id: str
    new_day_recall_min: float
    traps_pass_min: float
    heldout_alpha: float          # one-sided exact test vs the run's OWN base control
    heldout_backstop: float       # absolute ceiling, for a base model that is itself dirty
    heldout_probes: int           # how many heldout probes the eval must run
    min_probes: int
    decay_retention_min: float
    consecutive_fail_freeze: int
    snapshot_retention: int


def load_policy(path: str | Path) -> GatePolicy:
    raw = json.loads(Path(path).read_text())
    gate, publish = raw["gate"], raw["publish"]
    return GatePolicy(
        policy_id=raw["policy_id"],
        new_day_recall_min=float(gate["new_day_recall_min"]),
        traps_pass_min=float(gate["traps_pass_min"]),
        heldout_alpha=float(gate["heldout"]["alpha"]),
        heldout_backstop=float(gate["heldout"]["backstop"]),
        heldout_probes=int(gate["heldout"]["probes"]),
        min_probes=int(gate["min_probes"]),
        decay_retention_min=float(gate["decay_retention_min"]),
        consecutive_fail_freeze=int(gate["consecutive_fail_freeze"]),
        snapshot_retention=int(publish["snapshot_retention"]),
    )


def heldout_p_value(adapter_hits: int, adapter_n: int,
                    base_hits: int, base_n: int) -> float:
    """One-sided Fisher exact test: is the adapter's heldout recall above the base's?

    Testing against the run's OWN base control rather than a fixed number is what
    makes this robust. A constant ceiling asks "did this run score above 0.05",
    which conflates a leak with a base model that happens to know things — and
    breaks entirely the day the base model changes. This asks the question we
    actually care about: did CONSOLIDATION teach it days it never trained on.
    """
    total, successes = adapter_n + base_n, adapter_hits + base_hits
    if not successes or not total:
        return 1.0
    return sum(
        comb(adapter_n, i) * comb(base_n, successes - i) / comb(total, successes)
        for i in range(adapter_hits, min(adapter_n, successes) + 1))
