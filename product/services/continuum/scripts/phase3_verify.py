#!/usr/bin/env python
"""Phase-3a exit check: is the replayed day really queryable from /context?

The bridge's claim is narrow and has to be checked as stated: for each replayed day,
REAL C2 records -- caption AND transcript -- come back from the C10 range read by
(user, window), on the spine the plan laid down. So this asks storage the same question
continuum's nightly asks (`GET /context/records?user_id=&from=&to=`) and checks the
answer against the plan, EXACTLY: the set of caption timestamps that came back must equal
the set the plan predicted, not merely count the same.

Two properties of the spine make a naive count wrong, and both are reported rather than
papered over:

  * a day longer than its 24 h window has a tail OUTSIDE it (day 17 by ~2 h, day 13 by one
    chunk). Those records exist in /context and belong to no consolidated window, so the
    expected count is the plan's `captions_in_window`, and the expected transcript count is
    the number of chunks whose own t_start is in the window;
  * consecutive tour days in DIFFERENT timezones have overlapping 04:00-local windows --
    day 12 (Chicago) and day 13 (New York) overlap by one hour, so day 12's window read
    legitimately returns 60 of day 13's captions. They are counted as FOREIGN and reported;
    Arm 1's record-provider drops them by construction (it filters on the day's own caption
    timestamps), and this is the check that would notice if it ever stopped.

Also reports, because the exit criterion asks for them: distinct pipeline dialects (one per
day is healthy), whether every caption sits alone in its 60 s day-log segment (the property
the rule-bend depends on), and one caption + one transcript verbatim for the spot-check.
"""
from __future__ import annotations

import argparse
import collections
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx


def _epoch(raw: str) -> float:
    text = raw.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def fetch(storage_url: str, user_id: str, start: str, end: str, timeout: float) -> list[dict]:
    with httpx.Client(timeout=timeout) as client:
        resp = client.get(f"{storage_url}/context/records",
                          params={"user_id": user_id, "from": start, "to": end})
        resp.raise_for_status()
        body = resp.json()
    return body["records"] if isinstance(body, dict) and "records" in body else body


def expectations(plan: dict) -> tuple[dict[int, set[str]], dict[int, dict]]:
    """Per day: the caption timestamps the window should return, and the chunk facts."""
    by_day: dict[int, set[str]] = collections.defaultdict(set)
    with Path(plan["captions_file"]).open() as fh:
        for line in fh:
            if line.strip():
                row = json.loads(line)
                by_day[row["day"]].add(row["t_start"])
    windows = {int(d): (_epoch(i["window_start_utc"]), _epoch(i["window_end_utc"]))
               for d, i in plan["days"].items()}
    expected: dict[int, set[str]] = {}
    for day, starts in by_day.items():
        lo, hi = windows[day]
        expected[day] = {t for t in starts if lo <= _epoch(t) < hi}
    chunks: dict[int, dict] = {}
    for day, info in plan["days"].items():
        lo, hi = windows[int(day)]
        rows = [json.loads(x) for x in Path(info["replay_file"]).read_text().splitlines() if x.strip()]
        # A transcript carries its chunk's C1 t_start verbatim, so the chunk starts that
        # fall in the window ARE the transcript timestamps this day should return. Set,
        # not count: day 12's window also holds three of day 13's chunks.
        chunks[int(day)] = {
            "replayed": len(rows),
            "starts": {r["t_start"] for r in rows if lo <= _epoch(r["t_start"]) < hi},
        }
    return expected, chunks


