#!/usr/bin/env python
"""Re-score every run under the ratified gate policy, with a positive control.

Two questions, and both have to be answered or the policy is not calibrated:

  Does it admit good adapters?  The reference recipe's own runs must pass. The
                                previous policy blocked 71% of their nights, which
                                is how a gate ends up measuring the recipe rather
                                than the artifact.
  Does it still block bad ones?  The 40%-neg-boost arm (`h12_calib`) is the
                                positive control: a real lobotomy, recall collapsed
                                to 0.021, on disk. **A gate that passes everything
                                is exactly as broken as one that blocks everything.**

Offline: reads stored predictions and judged summaries, no GPU and no judge calls.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.backends.base import EvalScores                      # noqa: E402
from app.gate import run_gate                                 # noqa: E402
from app.morpheus.eval import trap_rates                      # noqa: E402
from app.policy import load_policy                            # noqa: E402

PHASED = Path("/home/ubuntu/engram/results/phased")
DEFAULT_RUNS = {
    "ref replay_f30": PHASED / "replay_f30",
    "ref replay_f30_s1": PHASED / "replay_f30_s1",
    "ref replay_f30_s2": PHASED / "replay_f30_s2",
    "ref repro (phase 1)": PHASED / "repro_replay_f30",
    "ours morpheus_s0": Path("var/parity/morpheus_f30_s0"),
    "ours morpheus_s1": Path("var/parity/morpheus_f30_s1"),
    "ours morpheus_s2": Path("var/parity/morpheus_f30_s2"),
}
# The positive control: 40% neg-boost. Denies its way to a clean trap score while
# recall collapses — the exact failure a calibration gate must not wave through.
CONTROL = {"CONTROL h12_calib (lobotomy)": PHASED / "h12_calib_s0"}


def scores_for(run_dir: Path) -> EvalScores | None:
    judge_path = run_dir / "judge.json"
    preds_path = run_dir / "preds.jsonl"
    if not (judge_path.exists() and preds_path.exists()):
        return None
    judged = json.loads(judge_path.read_text())
    preds = [json.loads(x) for x in preds_path.read_text().splitlines() if x.strip()]
    days = sorted({int(k.split("_d")[1]) for k in judged
                   if k.startswith("s") and "_d" in k and k.split("_d")[1].isdigit()})
    last = max(int(k[1:].split("_")[0]) for k in judged if k.startswith("s") and "_d" in k)

    final = [judged[f"s{last}_d{d}"]["judge_exact"] for d in days
             if f"s{last}_d{d}" in judged]
    held = judged.get("final_heldout", {})
    base = judged.get("base_heldout", {})
    traps = trap_rates(preds, last + 1)
    return EvalScores(
        new_day_recall=mean(final) if final else 0.0,
        traps_pass=traps.get(last, 0.0),
        heldout_hits=round(held.get("judge_exact", 0.0) * held.get("n", 0)),
        heldout_n=held.get("n", 0),
        base_heldout_hits=round(base.get("judge_exact", 0.0) * base.get("n", 0)),
        base_heldout_n=base.get("n", 0),
        n_probes=len(preds),
        extras={"days": len(days)})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--policy", default="policies/gate-policy-v1.1.json")
    ap.add_argument("--out", default="var/diag/gate_rescore.json")
    args = ap.parse_args()

    policy = load_policy(args.policy)
    print(f"policy {policy.policy_id}: traps>={policy.traps_pass_min}  "
          f"recall>={policy.new_day_recall_min}  heldout: exact test vs base "
          f"alpha={policy.heldout_alpha} backstop={policy.heldout_backstop}  "
          f"min_probes={policy.min_probes}\n")

    header = f"{'run':<30}{'recall':>8}{'traps':>7}{'heldout':>10}{'base':>8}{'p':>8}  verdict"
    print(header); print("-" * len(header))
    out = {"policy_id": policy.policy_id, "runs": {}}
    for label, path in {**DEFAULT_RUNS, **CONTROL}.items():
        scores = scores_for(path)
        if scores is None:
            print(f"{label:<30}  (missing judge.json/preds.jsonl at {path})")
            continue
        report = run_gate(scores, policy)
        blocking = [r for r in report.reasons if not r.startswith("NOTE")]
        out["runs"][label] = {"passed": report.passed, "checks": report.checks,
                              "reasons": report.reasons, "scores": report.scores}
        print(f"{label:<30}{scores.new_day_recall:>8.4f}{scores.traps_pass:>7.3f}"
              f"{f'{scores.heldout_hits}/{scores.heldout_n}':>10}"
              f"{f'{scores.base_heldout_hits}/{scores.base_heldout_n}':>8}"
              f"{report.scores['heldout_p_value']:>8.3f}  "
              f"{'PASS' if report.passed else 'BLOCK: ' + '; '.join(blocking)[:60]}")

    refs = [v for k, v in out["runs"].items() if k.startswith("ref")]
    ours = [v for k, v in out["runs"].items() if k.startswith("ours")]
    control = [v for k, v in out["runs"].items() if k.startswith("CONTROL")]
    out["summary"] = {
        "reference_pass_rate": f"{sum(r['passed'] for r in refs)}/{len(refs)}",
        "ours_pass_rate": f"{sum(r['passed'] for r in ours)}/{len(ours)}",
        "control_blocked": all(not r["passed"] for r in control),
    }
    print(f"\nreference runs passing: {out['summary']['reference_pass_rate']}")
    print(f"our runs passing:       {out['summary']['ours_pass_rate']}")
    print(f"lobotomy control BLOCKED: {out['summary']['control_blocked']}"
          f"   <- a gate that passes this is not a gate")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1))
    return 0 if out["summary"]["control_blocked"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
