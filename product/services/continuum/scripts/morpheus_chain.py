#!/usr/bin/env python
"""Run one Morpheus consolidation chain end-to-end and judge it.

This is the E2E half of the parity harness: N nights, sequentially, through the
REAL kernels — continue-CPT the one life adapter on each day's amplified corpus
plus rehearsal of the days before it, and after every night re-evaluate every day
consolidated so far. The output is a run directory in the same shape the
reference runs use, so `parity_report.py` can put ours and theirs in one table.

    <pinned python> scripts/morpheus_chain.py --seed 0 --out var/parity/seed0

Two things it deliberately does NOT do:

  it does not re-amplify.  The chain trains on the reference amplified corpora —
    the same bytes the goldens trained on. That is what makes this a clean A/B of
    the port: a fresh 48x generation would add generator variance on top of the
    seed spread and confound exactly the comparison we are trying to make.
    Amplification is proven separately, kernel-by-kernel, in tests/parity/.

  it does not use the storage or DP paths.  A data-shape change must never be
    able to confound a port bug (ws-morpheus-port §7).

Run it with the PINNED interpreter (never `conda activate`); it preflights the
judge environment before spending a single GPU-hour, because discovering a
missing litellm after a 3-hour chain is how a night gets wasted.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings                                    # noqa: E402
from app.morpheus import MORPHEUS_VERSION, SOURCE_COMMIT               # noqa: E402
from app.morpheus.eval import (HELDOUT_LIMIT, PROBES_PER_DAY, TRAP_ANSWER_TOKENS,  # noqa: E402
                               TRAPS_LIMIT, Predictions, answer_suite, base_floor,
                               day_suite, readout, traps_suite)
from app.morpheus.pinned_env import judge_env, train_env                # noqa: E402
from app.morpheus.probes import (HELDOUT_SUITE, QA_SUITE, TRAPS_SUITE,  # noqa: E402
                                 assert_independent_generators, day_pool, load_suite)
from app.morpheus.replay import sample_replay                          # noqa: E402
from app.morpheus.train import CptConfig, LifeAdapter, LoraSpec, matched_compute_budget  # noqa: E402
from app.recipe import load_recipe                                     # noqa: E402


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="run directory (reference-run shape)")
    ap.add_argument("--seed", type=int, default=0,
                    help="chain seed — varies LoRA-A init only, matching how the reference "
                         "seed ensemble was built")
    ap.add_argument("--rehearsal-seed", type=int, default=7,
                    help="rehearsal stream seed, FIXED across the ensemble (the reference "
                         "spread measures init variance, not sampling variance)")
    ap.add_argument("--days", default="5,9,12,13,17,21")
    ap.add_argument("--corpus-pattern",
                    default="~/engram/data/narrative/day{day}_x48neg.corpus.txt")
    ap.add_argument("--blocks-pattern", default="~/engram/data/corpus/day{day}.blocks.jsonl")
    ap.add_argument("--recipe", default="", help="recipe JSON (default: the service's pinned one)")
    ap.add_argument("--base-model", default="", help="override MORPHEUS_BASE_MODEL")
    ap.add_argument("--device", default="", help="override MORPHEUS_DEVICE, e.g. cuda:7")
    ap.add_argument("--replay-source", choices=["amp", "rawlog"], default="amp",
                    help="rehearse amplified corpora (what the goldens did) or raw day logs")
    ap.add_argument("--save-every-step", action="store_true",
                    help="keep a snapshot per night (1.4G each); default keeps only the final")
    ap.add_argument("--skip-judge", action="store_true", help="stop after preds.jsonl")
    ap.add_argument("--smoke", action="store_true",
                    help="tiny run to prove the wiring: 1 epoch, 20 chunks, 3 probes/suite. "
                         "BREAKS matched compute and means nothing numerically.")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    settings = get_settings()
    morpheus = settings.morpheus
    base_model = args.base_model or morpheus.base_model
    device = args.device or morpheus.device
    recipe = load_recipe(args.recipe or settings.recipe_path)
    days = [int(d) for d in args.days.split(",")]
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)

    # A score is only evidence if the questions were not written by the model that
    # wrote the training prose. Checked before anything expensive happens.
    assert_independent_generators(probe_generator=morpheus.probe_generator,
                                  corpus_generator=base_model)
    judge = judge_env(morpheus)
    if not args.skip_judge:
        judge.preflight()      # fail in one second, not after three GPU-hours
    # The environment that produced this run, stored with it.
    (out / "env.lock.txt").write_text(train_env(morpheus).freeze())

    corpora = {d: Path(args.corpus_pattern.format(day=d)).expanduser().read_text()
               for d in days}
    if args.replay_source == "rawlog":
        from app.morpheus.blocks import blocks_corpus, load_blocks
        rehearsal_sources = {d: blocks_corpus(load_blocks(
            Path(args.blocks_pattern.format(day=d)).expanduser())) for d in days}
    else:
        rehearsal_sources = corpora

    qa = load_suite(morpheus.probes_dir, QA_SUITE)
    probes = {d: day_pool(qa, d, PROBES_PER_DAY) for d in days}
    missing = [d for d in days if not probes[d]]
    if missing:
        raise SystemExit(f"no {QA_SUITE} probes for days {missing} — the decay matrix "
                         "would silently have holes")
    traps = load_suite(morpheus.probes_dir, TRAPS_SUITE)[:TRAPS_LIMIT]
    heldout = load_suite(morpheus.probes_dir, HELDOUT_SUITE)[:HELDOUT_LIMIT]
    if args.smoke:
        probes = {d: p[:3] for d, p in probes.items()}
        traps, heldout = traps[:3], heldout[:3]

    report = {"chain": out.name, "seed": args.seed, "days": days, "smoke": args.smoke,
              "morpheus_version": MORPHEUS_VERSION, "source_commit": SOURCE_COMMIT,
              "recipe_id": recipe.recipe_id, "base_model": base_model, "device": device,
              "replay_frac": recipe.replay_frac, "replay_source": args.replay_source,
              "replay_neg_boost": recipe.replay_neg_boost,
              "rehearsal_seed": args.rehearsal_seed, "matched_compute": True,
              "grad_checkpointing": morpheus.grad_checkpointing,
              "shard_gpus": morpheus.shard_gpus, "train": {}}
    print(json.dumps({k: v for k, v in report.items() if k != "train"}, indent=1), flush=True)

    started = time.time()
    adapter = LifeAdapter.open(base_model=base_model, device=device, seed=args.seed,
                               lora=LoraSpec(r=recipe.lora_r, alpha=recipe.lora_alpha),
                               shard_gpus=morpheus.shard_gpus,
                               grad_checkpointing=morpheus.grad_checkpointing)
    # ONE rehearsal stream for the whole chain: night 4's selection depends on
    # every draw nights 1-3 made, which is what makes a chain reproducible as a
    # chain rather than as six independent nights. Held FIXED across the seed
    # ensemble so the spread we compare against isolates adapter-init variance.
    rng = random.Random(args.rehearsal_seed)

    with Predictions(out / "preds.jsonl") as preds:
        for step, day in enumerate(days):
            new_day = corpora[day]
            budget = matched_compute_budget(adapter.tokenizer, new_day, recipe.chunk_tokens)
            text = new_day
            if step:
                rehearsal = sample_replay([rehearsal_sources[d] for d in days[:step]],
                                          frac=recipe.replay_frac, target_chars=len(new_day),
                                          rng=rng, neg_boost=recipe.replay_neg_boost)
                text = f"{new_day}\n\n{rehearsal}"
            stats = adapter.train_on(text, CptConfig(
                epochs=1 if args.smoke else recipe.epochs, seq_len=recipe.chunk_tokens,
                batch_size=recipe.batch_size, lr=recipe.lr,
                max_chunks=20 if args.smoke else budget, log_every=10 if args.smoke else 100),
                tag=f"s{step}_d{day}")
            report["train"][f"s{step}_d{day}"] = asdict(stats)
            print(f"== night {step} (day {day}) trained: {asdict(stats)}", flush=True)

            for seen in days[:step + 1]:                       # the decay-matrix column
                answer_suite(adapter, probes[seen], day_suite(step, seen), preds)
            answer_suite(adapter, traps, traps_suite(step), preds,
                         max_new_tokens=TRAP_ANSWER_TOKENS)
            if args.save_every_step:
                adapter.save(out / f"adapter_s{step}_d{day}")
            _write(out / "train_report.json", report)

        adapter.save(out / "adapter_final")
        # Controls last: the base floor for every seen day, and heldout days both
        # with and without the adapter. Without these a recall number is unreadable.
        base_floor(adapter, day_probes=probes, heldout=heldout, out=preds)
        answer_suite(adapter, heldout, "final_heldout", preds)

    report["wall_clock_hours"] = round((time.time() - started) / 3600, 3)
    _write(out / "train_report.json", report)
    print(f"chain done in {report['wall_clock_hours']}h -> {out}", flush=True)

    if args.skip_judge:
        return 0
    judge.run([str(Path(__file__).resolve().parent / "judge_preds.py"),
               "--preds", str(out / "preds.jsonl"), "--out", str(out / "judge.json"),
               "--label", out.name])
    scored = readout(json.loads((out / "judge.json").read_text()), days, label=out.name,
                     predictions=[json.loads(x) for x in
                                  (out / "preds.jsonl").read_text().splitlines() if x.strip()])
    _write(out / "readout.json", scored.as_row() | {"decay": scored.retention_per_day,
                                                    "traps_by_step": scored.traps_by_step})
    print(json.dumps(scored.as_row(), indent=1))
    return 0


def _write(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=1))


if __name__ == "__main__":
    raise SystemExit(main())
