#!/usr/bin/env python
"""Build the Phase-3 replay fixture: a wall-clock spine + a caption index.

Phase 3 replays Speed's tour days through the REAL pipeline. Two services must agree
on ONE fact -- WHEN each chunk happened -- because recording stamps the C1 time-spine
and data-processing has to find the 1-min description covering each sub-span of that
same span. Deriving that spine twice, in two services, is how the two copies drift; so
it is derived ONCE, here, into flat artifacts:

    day{D}.replay.jsonl   one row per C1 chunk: audio + its wall-clock span
                          -> recording's replay ChunkSource (--source <file>)
    captions.jsonl        one row per 1-min description window: [t_start, t_end) + text
                          -> data-processing's injected-caption sidecar (INJECT_CAPTION_INDEX)
    plan.json             per-day window/tz/city/counts -> the 3a + 3b drivers

THE SPINE (the one real decision here). A tour day is 18.8-26.0 h of stream and the
consolidation window is 24 h wide, so a day cannot simply be pinned to its real
time-of-day: with the real clock there is NO boundary that fits even the eight days
under 24 h (day 13 needs a boundary >= 08:59 local, day 28 needs <= 08:49), and day 17
is 26.0 h -- longer than any 24 h window. So the replay lays each day CONTIGUOUSLY FROM
ITS WINDOW START, on a whole-minute grid:

  * day D -> the real tour date (day 1 = 2025-08-29, verified against the manifest's
    own YYYYMMDD for eight of the nine days; day 17 spans two dates and takes the later);
  * the window is [date @ boundary local, +24 h) in the day's city timezone;
  * chunk i starts at the window start + sum of ceil(duration/60)*60 over the chunks
    before it. Quantising the STRIDE to whole minutes (never the span) is what makes
    the rule-bend exact: every 1-min description window then lands in exactly one 60 s
    day-log segment instead of straddling two. The chunk keeps its REAL duration, so
    ASR's offset->absolute mapping stays honest and consecutive chunks leave a
    sub-minute gap rather than overlapping.

What does not fit is reported, not hidden: a day longer than 24 h has its tail fall
outside the window, where the day-log's attribution rule drops it (day 17 loses ~2 h).
Those records still land in /context -- they simply belong to no consolidated window.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.morpheus.profiles import get_profile  # noqa: E402

# Day 1 of 35. Cross-checked against the manifest's own dated source paths.
TOUR_EPOCH = date(2025, 8, 29)
# The tour's states -> the local zone the descriptions' own ET/CT/PT readings assert.
STATE_TZ = {
    "DC": "America/New_York", "PA": "America/New_York", "MA": "America/New_York",
    "OH": "America/New_York", "IL": "America/Chicago", "LA": "America/Chicago",
    "OK + KS": "America/Chicago", "OR": "America/Los_Angeles",
}
_WINDOW_RE = re.compile(r"__w(\d+)_(\d+)$")


def tour_date(day: int) -> date:
    return TOUR_EPOCH + timedelta(days=day - 1)


def rfc3339(dt: datetime) -> str:
    """Match recording's own wall-clock rendering (app/timeutil.rfc3339)."""
    dt = dt.astimezone(timezone.utc)
    if dt.microsecond:
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def window_start(day: int, tz: str, boundary: str) -> datetime:
    hh, mm = (int(x) for x in boundary.split(":"))
    return datetime.combine(tour_date(day), time(hh, mm),
                            tzinfo=ZoneInfo(tz)).astimezone(timezone.utc)


def day_chunks(manifest: Path, days: set[int]) -> dict[int, list[dict[str, Any]]]:
    """The manifest rows for each day, in CAPTURE ORDER (part, then chunk index)."""
    by_day: dict[int, list[dict[str, Any]]] = defaultdict(list)
    with manifest.open() as fh:
        for row in csv.DictReader(fh):
            if int(row["day"]) in days:
                by_day[int(row["day"])].append(row)
    for rows in by_day.values():
        rows.sort(key=lambda r: (int(r["part"]), int(r["chunk_index"])))
    return by_day


