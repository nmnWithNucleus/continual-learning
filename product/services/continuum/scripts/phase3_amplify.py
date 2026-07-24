#!/usr/bin/env python
"""Phase-3b, step 2: amplify each rule-bent day-log 48x into a night's CPT corpus.

The kernel is untouched -- `amplify()` with the recipe's own variants / neg_frac /
ok_rate_min, the reference's `seed = 13 + day` decorrelation, the same vLLM generator
scripts/amplify_day.py uses. What this driver adds is the ANCHORS the product-path
day-log does not carry.

That gap is real and it is where a silent Phase-3 failure would have lived: the research
block text opened with "[Day 5 of 35 . Washington, DC . 5min clip . ~9:17 AM ET]", while
`daylog._render_block` writes its own anchor from the record's time-spine
(anchors = {date, place}) and knows nothing about a tour. SpeedProfile reads
`block.anchors["day"]` to build the prompt and `is_valid` requires that day number to
come back in the generated prose, so blocks loaded without a `day` anchor raise, and a
missing `city` would have silently rendered "(in )" into all 67k prompts. Both are
supplied here from the replay plan -- the same place the wall clock came from.

    <amplify python> scripts/phase3_amplify.py --days 5,9,12,13,17,21 --device cuda:0

Writes `day{D}.x48neg.corpus.txt` in the shape `morpheus_chain.py --corpus-pattern`
expects, plus per-day stats (ok-rate, realized calibration fraction, chars/paragraph)
against the reference night's own numbers, because a generator that quietly degrades
produces a corpus that still looks like a corpus.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings                                   # noqa: E402
from app.morpheus.amplify import amplify                              # noqa: E402
from app.morpheus.blocks import load_blocks                           # noqa: E402
from app.morpheus.generate import GenerationConfig, get_generator     # noqa: E402
from app.morpheus.pinned_env import amplify_env                       # noqa: E402
from app.morpheus.profiles import get_profile                         # noqa: E402
from app.recipe import load_recipe                                    # noqa: E402

AMPLIFY_SEED = 13      # the reference chain decorrelates days as seed + day


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", default="5,9,12,13,17,21")
    ap.add_argument("--plan", default="~/phase3/plan/plan.json")
    ap.add_argument("--daylog", default="~/phase3/daylog",
                    help="output of phase3_daylog.py: <daylog>/day{D}/blocks.jsonl")
    ap.add_argument("--out", default="~/phase3/corpus")
    ap.add_argument("--report", default="~/phase3/reports/amplify.json")
    ap.add_argument("--recipe", default="")
    ap.add_argument("--reference-stats",
                    default="~/engram/data/narrative/day{day}_x48neg.jsonl.stats.json")
    ap.add_argument("--limit-blocks", type=int, default=0,
                    help="amplify only the first N blocks per day (a measurable slice, "
                         "not a night) -- use it to check ok-rate before spending hours")
    ap.add_argument("--device", default="")
    ap.add_argument("--base-model", default="")
    ap.add_argument("--backend", default="", choices=["", "vllm", "hf"])
    ap.add_argument("--gpu-mem-util", type=float, default=0.0)
    args = ap.parse_args()

    settings = get_settings()
    morpheus = settings.morpheus
    recipe = load_recipe(args.recipe or
                         str(Path(settings.recipes_dir) / f"{settings.recipe_id}.json"))
    profile = get_profile(morpheus.profile)
    amplify_env(morpheus).preflight()      # fail now, not after the first day-log loads
    plan = json.loads(Path(args.plan).expanduser().read_text())
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)

    generator = get_generator(args.backend or morpheus.amplify_backend, GenerationConfig(
        model=args.base_model or morpheus.base_model,
        device=args.device or morpheus.device,
        gpu_memory_utilization=args.gpu_mem_util or morpheus.gpu_memory_utilization))

    report = {"recipe_id": recipe.recipe_id, "variants": recipe.variants,
              "neg_frac": recipe.neg_frac, "ok_rate_min": recipe.ok_rate_min,
              "base_model": args.base_model or morpheus.base_model,
              "limit_blocks": args.limit_blocks, "days": {}}
    for day in (int(d) for d in args.days.split(",")):
        info = plan["days"][str(day)]
        blocks = load_blocks(Path(args.daylog).expanduser() / f"day{day}" / "blocks.jsonl",
                             # The two anchors the product day-log cannot know.
                             extra_anchors={"day": day, "city": info["city"]})
        full = len(blocks)
        if args.limit_blocks:
            blocks = blocks[:args.limit_blocks]
        print(f"day {day}: {len(blocks)}/{full} blocks x {recipe.variants} = "
              f"{len(blocks) * recipe.variants} narratives", flush=True)
        started = time.time()
        result = amplify(blocks, generator, profile, variants=recipe.variants,
                         neg_frac=recipe.neg_frac, ok_rate_min=recipe.ok_rate_min,
                         seed=AMPLIFY_SEED + day)
        stats = result.stats() | {
            "day": day, "blocks": len(blocks), "day_blocks": full,
            "minutes": round((time.time() - started) / 60, 1),
            "chars_per_paragraph": round(result.chars / max(1, result.ok), 1),
        }
        reference = Path(args.reference_stats.format(day=day)).expanduser()
        if reference.exists():
            ref = json.loads(reference.read_text())
            stats |= {"reference_ok_rate": ref["ok_rate"],
                      "reference_chars_per_paragraph": round(ref["chars"] / ref["ok"], 1),
                      "reference_paragraphs": ref["ok"]}
        if not args.limit_blocks:
            path = out / f"day{day}.x48neg.corpus.txt"
            path.write_text(result.corpus)
            stats["corpus"] = str(path)
        report["days"][str(day)] = stats
        print(json.dumps(stats, indent=1), flush=True)

    path = Path(args.report).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=1))
    print(f"report -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
