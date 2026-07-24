#!/usr/bin/env python
"""Ensemble verdict at the expanded n: our chains vs the reference chains.

The overnight run took both sides past the original n=3-vs-4. The calibrated test
is unchanged — an exact two-sided permutation test on run-level seen-mean (the
min-max band was retired because the reference fails it half the time by
construction). This just re-runs it over whatever chains have judged.

Also reports the seq-arm control: reproducing the reference's rehearsal-OFF
FAILURE is what shows the harness detects regressions rather than only agreeing
with good runs. Everything is read from judged summaries — no GPU, no judging.
"""
from __future__ import annotations

import argparse
import json
import sys
from itertools import combinations
from pathlib import Path
from statistics import mean, pstdev

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DAYS = [5, 9, 12, 13, 17, 21]
PHASED = Path("/home/ubuntu/engram/results/phased")


def seen_mean(run_dir: Path) -> float | None:
    jp = run_dir / "judge.json"
    if not jp.exists():
        return None
    j = json.loads(jp.read_text())
    vals = [j[f"s5_d{d}"]["judge_exact"] for d in DAYS if f"s5_d{d}" in j]
    return mean(vals) if len(vals) == len(DAYS) else None


def permutation_p(ours: list[float], theirs: list[float]) -> float:
    pool = ours + theirs
    k = len(ours)
    observed = abs(mean(theirs) - mean(ours))
    splits = list(combinations(range(len(pool)), k))
    extreme = sum(1 for s in splits
                  if abs(mean([pool[i] for i in range(len(pool)) if i not in s])
                         - mean([pool[i] for i in s])) >= observed - 1e-12)
    return extreme / len(splits)


def collect(label: str, dirs: list[Path]) -> dict:
    runs = {d.name: seen_mean(d) for d in dirs}
    done = {k: v for k, v in runs.items() if v is not None}
    return {"label": label, "runs": runs, "seen_means": sorted(done.values()),
            "n": len(done), "pending": [k for k, v in runs.items() if v is None]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="var/diag/ensemble_report.json")
    args = ap.parse_args()

    reference = collect("reference", [
        PHASED / "replay_f30", PHASED / "replay_f30_s1", PHASED / "replay_f30_s2",
        PHASED / "repro_replay_f30",
        *sorted((Path("var/diag/refchains")).glob("ref_s*"))])
    ours = collect("ours", [
        Path("var/parity/morpheus_f30_s0"), Path("var/parity/morpheus_f30_s1"),
        Path("var/parity/morpheus_f30_s2"),
        *sorted((Path("var/diag/ourchains")).glob("morpheus_s*"))])
    seq = collect("seq-control", sorted(Path("var/diag/seqchains").glob("seq_s*")))

    print(f"reference: n={reference['n']}  seen-means {[round(x,3) for x in reference['seen_means']]}"
          f"  mean {mean(reference['seen_means']):.4f} sd {pstdev(reference['seen_means']):.4f}")
    print(f"ours:      n={ours['n']}  seen-means {[round(x,3) for x in ours['seen_means']]}"
          f"  mean {mean(ours['seen_means']):.4f} sd {pstdev(ours['seen_means']):.4f}")
    out = {"reference": reference, "ours": ours, "seq_control": seq}

    if ours["n"] >= 2 and reference["n"] >= 2:
        p = permutation_p(ours["seen_means"], reference["seen_means"])
        out["permutation_test"] = {
            "p_value": round(p, 4), "ours_mean": round(mean(ours["seen_means"]), 4),
            "reference_mean": round(mean(reference["seen_means"]), 4),
            "same_distribution": p > 0.05}
        print(f"\nexact permutation test on seen-mean: p = {p:.4f}  "
              f"({'same distribution' if p > 0.05 else 'DISTINGUISHABLE'})")
        print(f"our spread sd {pstdev(ours['seen_means']):.4f} vs reference "
              f"{pstdev(reference['seen_means']):.4f}")

    if seq["n"]:
        # Reproduce the FAILURE: seq should collapse (reference day-5 -> 0.00).
        print(f"\nseq-arm control (rehearsal OFF): n={seq['n']} "
              f"seen-means {[round(x,3) for x in seq['seen_means']]}")
        print("  reference seq final seen-mean is ~0.13; a low value here reproduces the "
              "known failure and shows the harness detects regressions.")
    if reference["pending"] or ours["pending"] or seq["pending"]:
        print(f"\npending judges: ref={reference['pending']} ours={ours['pending']} "
              f"seq={seq['pending']}")
    Path(args.out).write_text(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
