#!/usr/bin/env python
"""Reproduce a reference run's ENTIRE published decay matrix with our eval path.

The eval path is currently validated on one cell: the reference's final adapter,
scored through our code, returned its exact golden value (0.45 on day 21). One
cell is a spot check. Every reference run stores a per-night adapter snapshot, so
the whole matrix is reproducible — 21 cells for a six-night chain — and that turns
"our eval agrees on the case we checked" into "our eval agrees everywhere it can
be checked".

Eval only: no training, no adapter is written. Load snapshot t, answer the probes
for every day consolidated through t, move on.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.morpheus.eval import (PROBES_PER_DAY, TRAP_ANSWER_TOKENS, TRAPS_LIMIT,  # noqa: E402
                               Predictions, answer_suite, day_suite, traps_suite)
from app.morpheus.probes import QA_SUITE, TRAPS_SUITE, day_pool, load_suite  # noqa: E402
from app.morpheus.train import LifeAdapter                     # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", default="/home/ubuntu/engram/results/phased/repro_replay_f30")
    ap.add_argument("--days", default="5,9,12,13,17,21")
    ap.add_argument("--base-model", default="Qwen/Qwen3-VL-8B-Instruct")
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--probes-dir", default="/home/ubuntu/engram/data/probes_merged")
    ap.add_argument("--out", default="var/diag/decaymatrix")
    args = ap.parse_args()

    days = [int(d) for d in args.days.split(",")]
    run_dir = Path(args.run_dir)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    qa = load_suite(args.probes_dir, QA_SUITE)
    probes = {d: day_pool(qa, d, PROBES_PER_DAY) for d in days}
    traps = load_suite(args.probes_dir, TRAPS_SUITE)[:TRAPS_LIMIT]

    started = time.time()
    with Predictions(out / "preds.jsonl") as preds:
        for step, day in enumerate(days):
            snapshot = run_dir / f"adapter_s{step}_d{day}"
            if not snapshot.is_dir():
                print(f"[skip] no snapshot at {snapshot}", flush=True)
                continue
            adapter = LifeAdapter.open(base_model=args.base_model, device=args.device,
                                       resume_adapter=snapshot)
            for seen in days[:step + 1]:            # the column: every day written so far
                answer_suite(adapter, probes[seen], day_suite(step, seen), preds)
            answer_suite(adapter, traps, traps_suite(step), preds,
                         max_new_tokens=TRAP_ANSWER_TOKENS)
            print(f"== step {step} (day {day}) evaluated "
                  f"[{(time.time() - started) / 60:.0f}m]", flush=True)
            del adapter
            import gc

            import torch
            gc.collect(); torch.cuda.empty_cache()

    meta = {"run_dir": str(run_dir), "days": days, "base_model": args.base_model,
            "minutes": round((time.time() - started) / 60, 1),
            "compare_against": str(run_dir / "judge.json")}
    (out / "meta.json").write_text(json.dumps(meta, indent=1))
    print(json.dumps(meta), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
