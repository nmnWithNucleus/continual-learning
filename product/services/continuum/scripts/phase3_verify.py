#!/usr/bin/env python
"""Phase-3a exit check: is the replayed day really queryable from /context?

The bridge's claim is narrow and has to be checked as stated: for each replayed day,
REAL C2 records — caption AND transcript — come back from the C10 range read by
(user, window), on the spine the plan laid down. So this asks storage the same
question continuum's nightly asks (`GET /context/records?user_id=&from=&to=`), and
reports per day:

  * how many caption / transcript records land in the window, against what the plan
    predicted (a shortfall means chunks failed, not that a day was quiet);
  * how many distinct C1 chunks and pipeline dialects produced them (one dialect per
    day is the healthy answer: a second one means the config moved mid-run);
  * whether every caption sits alone in its 60 s day-log segment — the property the
    rule-bend depends on;
  * one caption and one transcript, verbatim, for the human spot-check the exit
    criterion actually asks for.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
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


def check_day(records: list[dict], info: dict, segment_seconds: int) -> dict:
    kinds = collections.Counter(r["content"]["kind"] for r in records)
    dialects = collections.Counter(r["pipeline_version"] for r in records)
    chunks = {r["source"]["chunk_id"] for r in records}
    start = _epoch(info["window_start_utc"])
    buckets = collections.Counter(
        int((_epoch(r["t_start"]) - start) // segment_seconds)
        for r in records if r["content"]["kind"] == "caption")
    shared = sum(n - 1 for n in buckets.values() if n > 1)
    asr_chars = sum(len(r["content"]["text"]) for r in records
                    if r["content"]["kind"] == "transcript")
    speakers = {s.get("speaker") for r in records if r["content"]["kind"] == "transcript"
                for s in (r["content"].get("segments") or [])}
    return {
        "captions": kinds.get("caption", 0), "transcripts": kinds.get("transcript", 0),
        "captions_expected": info["captions_in_window"],
        "chunks_seen": len(chunks), "chunks_expected": info["chunks"],
        "pipeline_versions": dict(dialects),
        "caption_segments": len(buckets), "captions_sharing_a_segment": shared,
        "transcript_chars": asr_chars,
        "distinct_speaker_labels": sorted(x for x in speakers if x),
        "ok": (kinds.get("caption", 0) == info["captions_in_window"]
               and len(chunks) == info["chunks"] and shared == 0),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", default="~/phase3/plan/plan.json")
    ap.add_argument("--storage-url", default="http://127.0.0.1:8083")
    ap.add_argument("--days", default="", help="default: every day in the plan")
    ap.add_argument("--segment-seconds", type=int, default=60)
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--out", default="", help="also write the report as JSON")
    ap.add_argument("--samples", type=int, default=1)
    args = ap.parse_args()

    plan = json.loads(Path(args.plan).expanduser().read_text())
    days = [d.strip() for d in args.days.split(",") if d.strip()] or list(plan["days"])
    report: dict = {"storage_url": args.storage_url, "user_id": plan["user_id"], "days": {}}
    samples: dict = {}
    for day in days:
        info = plan["days"][str(day)]
        records = fetch(args.storage_url, plan["user_id"], info["window_start_utc"],
                        info["window_end_utc"], args.timeout)
        report["days"][str(day)] = check_day(records, info, args.segment_seconds)
        if not samples:
            for kind in ("caption", "transcript"):
                picked = [r for r in records if r["content"]["kind"] == kind
                          and r["content"]["text"].strip()][:args.samples]
                samples[kind] = picked
    report["samples"] = samples
    report["all_ok"] = all(d["ok"] for d in report["days"].values())

    header = (f"{'day':>4}{'chunks':>9}{'captions':>11}{'expected':>10}"
              f"{'transcripts':>13}{'asr chars':>11}{'seg dup':>9}{'ok':>4}")
    print(header)
    print("-" * len(header))
    for day, row in report["days"].items():
        print(f"{day:>4}{row['chunks_seen']:>4}/{row['chunks_expected']:<4}"
              f"{row['captions']:>11}{row['captions_expected']:>10}"
              f"{row['transcripts']:>13}{row['transcript_chars']:>11}"
              f"{row['captions_sharing_a_segment']:>9}{('y' if row['ok'] else 'NO'):>4}")
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
