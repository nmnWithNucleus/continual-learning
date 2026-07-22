"""Pre-publish eval gate — the service-owned VERDICT over backend-supplied scores.

Skeleton of the research design's 6-check nightly gate. v0 wires the three
checks the mock backend can score (new-day recall floor, traps floor, heldout
contamination ceiling) plus the probe-count floor; the remaining checks
(decay spot-check, general-ability canary, read-skill canary) are declared as
SKIPPED — visible in every report, so nobody mistakes a 3-check pass for a
6-check pass. No green, no publish.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .backends.base import EvalScores
from .recipe import Recipe

_NOT_WIRED = ("decay_spot_check", "general_canary", "read_skill_canary")


@dataclass(frozen=True)
class GateReport:
    passed: bool
    checks: dict[str, bool]
    reasons: list[str]
    skipped: tuple[str, ...] = field(default=_NOT_WIRED)
    scores: dict | None = None


def run_gate(scores: EvalScores, recipe: Recipe) -> GateReport:
    checks: dict[str, bool] = {}
    reasons: list[str] = []

    checks["min_probes"] = scores.n_probes >= recipe.min_probes
    if not checks["min_probes"]:
        reasons.append(f"probe count {scores.n_probes} < floor {recipe.min_probes}")

    checks["new_day_recall"] = scores.new_day_recall >= recipe.new_day_recall_min
    if not checks["new_day_recall"]:
        reasons.append(f"new-day recall {scores.new_day_recall:.3f} "
                       f"< {recipe.new_day_recall_min}")

    checks["traps"] = scores.traps_pass >= recipe.traps_pass_min
    if not checks["traps"]:
        reasons.append(f"trap pass-rate {scores.traps_pass:.3f} < {recipe.traps_pass_min}")

    checks["heldout"] = scores.heldout_recall <= recipe.heldout_recall_max
    if not checks["heldout"]:
        reasons.append(f"heldout recall {scores.heldout_recall:.3f} "
                       f"> ceiling {recipe.heldout_recall_max} (contamination tripwire)")

    return GateReport(passed=all(checks.values()), checks=checks, reasons=reasons,
                      scores={"new_day_recall": scores.new_day_recall,
                              "traps_pass": scores.traps_pass,
                              "heldout_recall": scores.heldout_recall,
                              "n_probes": scores.n_probes, **scores.extras})
