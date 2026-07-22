"""Day-log construction: C2 records → segment rows → scene blocks.

The day log is the ONLY interface between ingest and consolidation (the
research design's frozen-schema rule), so field names here deliberately match
the engram schema: segment rows carry seg_id / t_start / t_end / caption / asr
/ ocr / quality; block rows carry block_id / seg_ids / text / anchors /
quality. The trainer seam renders these to segments.jsonl / blocks.jsonl
byte-compatible with what the ported research code consumes.

The join is a TIME-WINDOW join, not a per-chunk one: our audio chunks are
VAD-carved (5–30 s) and video captions are per-keyframe records, so one ~10 s
segment gathers every C2 record (or diarized sub-span) whose t_start falls in
its bucket. Records are attributed by t_start (window rule).

v0 renderer note: block text is labeled anchored lines (anchor line + Caption /
Heard / World text). The research prose renderer (render_block's structured
fields + in-text anchor weaving) ports with ws-engram-port; the seam and field
names are already its shape.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

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


def _parse_ts(raw: str) -> datetime:
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _bucket_index(t: datetime, win: Window, segment_seconds: int) -> int:
    return int((t - win.start_utc).total_seconds() // segment_seconds)


def build_daylog(records: list[dict[str, Any]], win: Window, *,
                 segment_seconds: int = 10, block_segments: int = 12) -> DayLog:
    """Bucket C2 records into ~10 s segment rows, then group consecutive
    non-empty segments into ~2 min scene blocks."""
    buckets: dict[int, Segment] = {}

    def seg_for(idx: int) -> Segment:
        if idx not in buckets:
            start = win.start_utc.timestamp() + idx * segment_seconds
            end = start + segment_seconds
            buckets[idx] = Segment(
                seg_id=f"{win.window_id}_s{idx:05d}",
                t_start=datetime.fromtimestamp(start, tz=timezone.utc).isoformat(),
                t_end=datetime.fromtimestamp(end, tz=timezone.utc).isoformat(),
            )
        return buckets[idx]

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
                seg_for(_bucket_index(st, win, segment_seconds)).asr.append(
                    {"spk": sub.get("speaker"), "text": sub_text, "t": sub["t_start"]})
            continue
        t0 = _parse_ts(rec["t_start"])
        if not in_window(t0, win):
            continue  # attribution rule: t_start decides membership
        if not text:
            continue
        seg = seg_for(_bucket_index(t0, win, segment_seconds))
        if kind == "transcript":
            seg.asr.append({"spk": None, "text": text, "t": rec["t_start"]})
        elif kind == "ocr":
            seg.ocr.append(text)
        else:  # caption | text
            seg.caption.append(text)

    segments = [buckets[i] for i in sorted(buckets)]
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


def _render_block(win: Window, index: int, group: list[Segment]) -> Block:
    """v0 labeled-lines renderer; anchors written IN the text (never metadata-only).
    Times are rendered in the WEARER'S timezone — pairing the local date with UTC
    clock readings would anchor a moment up to a day away from the event."""
    zone = ZoneInfo(win.tz)
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
    record). Unscored (None) blocks pass — C2 v0 carries no quality yet."""
    return [b for b in daylog.blocks
            if b.quality is None or b.quality >= quality_min]
