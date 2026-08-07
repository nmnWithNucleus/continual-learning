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
from . import PACK_DIGEST, PACK_VERSION, all_packs, get, render, scenario_label

# Mirrors of the stage-file code pins (app/stages/video/clipcap.py /
# screentext.py) so the preview renders what a real request sends. Display-only.
_CAPTION_RATE = 16     # chars/second-of-life, the clipcap pin
_SCENARIO = "screen-mac"
_MAX_REGIONS = 3       # the screentext ocr_max_events pin (vlm-OCR packs only)

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
    ap.add_argument("--scenario", default=_SCENARIO,
                    help=f"scenario whose [[scenario_label]] to render (default: {_SCENARIO})")
    ap.add_argument("--ocr", default=_SAMPLE_OCR, help="sample injected on-screen text")
    args = ap.parse_args(argv)

    if args.pack not in all_packs():
        print(f"unknown pack {args.pack!r}; have: {', '.join(sorted(all_packs()))}",
              file=sys.stderr)
        return 2

    spec = get(args.pack)
    n = max(1, args.frames)
    lo, hi = caption_word_bounds(args.span, _CAPTION_RATE)
    context = {
        "span_s": f"{args.span:.0f}",
        "scenario_label": scenario_label(args.scenario),
        "n": n,
        "offsets": ", ".join(f"{t:.1f}" for t in _offsets(args.span, n)),
        "ocr_block": args.ocr or "(none)",
        "words_lo": lo,
        "words_hi": hi,
        "max_regions": _MAX_REGIONS,
    }
    rendered = render(spec, **context)

    bar = "=" * 78
    print(bar)
    print(f"pack {spec.id!r}  role={spec.role}  schema={spec.schema_name or '(none)'}  "
          f"max_tokens={spec.max_tokens}  temperature={spec.temperature}")
    print(f"pack_version p{PACK_VERSION} · aggregate PACK_DIGEST {PACK_DIGEST} "
          "(pinned in app/stages/video/clipcap.py — an edit here needs a vB bump there)")
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
