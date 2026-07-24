#!/usr/bin/env python
"""Phase-3b, step 4: the Arm-1 table — did seen-vs-heldout separation SURVIVE?

One question, one table. The Phase-3 chains (rule-bent day-log, built by the real
recording -> DP -> storage -> continuum path) go beside the 5-min parity baseline they
must be read against, and beside the rehearsal-off negative control that says what a
BROKEN consolidation looks like on this same probe set.

The verdict rule is a band, not a point, and it was fixed before the run:

  * separation SURVIVES if the Phase-3 mean clears the negative control's CEILING (its
    max, the conservative bar) -- at or under it, the run is indistinguishable from a
    chain that never rehearsed and therefore never consolidated;
  * it is UNCHANGED if it also sits inside the 5-min baseline's own spread;
  * it is WEAKENED if it clears the control but sits below that spread.

Deliberately NOT a pass/fail gate on landing inside the baseline band: the baseline's
own runs satisfy their min-max envelope only about half the time (eval.Band's docstring
says so), and the product path differs from the parity path in several coupled ways at
once. The number that matters is whether the SIGNAL is still there.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.morpheus.eval import Readout, ensemble_table, readout, same_distribution  # noqa: E402

DEFAULT_DAYS = [5, 9, 12, 13, 17, 21]


def load(path: Path, days: list[int]) -> Readout | None:
    judged = path / "judge.json"
    if not judged.exists():
        return None
    report = path / "train_report.json"
    chain_days = days
    if report.exists():
        chain_days = json.loads(report.read_text()).get("days", days)
    preds = path / "preds.jsonl"
    predictions = [json.loads(x) for x in preds.read_text().splitlines() if x.strip()] \
        if preds.exists() else []
    return readout(json.loads(judged.read_text()), chain_days, label=path.name,
                   predictions=predictions)


def expand(pattern: str) -> list[Path]:
    """Absolute or relative, glob or plain path — run dirs are named by hand."""
    path = Path(pattern).expanduser()
    if any(ch in pattern for ch in "*?["):
        root = Path(path.anchor) if path.is_absolute() else Path()
        return sorted(root.glob(str(path.relative_to(path.anchor)) if path.is_absolute()
                                else str(path)))
    return [path]


def collect(patterns: list[str], days: list[int]) -> list[Readout]:
    out = []
    for pattern in patterns:
        for path in expand(pattern):
            row = load(path, days)
            if row is not None:
                out.append(row)
    return out


def spread(readouts: list[Readout], attr: str) -> dict:
    values = [getattr(r, attr) for r in readouts if getattr(r, attr) is not None]
    if not values:
        return {}
    return {"n": len(values), "mean": round(statistics.mean(values), 4),
            "sd": round(statistics.stdev(values), 4) if len(values) > 1 else 0.0,
            "min": round(min(values), 4), "max": round(max(values), 4)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", nargs="+", required=True,
                    help="Phase-3 chain dirs (or globs)")
    ap.add_argument("--baseline", nargs="+",
                    default=["var/parity/morpheus_f30_s*", "var/diag/ourchains/morpheus_s*"],
                    help="the 5-min parity chains this is read against")
    ap.add_argument("--control", nargs="+",
                    default=["var/parity/seq", "var/parity/seq_s1", "var/parity/seq_s2"],
                    help="rehearsal-off chains: what a broken consolidation scores")
    ap.add_argument("--days", default="5,9,12,13,17,21")
    ap.add_argument("--out", default="~/phase3/reports/arm1.json")
    args = ap.parse_args()

    days = [int(d) for d in args.days.split(",")]
    ours = collect(args.runs, days)
    if not ours:
        raise SystemExit(f"no finished Phase-3 chains in {args.runs} (no judge.json)")
    baseline = collect(args.baseline, days)
    control = collect(args.control, days)

    print("PHASE 3 — ARM 1 (rule-bent 1-min day-log, real recording -> DP -> storage)")
    print(ensemble_table(ours))
    if baseline:
        print("\n5-MIN PARITY BASELINE (same probes, same recipe knobs, 5-min blocks)")
        print(ensemble_table(baseline))
    if control:
        print("\nNEGATIVE CONTROL (rehearsal off — what no consolidation looks like)")
        print(ensemble_table(control))

    ours_sep, base_sep = spread(ours, "separation"), spread(baseline, "separation")
    control_sep = spread(control, "separation")
    ceiling = control_sep.get("max")
    verdict = "INDETERMINATE"
    if ceiling is not None:
        if ours_sep["mean"] > ceiling:
            inside = base_sep and base_sep["min"] <= ours_sep["mean"] <= base_sep["max"]
            verdict = "SURVIVED" if inside else "SURVIVED (weakened)"
        else:
            verdict = "COLLAPSED"

    summary = {
        "verdict": verdict,
        "separation": {"phase3": ours_sep, "baseline_5min": base_sep,
                       "control_rehearsal_off": control_sep},
        "seen_mean": {"phase3": spread(ours, "seen_mean"),
                      "baseline_5min": spread(baseline, "seen_mean")},
        "heldout": {"phase3": spread(ours, "heldout"),
                    "baseline_5min": spread(baseline, "heldout")},
        "runs": [r.as_row() for r in ours],
        "days": days,
    }
    if baseline and len(ours) > 1:
        summary["same_distribution_vs_baseline"] = same_distribution(
            [r.separation for r in ours if r.separation is not None],
            [r.separation for r in baseline if r.separation is not None])

    print("\nSEPARATION (seen_mean - final heldout)")
    for name, row in summary["separation"].items():
        if row:
            print(f"  {name:<24} n={row['n']}  mean {row['mean']:.4f}  "
                  f"sd {row['sd']:.4f}  range {row['min']:.4f}..{row['max']:.4f}")
    print(f"\nVERDICT: separation {verdict}")

    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=1))
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