def chunk_splits(holdout: Path) -> dict[str, str]:
    """chunk_id -> "train" | "heldout" (the WITHIN-day split).

    Load-bearing for comparability, not decoration: the 5-min parity corpus was built
    from the TRAIN chunks only (day5 240 blocks / 61 distinct parents == the day's 61
    train chunks, 0 heldout), which is what makes the within-day heldout chunks a real
    tripwire. Phase 3 lands every chunk in /context — that is what a faithful capture
    does — and reproduces the baseline's content scope at the READ, in the same
    record-provider wrapper that keeps the recall day-log caption-only.
    """
    with holdout.open() as fh:
        return {r["chunk_id"]: r["split"] for r in csv.DictReader(fh)}


def caption_windows(descriptions: Path, chunk_key: str) -> list[tuple[int, int, Path]]:
    """The 1-min description windows covering one chunk, in time order."""
    found = []
    for path in descriptions.glob(f"{chunk_key}__w*.json"):
        match = _WINDOW_RE.search(path.stem)
        if match:
            found.append((int(match.group(1)), int(match.group(2)), path))
    return sorted(found)


def caption_text(profile, record: dict[str, Any], *, with_anchor: bool) -> str:
    """The description rendered as a DP caption record would carry it.

    The profile's own field render; by default minus its FIRST line, which is the
    tour anchor ("[Day 5 of 35 . Washington, DC . 5min clip . ~9:17 AM ET]") --
    dataset metadata a real captioner could not know, and the day-log writes its own
    time anchor from the record's t_start. Keeping the FIELDS identical to the 5-min
    parity path is what leaves CADENCE as the single changed variable.
    """
    rendered = profile.render_block(record)
    if with_anchor:
        return rendered
    _, _, body = rendered.partition("\n")
    return body or rendered


