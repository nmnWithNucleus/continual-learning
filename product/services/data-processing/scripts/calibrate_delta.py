#!/usr/bin/env python3
"""Calibrate the delta gate against a folder of real screen clips (O-1).

The one measurement WS-B cannot make from synthetic fixtures: which change-classification
threshold to pin. Production thresholds are gated on one real-capture hour; this script is
how that hour is read. For every clip in a folder it runs the Pass-A change pipeline
(``app/vision/clip._pass_a``) and prints:

  * per-clip peak / spread signatures (the D-04 vectors, on YOUR footage);
  * an aggregate peak histogram over all deltas (validates the idle assumption directly);
  * for each candidate ``VIDEO_OCR_IDLE_PEAK``, the OCR events/chunk the selector would
    emit — the cost/coverage curve you pin the default against.

No GPU, no network — just ffmpeg + the two WS-B modules. Run from anywhere:

    python scripts/calibrate_delta.py /path/to/clips/ --period 2.0 --floor-s 120

The change metric itself (binarize-per-pixel -> 32x32 area-average -> max/count) is fixed by
D-04; what is being swept here is the classification threshold over that metric.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make ``app`` importable when this file is run as a script from any working directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.vision import clip, delta  # noqa: E402

_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".bin"}


def _analyze(path: Path, period: float):
    maps, times, fps = clip._pass_a(str(path), period)
    peaks = [delta.summarize_map(m).peak for m in maps]
    spreads = [delta.summarize_map(m).spread for m in maps]
    return maps, times, peaks, spreads


def _histogram(values: list[int], edges: list[int], width: int = 48) -> str:
    """A text histogram of ``values`` bucketed by ``edges`` (``[e0,e1) [e1,e2) ... [en,inf)``)."""
    if not values:
        return "  (no deltas)"
    counts = [0] * len(edges)
    for v in values:
        b = 0
        for i, e in enumerate(edges):
            if v >= e:
                b = i
        counts[b] += 1
    top = max(counts) or 1
    lines = []
    for i, e in enumerate(edges):
        hi = edges[i + 1] - 1 if i + 1 < len(edges) else None
        label = f"[{e:>4}..{hi:<4}]" if hi is not None else f"[{e:>4}..∞  ]"
        bar = "#" * round(counts[i] * width / top)
        pct = 100.0 * counts[i] / len(values)
        lines.append(f"  {label} {counts[i]:>6} {pct:5.1f}% |{bar}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Calibrate the WS-B delta gate over a clip folder (O-1).")
    ap.add_argument("folder", help="directory of screen-capture clips")
    ap.add_argument("--period", type=float, default=2.0, help="Pass-A analysis period (s)")
    ap.add_argument("--layout-peak", type=int, default=40, help="fixed VIDEO_OCR_LAYOUT_PEAK")
    ap.add_argument("--max-events", type=int, default=3, help="fixed VIDEO_OCR_MAX_EVENTS")
    ap.add_argument("--floor-s", type=float, default=120.0, help="VIDEO_OCR_FLOOR_S")
    ap.add_argument("--candidates", default="4,6,8,10,12,16,20,32",
                    help="comma-separated VIDEO_OCR_IDLE_PEAK candidates to sweep")
    args = ap.parse_args(argv)

    folder = Path(args.folder)
    clips = sorted(p for p in folder.iterdir() if p.suffix.lower() in _VIDEO_EXTS)
    if not clips:
        print(f"no clips found in {folder} (extensions: {sorted(_VIDEO_EXTS)})")
        return 1

    candidates = [int(x) for x in args.candidates.split(",") if x.strip()]
    all_peaks: list[int] = []
    all_spreads: list[int] = []
    analyzed: list[tuple[str, list, list]] = []  # (name, maps, times)

    print(f"== Per-clip signatures (period={args.period}s) ==")
    for c in clips:
        try:
            maps, times, peaks, spreads = _analyze(c, args.period)
        except clip.ClipDecodeError as exc:
            print(f"  {c.name:<32} SKIP ({exc})")
            continue
        all_peaks += peaks
        all_spreads += spreads
        analyzed.append((c.name, maps, times))
        print(f"  {c.name:<32} deltas={len(maps):>3}  "
              f"peak[min/med/max]={min(peaks)}/{sorted(peaks)[len(peaks)//2]}/{max(peaks)}  "
              f"spread[max]={max(spreads)}")

    if not analyzed:
        print("no decodable clips.")
        return 1

    print(f"\n== Aggregate peak histogram ({len(all_peaks)} deltas) ==")
    print(_histogram(all_peaks, [0, 3, 5, 9, 13, 21, 41, 81, 129, 256]))
    print(f"\n== Aggregate spread histogram ==")
    print(_histogram(all_spreads, [0, 1, 8, 33, 129, 513, 1025]))

    print(f"\n== OCR events/chunk vs candidate VIDEO_OCR_IDLE_PEAK "
          f"(layout_peak={args.layout_peak}, max_events={args.max_events}, floor_s={args.floor_s}) ==")
    print(f"  {'idle_peak':>9} | {'mean events/chunk':>17} | {'idle chunks':>11} | events per clip")
    for cand in candidates:
        per_clip_events = []
        idle_count = 0
        for name, maps, times in analyzed:
            _, ocr_times, idle = delta.select_ocr_frames(
                times, maps, t_start_epoch=0.0, idle_peak=cand,
                layout_peak=args.layout_peak, max_events=args.max_events, floor_s=args.floor_s)
            per_clip_events.append(len(ocr_times))
            idle_count += int(idle)
        mean = sum(per_clip_events) / len(per_clip_events)
        seq = ",".join(str(e) for e in per_clip_events)
        print(f"  {cand:>9} | {mean:>17.2f} | {idle_count:>4}/{len(analyzed):<6} | {seq}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
