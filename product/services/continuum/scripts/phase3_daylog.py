#!/usr/bin/env python
"""Phase-3b, step 1: fetch each replayed day's day-log from the REAL /context.

This is the whole of continuum's part in the dogfood, and it is why the 2c seam was
built the way it was: nothing here is a new code path. It calls the same
`day_log_client(settings, recipe, record_provider=...)` factory the nightly calls, with
the same `fetch_window_records` C10 read underneath, and the same `build_daylog` ->
renderer. Only two things are configuration:

  * the RECIPE is the rule-bent test profile (60 s segments, 5 per block), so one 1-min
    caption record fills one segment and five of them make the ~5-minute block the 5-min
    parity baseline used;
  * the PROVIDER is wrapped, exactly as the design said it would be -- a lambda around
    the real read. It keeps ARM 1 honest in two ways:
      - caption-only: the ASR transcripts run for real (Arm 2) but must not enter the
        recall day-log, or the measurement stops being descriptions-only;
      - train-split-only: the 5-min baseline corpus was built from the train chunks
        alone (day5 = 240 blocks over exactly the day's 61 train chunks, 0 heldout), so
        including the within-day heldout chunks would change the CONTENT SCOPE as well
        as the cadence, and the comparison would no longer isolate one variable.

Both filters are pure config over records that all really landed in /context. Output is
the trainer-seam files (`segments.jsonl` / `blocks.jsonl` / `day.txt`) plus a report of
what the rule-bend actually produced, including how many blocks exceed the amplifier's
6000-char excerpt cap -- a cap that never bound at 5-min cadence and does bind here.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.clients import day_log_client                     # noqa: E402
from app.config import get_settings                        # noqa: E402
from app.context_reader import fetch_window_records        # noqa: E402
from app.morpheus.profiles.speed import EXCERPT_CHARS      # noqa: E402
from app.recipe import load_recipe                         # noqa: E402
from app.window import window_for                          # noqa: E402


def allowed_caption_starts(captions_file: Path, day: int, splits: tuple[str, ...]) -> set[str]:
    """The t_start of every caption the ARM-1 day-log may see for this day.

    Keyed on the wall-clock string the whole replay is keyed on, so the filter needs
    nothing from the record but its own time-spine field."""
    allowed = set()
    with captions_file.open() as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            if row["day"] == day and row.get("split") in splits:
                allowed.add(row["t_start"])
    return allowed


def build_day(day: int, plan: dict, args, recipe) -> dict:
    settings = get_settings()
    info = plan["days"][str(day)]
    win = window_for(plan["user_id"], date.fromisoformat(info["date"]), info["tz"],
                     recipe.boundary_local_time)
    allowed = allowed_caption_starts(Path(plan["captions_file"]), day, tuple(args.splits.split(",")))
    seen = Counter()

    def provider(window):
        records = fetch_window_records(args.storage_url, window,
                                       timeout=settings.http_timeout)
        seen["fetched"] = len(records)
        seen.update(r["content"]["kind"] for r in records)
        kept = [r for r in records
                if r["content"]["kind"] == "caption" and r["t_start"] in allowed]
        seen["kept"] = len(kept)
        return kept

    client = day_log_client(settings, recipe, record_provider=provider)
    daylog = client.fetch_daylog(win)
    outdir = Path(args.out).expanduser() / f"day{day}"
    outdir.mkdir(parents=True, exist_ok=True)
    paths = client.render(daylog, outdir)

    lengths = [len(b.text) for b in daylog.blocks]
    seg_sizes = Counter(len(b.seg_ids) for b in daylog.blocks)
    return {
        "day": day, "window_id": win.window_id, "tz": info["tz"], "city": info["city"],
        "records_fetched": seen["fetched"], "captions": seen["caption"],
        "transcripts": seen["transcript"], "captions_kept": seen["kept"],
        "captions_allowed": len(allowed),
        "segments": len(daylog.segments), "blocks": len(daylog.blocks),
        "block_sizes": {str(k): v for k, v in sorted(seg_sizes.items())},
        "block_chars_mean": round(statistics.mean(lengths)) if lengths else 0,
        "block_chars_max": max(lengths) if lengths else 0,
        "blocks_over_excerpt_cap": sum(1 for n in lengths if n > EXCERPT_CHARS),
        "excerpt_cap": EXCERPT_CHARS,
        "chars_total": sum(lengths),
        "fingerprint": client.fingerprint(daylog),
        "files": paths,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", default="~/phase3/plan/plan.json")
    ap.add_argument("--storage-url", default="http://127.0.0.1:8083")
    ap.add_argument("--days", default="5,9,12,13,17,21", help="the TRAIN days (Arm 1)")
    ap.add_argument("--recipe", default="", help="default: the service's pinned recipe id")
    ap.add_argument("--splits", default="train",
                    help="which within-day splits the day-log may see; 'train,heldout' "
                         "reproduces a scope the 5-min baseline did NOT use")
    ap.add_argument("--out", default="~/phase3/daylog")
    ap.add_argument("--report", default="~/phase3/reports/daylog.json")
    args = ap.parse_args()

    settings = get_settings()
    recipe = load_recipe(args.recipe or
                         str(Path(settings.recipes_dir) / f"{settings.recipe_id}.json"))
    plan = json.loads(Path(args.plan).expanduser().read_text())
    report = {"recipe_id": recipe.recipe_id, "segment_seconds": recipe.segment_seconds,
              "block_segments": recipe.block_segments, "splits": args.splits,
              "storage_url": args.storage_url, "days": {}}
    for day in (int(d) for d in args.days.split(",")):
        row = build_day(day, plan, args, recipe)
        report["days"][str(day)] = row
        print(json.dumps({k: v for k, v in row.items() if k != "files"}), flush=True)

    out = Path(args.report).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1))
    header = (f"{'day':>4}{'fetched':>9}{'caps':>7}{'kept':>7}{'segs':>7}{'blocks':>8}"
              f"{'chars/blk':>11}{'over 6k':>9}")
    print(header)
    print("-" * len(header))
    for day, row in report["days"].items():
        print(f"{day:>4}{row['records_fetched']:>9}{row['captions']:>7}{row['captions_kept']:>7}"
              f"{row['segments']:>7}{row['blocks']:>8}{row['block_chars_mean']:>11}"
              f"{row['blocks_over_excerpt_cap']:>9}")
    print(f"\nblocks -> {Path(args.out).expanduser()}/day{{D}}/blocks.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