def build(args: argparse.Namespace) -> dict[str, Any]:
    profile = get_profile(args.profile)
    days = sorted({int(d) for d in args.days.split(",")})
    manifest = Path(args.manifest).expanduser()
    splits = chunk_splits(Path(args.holdout).expanduser())
    descriptions = Path(args.descriptions).expanduser()
    cache = Path(args.audio_cache).expanduser()
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)

    by_day = day_chunks(manifest, set(days))
    missing = [d for d in days if not by_day.get(d)]
    if missing:
        raise SystemExit(f"no manifest rows for days {missing}")

    plan: dict[str, Any] = {
        "user_id": args.user_id, "device_id": args.device_id,
        "boundary_local_time": args.boundary, "tour_epoch": TOUR_EPOCH.isoformat(),
        "profile": profile.id, "caption_anchor_line": bool(args.with_anchor), "days": {},
    }
    caption_rows: list[dict[str, Any]] = []

    for day in days:
        rows = by_day[day]
        state = rows[0]["state"]
        tz = STATE_TZ.get(state)
        if tz is None:
            raise SystemExit(f"day {day}: no timezone mapped for state {state!r}")
        start = window_start(day, tz, args.boundary)
        end = start + timedelta(days=1)

        offset = 0                    # whole-minute stride from the window start
        chunk_rows: list[dict[str, Any]] = []
        captions = in_window = missing_audio = train_captions = 0
        for sequence, row in enumerate(rows):
            duration = float(row["duration_sec"])
            chunk_key = row["chunk_id"]
            t_start = start + timedelta(seconds=offset)
            audio = cache / f"{chunk_key}.wav"
            missing_audio += 0 if audio.exists() else 1
            chunk_rows.append({
                "chunk_key": chunk_key, "day": day, "sequence": sequence,
                "part": int(row["part"]), "video_id": row["video_id"],
                "split": splits.get(chunk_key, "unknown"),
                "audio": str(audio), "media": row["media_path"],
                "duration_s": round(duration, 3),
                "t_start": rfc3339(t_start),
                "t_end": rfc3339(t_start + timedelta(seconds=duration)),
            })
            for w_start, w_end, path in caption_windows(descriptions, chunk_key):
                text = caption_text(profile, json.loads(path.read_text()),
                                    with_anchor=args.with_anchor)
                if not text.strip():
                    continue
                c_start = t_start + timedelta(seconds=w_start)
                captions += 1
                inside = start <= c_start < end
                in_window += 1 if inside else 0
                train_captions += 1 if inside and splits.get(chunk_key) == "train" else 0
                caption_rows.append({
                    "t_start": rfc3339(c_start),
                    "t_end": rfc3339(t_start + timedelta(seconds=w_end)),
                    "text": text, "chunk_key": chunk_key,
                    "window": f"w{w_start}_{w_end}", "day": day,
                    "split": splits.get(chunk_key, "unknown"),
                })
            offset += int(math.ceil(duration / 60.0)) * 60

        replay_path = out / f"day{day}.replay.jsonl"
        replay_path.write_text("".join(json.dumps(r) + "\n" for r in chunk_rows))
        plan["days"][str(day)] = {
            "date": tour_date(day).isoformat(), "tz": tz,
            "city": rows[0]["city"], "state": state,
            "window_start_utc": rfc3339(start), "window_end_utc": rfc3339(end),
            "chunks": len(rows), "missing_audio": missing_audio,
            "content_hours": round(sum(float(r["duration_sec"]) for r in rows) / 3600, 2),
            "spine_hours": round(offset / 3600, 2),
            "train_chunks": sum(1 for r in rows if splits.get(r["chunk_id"]) == "train"),
            "captions": captions, "captions_in_window": in_window,
            "captions_train_in_window": train_captions,
            "dropped_by_window": captions - in_window,
            "replay_file": str(replay_path),
        }

    captions_path = out / "captions.jsonl"
    captions_path.write_text("".join(json.dumps(r) + "\n" for r in caption_rows))
    plan["captions_file"] = str(captions_path)
    plan["captions_total"] = len(caption_rows)
    plan["built_at"] = datetime.now(timezone.utc).isoformat()
    (out / "plan.json").write_text(json.dumps(plan, indent=1))
    return plan


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", default="5,6,9,12,13,16,17,21,28")
    ap.add_argument("--manifest", default="~/speed_lora/data/chunks_manifest.csv")
    ap.add_argument("--holdout", default="~/speed_lora/data/holdout_manifest.csv")
    ap.add_argument("--descriptions", default="~/speed_lora/data/descriptions/1min")
    ap.add_argument("--audio-cache", default="~/phase3/cache/audio")
    ap.add_argument("--out", default="~/phase3/plan")
    ap.add_argument("--user-id", default="replay-speed")
    ap.add_argument("--device-id", default="replay-tour-audio")
    ap.add_argument("--boundary", default="04:00", help="recipe boundary_local_time")
    ap.add_argument("--profile", default="speed")
    ap.add_argument("--with-anchor", action="store_true",
                    help="keep the description's tour-anchor line in the caption text "
                         "(off: a real captioner never sees it)")
    args = ap.parse_args()
    plan = build(args)
    print(json.dumps({k: v for k, v in plan.items() if k != "days"}, indent=1))
    header = (f"{'day':>4}{'date':>13}{'tz':>22}{'chunks':>8}{'train':>7}{'hours':>7}"
              f"{'spine':>7}{'caps':>7}{'cap-tr':>8}{'dropped':>9}{'no-audio':>10}")
    print(header)
    print("-" * len(header))
    for day, info in plan["days"].items():
        print(f"{day:>4}{info['date']:>13}{info['tz']:>22}{info['chunks']:>8}"
              f"{info['train_chunks']:>7}{info['content_hours']:>7}{info['spine_hours']:>7}"
              f"{info['captions']:>7}{info['captions_train_in_window']:>8}"
              f"{info['dropped_by_window']:>9}{info['missing_audio']:>10}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