def check_day(records: list[dict], info: dict, expected: set[str], chunks: dict,
              segment_seconds: int) -> dict:
    captions = [r for r in records if r["content"]["kind"] == "caption"]
    mine = [r for r in captions if r["t_start"] in expected]
    got = {r["t_start"] for r in mine}
    start = _epoch(info["window_start_utc"])
    buckets = collections.Counter(
        int((_epoch(r["t_start"]) - start) // segment_seconds) for r in mine)
    transcripts = [r for r in records if r["content"]["kind"] == "transcript"]
    mine_asr = [r for r in transcripts if r["t_start"] in chunks["starts"]]
    speakers = {s.get("speaker") for r in mine_asr
                for s in (r["content"].get("segments") or [])}
    kinds = collections.Counter(r["content"]["kind"] for r in records)
    return {
        "kinds": dict(kinds),
        "captions_expected": len(expected), "captions_matched": len(got),
        "captions_missing": len(expected - got),
        "captions_foreign": len(captions) - len(mine),   # a neighbouring day's overlap
        "transcripts": len(mine_asr),
        "transcripts_expected": len(chunks["starts"]),
        "transcripts_foreign": len(transcripts) - len(mine_asr),
        "chunks_replayed": chunks["replayed"],
        "chunks_seen": len({r["source"]["chunk_id"] for r in records}),
        "pipeline_versions": dict(collections.Counter(r["pipeline_version"] for r in records)),
        "caption_segments": len(buckets),
        "captions_sharing_a_segment": sum(n - 1 for n in buckets.values() if n > 1),
        "transcript_chars": sum(len(r["content"]["text"]) for r in mine_asr),
        "empty_transcripts": sum(1 for r in mine_asr if not r["content"]["text"].strip()),
        "distinct_speaker_labels": sorted(x for x in speakers if x),
        "ok": (got == expected
               and {r["t_start"] for r in mine_asr} == chunks["starts"]
               and sum(n - 1 for n in buckets.values() if n > 1) == 0
               and len(set(r["pipeline_version"] for r in records)) == 1),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", default="~/phase3/plan/plan.json")
    ap.add_argument("--storage-url", default="http://127.0.0.1:8083")
    ap.add_argument("--days", default="", help="default: every day in the plan")
    ap.add_argument("--segment-seconds", type=int, default=60)
    ap.add_argument("--timeout", type=float, default=600.0)
    ap.add_argument("--out", default="", help="also write the report as JSON")
    ap.add_argument("--samples", type=int, default=1)
    args = ap.parse_args()

    plan = json.loads(Path(args.plan).expanduser().read_text())
    expected, chunks = expectations(plan)
    days = [d.strip() for d in args.days.split(",") if d.strip()] or list(plan["days"])
    report: dict = {"storage_url": args.storage_url, "user_id": plan["user_id"], "days": {}}
    samples: dict = {}
    for day in days:
        info = plan["days"][str(day)]
        records = fetch(args.storage_url, plan["user_id"], info["window_start_utc"],
                        info["window_end_utc"], args.timeout)
        report["days"][str(day)] = check_day(records, info, expected[int(day)],
                                             chunks[int(day)], args.segment_seconds)
        for kind in ("caption", "transcript"):
            if not samples.get(kind):
                samples[kind] = [r for r in records if r["content"]["kind"] == kind
                                 and r["content"]["text"].strip()][:args.samples]
    report["samples"] = samples
    report["all_ok"] = all(d["ok"] for d in report["days"].values())

    header = (f"{'day':>4}{'captions':>11}{'expected':>10}{'missing':>9}{'foreign':>9}"
              f"{'transcripts':>13}{'asr chars':>11}{'seg dup':>9}{'ok':>4}")
    print(header)
    print("-" * len(header))
    for day, row in report["days"].items():
        print(f"{day:>4}{row['captions_matched']:>11}{row['captions_expected']:>10}"
              f"{row['captions_missing']:>9}{row['captions_foreign']:>9}"
              f"{row['transcripts']:>6}/{row['transcripts_expected']:<6}"
              f"{row['transcript_chars']:>11}{row['captions_sharing_a_segment']:>9}"
              f"{('y' if row['ok'] else 'NO'):>4}")
    print(f"\nall_ok={report['all_ok']}")
    for kind, picked in samples.items():
        for rec in picked:
            print(f"\n--- sample {kind} ({rec['t_start']} -> {rec['t_end']}, "
                  f"{rec['pipeline_version']}) ---")
            print(rec["content"]["text"][:1200])
    if args.out:
        Path(args.out).expanduser().write_text(json.dumps(report, indent=1))
    return 0 if report["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
