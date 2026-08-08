"""Day-log construction: C2 records → segment rows → scene blocks.

The day log is the ONLY interface between ingest and consolidation (the
research design's frozen-schema rule), so field names here deliberately match
the day-log schema: segment rows carry seg_id / t_start / t_end / caption / asr
/ ocr / quality; block rows carry block_id / seg_ids / text / anchors /
quality. The trainer seam renders these to segments.jsonl / blocks.jsonl
byte-compatible with what the ported research code consumes.

The join is a TIME-WINDOW join, not a per-chunk one: chunk spans and segment
buckets are independent grids — audio chunks are VAD-carved (5–30 s) while a
bucket is ~10 s — so one bucket gathers every C2 record, or diarized sub-span,
whose t_start falls inside it. Records are attributed by t_start.

THE BUCKET GRID IS GLOBAL, NOT WINDOW-RELATIVE (D18 rule 2 — see
`_bucket_index`). This module is the PARITY REFERENCE that storage's
`materialize_daylog` is diffed against (storage CHARTER M9), and a window-relative
grid would make that diff true only for a window whose origin happened to sit on a
segment boundary. Real windows are `[watermark, now−δ)` at second granularity, so
nine origins in ten are misaligned, and under a window-relative grid the two
renderers group records into DIFFERENT segments — different block text, i.e.
different training text. Measured, not reasoned: with the M9 fixture's origin
shifted by 1–9 s, tier A failed for every one of the nine.

ONE deliberate difference from storage's materializer remains, and it is not a
defect: MEMBERSHIP. This path filters on EVENT time (`in_window(t_start, win)`)
because its callers hold an event-time window on purpose; storage selects on its
own `updated_at` axis. That is D18 rule 1, and it is what the M9 fixture
neutralises (N1) rather than something either side should change.

Block text is labelled anchored lines (anchor line + Caption / Heard / World
text). The research prose renderer (render_block's structured fields + in-text
anchor weaving) lives in morpheus/blocks.py; the seam and field names are already
its shape.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .window import Window, in_window


@dataclass
class Segment:
    seg_id: str
    t_start: str
    t_end: str
    caption: list[str] = field(default_factory=list)
    asr: list[dict[str, Any]] = field(default_factory=list)   # {spk, text, t}
    ocr: list[str] = field(default_factory=list)
    quality: float | None = None   # C2 v0 has no quality field yet; None = not scored
    # D17: the zone the CAPTURING DEVICE reported for the records in this bucket
    # (C2 source.device_tz). None when no contributing record carried one, which
    # is what makes the renderer fall back to the window's home_tz. This is the
    # field that makes anchor lines correct for a day spent in another zone.
    tz: str | None = None

    def is_empty(self) -> bool:
        return not (self.caption or self.asr or self.ocr)


@dataclass
class Block:
    block_id: str
    seg_ids: list[str]
    text: str
    anchors: dict[str, Any]
    quality: float | None = None


@dataclass
class DayLog:
    window_id: str
    user_id: str
    segments: list[Segment]
    blocks: list[Block]
    # C10 v1's `content_fingerprint`, when the day-log came from storage. Computed
    # BY WHOEVER RENDERS and only ever compared to ITSELF across runs (it is the
    # cycle's day-log stage key, not a cross-backend equality claim), so the fetch
    # carries the server's value through rather than re-deriving one. None on the
    # locally-built path, where `daylog_fingerprint` computes it.
    content_fingerprint: str | None = None


def _parse_ts(raw: str) -> datetime:
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _bucket_index(t: datetime, segment_seconds: int) -> int:
    """The GLOBAL epoch grid — `floor(epoch_seconds / segment_seconds)`.

    The window origin is deliberately not a parameter. It used to be
    (`floor((t - win.start_utc)/segment_seconds)`), and that is the same partition
    of the timeline ONLY when the origin lands exactly on a segment boundary.
    Storage's materializer buckets on this global grid (D18 rule 2: a
    window-relative index goes negative on the ingest axis), so for any other
    origin the two renderers put the same records in different segments and
    rendered different block text — the one thing M9 tier A says must be
    byte-identical.

    Storage's grid is the one that had to win: it is the production path, its
    reason (negative indices on the ingest axis) is structural, and this module is
    the reference measured against it. A bucket is also now stable under
    re-materialization, because it no longer depends on which window collected the
    record.
    """
    return int(t.timestamp() // segment_seconds)


def build_daylog(records: list[dict[str, Any]], win: Window, *,
                 segment_seconds: int = 10, block_segments: int = 12) -> DayLog:
    """Bucket C2 records into ~10 s segment rows, then group consecutive
    non-empty segments into ~2 min scene blocks."""
    buckets: dict[int, Segment] = {}

    def seg_for(idx: int) -> Segment:
        if idx not in buckets:
            start = idx * segment_seconds
            end = start + segment_seconds
            buckets[idx] = Segment(
                # seg_id is assigned after the buckets are sorted (see below): it
                # is the ORDINAL in the day-log, not the epoch bucket index, which
                # at 10 s buckets is a nine-digit number that would blow the {:05d}
                # width. Storage labels segments by the same rule, for the same
                # reason, and the label is a REPRESENTATION choice either way —
                # nothing parses a seg_id (the only reader is a histogram's
                # `len(b.seg_ids)` in scripts/phase3_daylog.py).
                seg_id="",
                t_start=datetime.fromtimestamp(start, tz=timezone.utc).isoformat(),
                t_end=datetime.fromtimestamp(end, tz=timezone.utc).isoformat(),
            )
        return buckets[idx]

    def note_tz(seg: Segment, rec: dict[str, Any]) -> Segment:
        """Stamp the bucket with the capturing device's zone (first writer wins).

        A ~10 s bucket cannot straddle a real zone change, so first-wins is not a
        lossy choice here; it just avoids re-deciding per contributing record."""
        if seg.tz is None:
            device_tz = (rec.get("source") or {}).get("device_tz")
            if device_tz:
                seg.tz = device_tz
        return seg

    for rec in records:
        content = rec.get("content", {})
        kind = content.get("kind")
        text = (content.get("text") or "").strip()
        subsegs = content.get("segments") or []
        if kind == "transcript" and subsegs:
            # Diarized sub-spans land in their OWN buckets by their own t_start —
            # membership is judged per sub-span, never by the parent chunk's
            # t_start (a VAD chunk starting just before the boundary must not
            # drag its in-window speech out of the window).
            for sub in subsegs:
                sub_text = (sub.get("text") or "").strip()
                if not sub_text:
                    continue
                st = _parse_ts(sub["t_start"])
                if not in_window(st, win):
                    continue
                note_tz(seg_for(_bucket_index(st, segment_seconds)), rec).asr.append(
                    {"spk": sub.get("speaker"), "text": sub_text, "t": sub["t_start"]})
            continue
        t0 = _parse_ts(rec["t_start"])
        if not in_window(t0, win):
            continue  # attribution rule: t_start decides membership
        if not text:
            continue
        seg = note_tz(seg_for(_bucket_index(t0, segment_seconds)), rec)
        if kind == "transcript":
            seg.asr.append({"spk": None, "text": text, "t": rec["t_start"]})
        elif kind == "ocr":
            seg.ocr.append(text)
        else:  # caption | text
            seg.caption.append(text)

    segments = [buckets[i] for i in sorted(buckets)]
    for ordinal, seg in enumerate(segments):
        seg.seg_id = f"{win.window_id}_s{ordinal:05d}"
    non_empty = [s for s in segments if not s.is_empty()]

    # A block is a run of TEMPORALLY ADJACENT segments (≤ block_segments long);
    # a camera-off gap starts a new block, so one anchor line never spans hours
    # of silence (the research's scene-boundary rule, gap-only in v0).
    max_gap_s = 6 * segment_seconds
    blocks: list[Block] = []
    group: list[Segment] = []
    for seg in non_empty:
        if group:
            gap = (_parse_ts(seg.t_start) - _parse_ts(group[-1].t_end)).total_seconds()
            if len(group) >= block_segments or gap > max_gap_s:
                blocks.append(_render_block(win, len(blocks), group))
                group = []
        group.append(seg)
    if group:
        blocks.append(_render_block(win, len(blocks), group))
    return DayLog(window_id=win.window_id, user_id=win.user_id,
                  segments=segments, blocks=blocks)


def _block_zone(win: Window, group: list[Segment]) -> ZoneInfo:
    """The zone this block's anchor line is written in (D17).

    Preference order — device-reported, then the window's fallback:
      1. the `device_tz` the CAPTURING DEVICE reported for the block's first
         segment: the only value that is right when the user was travelling,
         because it says where they physically were at that moment;
      2. `win.tz` — the user's profile `home_tz`, for records captured before
         clients reported a zone, or by a device that can't determine one.

    The first segment decides, matching the anchor line's own start time. An
    unknown/garbage zone id must never sink a whole night's training run, so an
    unresolvable value degrades to the window fallback rather than raising.
    """
    for seg in group:
        if seg.tz:
            try:
                return ZoneInfo(seg.tz)
            except (ZoneInfoNotFoundError, ValueError):
                break  # bad id from a device — fall through to the window zone
    return ZoneInfo(win.tz)


def _render_block(win: Window, index: int, group: list[Segment]) -> Block:
    """v0 labeled-lines renderer; anchors written IN the text (never metadata-only).
    Times are rendered in the WEARER'S timezone — pairing the local date with UTC
    clock readings would anchor a moment up to a day away from the event."""
    zone = _block_zone(win, group)
    start_local = _parse_ts(group[0].t_start).astimezone(zone)
    end_local = _parse_ts(group[-1].t_end).astimezone(zone)
    local_date = start_local.date().isoformat()
    captions = [c for s in group for c in s.caption]
    heard = [f"{a['spk'] or 'someone'}: {a['text']}" if a.get("spk") else a["text"]
             for s in group for a in s.asr]
    world_text = [o for s in group for o in s.ocr]
    t0 = start_local.strftime("%H:%M")
    t1 = end_local.strftime("%H:%M")
    lines = [f"On {local_date}, around {t0}–{t1} local time:"]
    if captions:
        lines.append("Scene: " + " ".join(captions))
    if heard:
        lines.append("Heard: " + " | ".join(heard))
    if world_text:
        lines.append("World text (OCR): " + " | ".join(world_text))
    scored = [s.quality for s in group if s.quality is not None]
    return Block(
        block_id=f"{win.window_id}_b{index:04d}",
        seg_ids=[s.seg_id for s in group],
        text="\n".join(lines),
        anchors={"date": local_date, "place": None},
        quality=min(scored) if scored else None,
    )


def corpus_blocks(daylog: DayLog, quality_min: float) -> list[Block]:
    """Blocks eligible for amplification: the quality gate lives HERE (day log
    keeps everything; low-quality rows are excluded from training, not from the
    record). Unscored (None) blocks pass — C2 carries no quality field, and
    unscored is not the same as failing."""
    return [b for b in daylog.blocks
            if b.quality is None or b.quality >= quality_min]
