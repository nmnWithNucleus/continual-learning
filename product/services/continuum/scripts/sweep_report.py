#!/usr/bin/env python
"""Judge the fixed-start draws and report the single-night outcome distribution.

Every draw starts from the same pinned adapter and differs only in the rehearsal
seed, so the spread across draws IS the single-night contribution — with
accumulation across nights held out. Two anchors give it meaning:

    0.45   the reference's own day-21 recall from this starting point
    0.15   what our full six-night chain (seed 0) reached

If the draws span both, seed 0 was a chain that drew badly rather than a port
that trains badly. If they cluster high and 0.15 is unreachable, something about
running six nights in sequence is the problem, not any single night.

Runs in the judge env (litellm + Vertex).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import mean, median, pstdev

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.morpheus.judge import JudgeConfig, judge_items, summarize   # noqa: E402

REFERENCE_D21 = 0.45      # reference, same starting adapter
OUR_CHAIN_D21 = 0.15      # our seed-0 full chain


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sweep-dir", default="var/diag/sweep")
    ap.add_argument("--out", default="var/diag/sweep_report.json")
    args = ap.parse_args()

    from app.config import get_settings
    settings = get_settings().morpheus
    cfg = JudgeConfig(model=settings.judge_model, project=settings.vertex_project,
                      location=settings.vertex_location, workers=settings.judge_workers)

    draws = {}
    for path in sorted(Path(args.sweep_dir).glob("draw_*.jsonl")):
        seed = int(path.stem.split("_")[1])
        cached = path.with_suffix(".judged.json")
        if cached.exists():
            draws[seed] = json.loads(cached.read_text())
            continue
        rows = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
        if not rows:
            continue
        summary = summarize(rows, judge_items(rows, cfg), label=f"draw{seed}")
        meta = json.loads(path.with_suffix(".meta.json").read_text()) \
            if path.with_suffix(".meta.json").exists() else {}
        draws[seed] = {"d21": summary.get(f"draw{seed}_d21", {}).get("judge_exact"),
                       "d5": summary.get(f"draw{seed}_d5", {}).get("judge_exact"),
                       "minutes": meta.get("minutes"), "loss_last": meta.get("loss_last"),
                       "replay_chars": meta.get("replay_chars")}
        cached.write_text(json.dumps(draws[seed], indent=1))
        print(f"judged draw {seed}: {draws[seed]}", flush=True)

    if not draws:
        raise SystemExit("no completed draws yet")
    d21 = [v["d21"] for v in draws.values() if v.get("d21") is not None]
    d5 = [v["d5"] for v in draws.values() if v.get("d5") is not None]

    print(f"\nFIXED-START DRAWS (n={len(d21)}) — same adapter, same night, rehearsal seed varied")
    print(f"{'seed':>6}{'day21':>9}{'day5':>9}{'loss_last':>11}{'min':>7}")
    for seed in sorted(draws):
        v = draws[seed]
        print(f"{seed:>6}{(v.get('d21') or float('nan')):>9.4f}{(v.get('d5') or float('nan')):>9.4f}"
              f"{(v.get('loss_last') or float('nan')):>11.3f}{(v.get('minutes') or 0):>7.0f}")
    for name, xs in (("day 21 (just written)", d21), ("day 5 (retention canary)", d5)):
        if not xs:
            continue
        print(f"\n  {name}: n={len(xs)} mean {mean(xs):.4f} median {median(xs):.4f} "
              f"sd {pstdev(xs):.4f} min {min(xs):.4f} max {max(xs):.4f}")
    verdict = {
        "n_draws": len(d21), "d21": {"values": sorted(d21), "mean": round(mean(d21), 4),
                                     "sd": round(pstdev(d21), 4), "min": min(d21), "max": max(d21)},
        "reference_d21": REFERENCE_D21, "our_chain_d21": OUR_CHAIN_D21,
        "reference_inside_draw_range": min(d21) <= REFERENCE_D21 <= max(d21),
        "chain_result_inside_draw_range": min(d21) <= OUR_CHAIN_D21 <= max(d21),
    }
    if d5:
        verdict["d5"] = {"values": sorted(d5), "mean": round(mean(d5), 4),
                         "sd": round(pstdev(d5), 4)}
    print(f"\n  reference 0.45 inside our draw range: {verdict['reference_inside_draw_range']}")
    print(f"  our chain's 0.15 reachable in ONE night: {verdict['chain_result_inside_draw_range']}")
    print("\n  reading: a single night alone spans this much; anything beyond it in the full")
    print("  chain is accumulation across nights, not one night's training.")
    Path(args.out).write_text(json.dumps(verdict, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
