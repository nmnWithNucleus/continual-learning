"""``python -m app.vision.prompts show`` — print the EXACT assembled wire text of a pack.

Goal 4: a pack is evolvable without a code change, so a researcher must be able to see
precisely what will be sent to the model. This renders a pack's system + user text with a
realistic context (frame count, offsets, budget-derived word band, a sample OCR block) and
prints the composed prompt dialect tag alongside — no GPU, no network.

    python -m app.vision.prompts show --pack screen-clip-v1 --frames 12 --span 60
"""
from __future__ import annotations

import argparse
import sys

from ..budget import caption_word_bounds
from ..config import get_vision_settings
from . import PACK_DIGEST, PACK_VERSION, all_packs, get, render, scenario_label

_SAMPLE_OCR = (
    "+0.0s [titlebar] Inbox — Mail · [message] Re: Q3 deck — Sarah Chen · "
    "[compose] Thanks, sending the revised slides now"
)


def _offsets(span: float, n: int) -> list[float]:
    if n <= 1:
        return [round(span / 2, 1)]
    return [round(i * (span / n), 1) for i in range(n)]


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(prog="python -m app.vision.prompts show")
    ap.add_argument("--pack", default="screen-clip-v1")
    ap.add_argument("--frames", type=int, default=12)
    ap.add_argument("--span", type=float, default=60.0)
    ap.add_argument("--scenario", default=None,
                    help="scenario whose [[scenario_label]] to render (default: VIDEO_SCENARIO)")
    ap.add_argument("--ocr", default=_SAMPLE_OCR, help="sample injected on-screen text")
    args = ap.parse_args(argv)

    if args.pack not in all_packs():
        print(f"unknown pack {args.pack!r}; have: {', '.join(sorted(all_packs()))}",
              file=sys.stderr)
        return 2

    spec = get(args.pack)
    vs = get_vision_settings()
    n = max(1, args.frames)
    lo, hi = caption_word_bounds(args.span, vs)
    # Render the label for the deployment's scenario (what a real request sends), overridable
    # with --scenario so any scenario's exact wire text can be inspected.
    label_scenario = args.scenario or vs.scenario
    context = {
        "span_s": f"{args.span:.0f}",
        "scenario_label": scenario_label(label_scenario),
        "n": n,
        "offsets": ", ".join(f"{t:.1f}" for t in _offsets(args.span, n)),
        "ocr_block": args.ocr or "(none)",
        "words_lo": lo,
        "words_hi": hi,
        "max_regions": vs.ocr_max_events,
    }
    rendered = render(spec, **context)

    bar = "=" * 78
    print(bar)
    print(f"pack {spec.id!r}  role={spec.role}  schema={spec.schema_name or '(none)'}  "
          f"max_tokens={spec.max_tokens}  temperature={spec.temperature}")
    print(f"prompt dialect tag: @{spec.id}.p{PACK_VERSION}.{PACK_DIGEST}")
    print(bar)
    print("[system]")
    print(rendered.system)
    print()
    print("[user]")
    print(rendered.user)
    print(bar)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
