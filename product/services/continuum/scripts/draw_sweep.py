#!/usr/bin/env python
"""One fixed-start draw: night 5 from a pinned adapter, varying only the rehearsal seed.

The full-chain result confounds two things — what a single night's training does,
and what accumulates over six of them. This pins the starting state to the
reference's `adapter_s4_d17` and varies ONLY the rehearsal draw, so the spread it
produces is the single-night contribution alone, at ~1/12 the cost of a chain.

The reference reached 0.45 on day 21 from this exact starting point. A sweep of
draws says whether that is a typical outcome or a lucky one, and whether ours
land in the same place.

Day 5 is evaluated alongside day 21 as a retention canary: it is the day with the
longest decay path, and the one whose collapse characterized our seed 0.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings                                 # noqa: E402
from app.morpheus.probes import QA_SUITE, day_pool, load_suite      # noqa: E402
from app.morpheus.replay import sample_replay                       # noqa: E402
from app.morpheus.train import CptConfig, LifeAdapter, matched_compute_budget  # noqa: E402

DAYS = [5, 9, 12, 13, 17, 21]
START_ADAPTER = "/home/ubuntu/engram/results/phased/repro_replay_f30/adapter_s4_d17"
CORPUS = "/home/ubuntu/engram/data/narrative/day{day}_x48neg.corpus.txt"


def night5_text(rehearsal_seed: int, corpora: dict[int, str]) -> str:
    """Replay the chain's rehearsal stream to night 5 under this seed.

    The draws must be consumed in chain order — night 5's selection depends on
    every draw nights 1-4 made — or this is not the same kind of sample."""
    rng = random.Random(rehearsal_seed)
    text = ""
    for step, day in enumerate(DAYS):
        if not step:
            continue
        rehearsal = sample_replay([corpora[d] for d in DAYS[:step]], frac=0.30,
                                  target_chars=len(corpora[day]), rng=rng)
        if step == len(DAYS) - 1:
            text = f"{corpora[day]}\n\n{rehearsal}"
    return text


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rehearsal-seed", type=int, required=True)
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--base-model", default="Qwen/Qwen3-VL-8B-Instruct")
    ap.add_argument("--probes-dir", default="/home/ubuntu/engram/data/probes_merged")
    ap.add_argument("--out", required=True)
    ap.add_argument("--grad-ckpt", action="store_true",
                    help="override; otherwise MORPHEUS_GRAD_CKPT is honoured")
    args = ap.parse_args()

    corpora = {d: Path(CORPUS.format(day=d)).read_text() for d in DAYS}
    text = night5_text(args.rehearsal_seed, corpora)

    # Honour the checkpointing setting. Scripts that build a LifeAdapter directly
    # do NOT pick this up from the environment on their own — an earlier version of
    # this script set MORPHEUS_GRAD_CKPT in its launcher and silently trained
    # uncheckpointed at 52 GB instead of 38 GB, which OOM'd five jobs on shared
    # cards. Read it explicitly or do not claim to support it.
    ckpt = args.grad_ckpt or get_settings().morpheus.grad_checkpointing
    adapter = LifeAdapter.open(base_model=args.base_model, device=args.device,
                               resume_adapter=START_ADAPTER, grad_checkpointing=ckpt)
    budget = matched_compute_budget(adapter.tokenizer, corpora[21], 1024)
    started = time.time()
    stats = adapter.train_on(text, CptConfig(epochs=3, seq_len=1024, batch_size=2,
                                             lr=1e-4, max_chunks=budget),
                             tag=f"draw{args.rehearsal_seed}")
    qa = load_suite(args.probes_dir, QA_SUITE)
    rows = []
    for day in (21, 5):          # newest written, and the longest-decay retention canary
        for probe in day_pool(qa, day, 60):
            rows.append({"suite": f"draw{args.rehearsal_seed}_d{day}",
                         "probe_id": probe.probe_id, "q": probe.question,
                         "gold": probe.gold, "pred": adapter.answer(probe.question)})
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(r) + "\n" for r in rows))
    meta = {"rehearsal_seed": args.rehearsal_seed, "start_adapter": START_ADAPTER,
            "grad_checkpointing": ckpt,
            "minutes": round((time.time() - started) / 60, 1),
            "replay_chars": len(text) - len(corpora[21]) - 2, **stats.__dict__}
    out.with_suffix(".meta.json").write_text(json.dumps(meta, indent=1))
    print(json.dumps(meta), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
