#!/usr/bin/env python
"""Amplify one day for real, through the generator, and diff the stats.

The amplify kernel's parity is proved offline in tests/parity/test_amplify.py —
the job plan, the calibration fraction, the validity gate and the corpus rebuild
are all differenced against the reference corpora without a GPU. What that cannot
show is that the vLLM/HF path in front of the kernel actually produces usable
text on this node. This does, on a slice big enough to measure:

    <pinned python> scripts/amplify_day.py --day 5 --limit-blocks 40 --device cuda:6

Reports ok-rate, realized calibration fraction, and chars-per-paragraph against
the reference run's own stats for that day. It writes nothing into the reference
tree.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings                                    # noqa: E402
from app.morpheus.amplify import amplify                               # noqa: E402
from app.morpheus.blocks import load_blocks                            # noqa: E402
from app.morpheus.generate import GenerationConfig, get_generator      # noqa: E402
from app.morpheus.pinned_env import amplify_env                        # noqa: E402
from app.morpheus.profiles import get_profile                          # noqa: E402
from app.recipe import load_recipe                                     # noqa: E402

AMPLIFY_SEED = 13      # the reference chain decorrelates days as seed + day


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--day", type=int, required=True)
    ap.add_argument("--blocks-pattern", default="~/engram/data/corpus/day{day}.blocks.jsonl")
    ap.add_argument("--reference-stats",
                    default="~/engram/data/narrative/day{day}_x48neg.jsonl.stats.json")
    ap.add_argument("--limit-blocks", type=int, default=0,
                    help="amplify only the first N blocks (a measurable slice, not a full night)")
    ap.add_argument("--device", default="")
    ap.add_argument("--base-model", default="")
    ap.add_argument("--backend", default="", choices=["", "vllm", "hf"])
    ap.add_argument("--gpu-mem-util", type=float, default=0.0)
    ap.add_argument("--out", default="", help="write the amplified corpus here")
    args = ap.parse_args()

    settings = get_settings().morpheus
    _s = get_settings()
    recipe = load_recipe(Path(_s.recipes_dir) / f"{_s.recipe_id}.json")
    profile = get_profile(settings.profile)
    # Amplification's env is NOT the trainer's: vLLM pins its own transformers.
    # Fail here rather than after the day log is loaded and the plan is built.
    amplify_env(settings).preflight()

    blocks = load_blocks(Path(args.blocks_pattern.format(day=args.day)).expanduser(),
                         extra_anchors={"day": args.day})
    full_day_blocks = len(blocks)
    if args.limit_blocks:
        blocks = blocks[:args.limit_blocks]

    generator = get_generator(args.backend or settings.amplify_backend, GenerationConfig(
        model=args.base_model or settings.base_model,
        device=args.device or settings.device,
        gpu_memory_utilization=args.gpu_mem_util or settings.gpu_memory_utilization))

    print(f"day {args.day}: {len(blocks)}/{full_day_blocks} blocks x {recipe.variants} "
          f"= {len(blocks) * recipe.variants} narratives", flush=True)
    started = time.time()
    result = amplify(blocks, generator, profile, variants=recipe.variants,
                     neg_frac=recipe.neg_frac, ok_rate_min=recipe.ok_rate_min,
                     seed=AMPLIFY_SEED + args.day)
    elapsed = time.time() - started

    reference = json.loads(Path(args.reference_stats.format(day=args.day))
                           .expanduser().read_text())
    reference_chars_per_para = reference["chars"] / reference["ok"]
    ours = result.stats()
    ours |= {
        "minutes": round(elapsed / 60, 1),
        "chars_per_paragraph": round(result.chars / max(1, result.ok), 1),
        "reference_ok_rate": reference["ok_rate"],
        "reference_chars_per_paragraph": round(reference_chars_per_para, 1),
        "chars_per_paragraph_ratio": round(
            (result.chars / max(1, result.ok)) / reference_chars_per_para, 4),
        "blocks_amplified": len(blocks), "day_blocks": full_day_blocks,
    }
    print(json.dumps(ours, indent=1))
    if args.out:
        Path(args.out).expanduser().write_text(result.corpus)
        print(f"corpus -> {args.out} ({len(result.corpus)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
