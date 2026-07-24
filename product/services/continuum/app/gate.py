"""Pre-publish eval gate — the service-owned VERDICT over backend-supplied scores.

Skeleton of the research design's 6-check nightly gate. v0 wires four checks
(new-day recall floor, traps floor, heldout contamination, probe-count floor);
the remaining ones (decay spot-check, general-ability canary, read-skill canary)
are declared SKIPPED — visible in every report, so nobody mistakes a 4-check pass
for a 6-check pass. No green, no publish.

Thresholds come from the GATE POLICY, never from the training recipe: what we are
willing to ship is a different decision, on a different clock, from what we train.
See app/policy.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .backends.base import EvalScores
from .policy import GatePolicy, heldout_p_value

_NOT_WIRED = ("decay_spot_check", "general_canary", "read_skill_canary")


@dataclass(frozen=True)
class GateReport:
    passed: bool
    checks: dict[str, bool]
    reasons: list[str]
    skipped: tuple[str, ...] = field(default=_NOT_WIRED)
    scores: dict | None = None
    policy_id: str = ""


def run_gate(scores: EvalScores, policy: GatePolicy) -> GateReport:
    checks: dict[str, bool] = {}
    reasons: list[str] = []

    checks["min_probes"] = scores.n_probes >= policy.min_probes
    if not checks["min_probes"]:
        reasons.append(f"probe count {scores.n_probes} < floor {policy.min_probes}")

    checks["new_day_recall"] = scores.new_day_recall >= policy.new_day_recall_min
    if not checks["new_day_recall"]:
        reasons.append(f"new-day recall {scores.new_day_recall:.3f} "
                       f"< {policy.new_day_recall_min}")

    checks["traps"] = scores.traps_pass >= policy.traps_pass_min
    if not checks["traps"]:
        reasons.append(f"trap pass-rate {scores.traps_pass:.3f} < {policy.traps_pass_min}")

    # Contamination: is heldout recall distinguishable from this run's OWN base
    # model? A fixed ceiling cannot tell a leak from a base that already knew the
    # answer, and it stops meaning anything the day the base model changes.
    p_value = heldout_p_value(scores.heldout_hits, scores.heldout_n,
                              scores.base_heldout_hits, scores.base_heldout_n)
    above_base = p_value < policy.heldout_alpha
    over_backstop = scores.heldout_recall > policy.heldout_backstop
    checks["heldout"] = not (above_base or over_backstop)
    if above_base:
        reasons.append(
            f"heldout recall {scores.heldout_hits}/{scores.heldout_n} exceeds the base "
            f"control {scores.base_heldout_hits}/{scores.base_heldout_n} "
            f"(p={p_value:.4f} < {policy.heldout_alpha}) — contamination tripwire")
    if over_backstop:
        reasons.append(f"heldout recall {scores.heldout_recall:.3f} over the absolute "
                       f"backstop {policy.heldout_backstop}")
    if scores.heldout_n and scores.heldout_n < policy.heldout_probes:
        # Not a failure — a caveat that must travel with the verdict, because a
        # small suite makes this check weak rather than wrong.
        reasons.append(f"NOTE heldout ran {scores.heldout_n} probes, policy expects "
                       f"{policy.heldout_probes} — the contamination check is underpowered")

    return GateReport(passed=all(checks.values()), checks=checks, reasons=reasons,
                      policy_id=policy.policy_id,
                      scores={"new_day_recall": scores.new_day_recall,
                              "traps_pass": scores.traps_pass,
                              "heldout_recall": scores.heldout_recall,
                              "heldout": f"{scores.heldout_hits}/{scores.heldout_n}",
                              "base_heldout": f"{scores.base_heldout_hits}/{scores.base_heldout_n}",
                              "heldout_p_value": round(p_value, 5),
                              "n_probes": scores.n_probes, **scores.extras})
