"""Audio SIDECAR stage: replay-injected description captions (Phase-3 DP dogfood).

The dogfood needs REAL C2 caption records on the same time spine as the audio, without
a real keyframe captioner (that is the deferred true caption-shape test). This stage
supplies them the way a captioner would: given the chunk's C1 span, it emits one
``kind="caption"`` unit per description window inside it, each carrying its OWN
sub-span. Exactly the acoustic sidecar's shape (``needs=()``, independent of ASR, off
by default), one step further along the seam the video captioner already uses -- MANY
units with per-unit sub-spans (``ProcessedUnit.t_start/t_end``), so a chunk's captions
land in their own day-log segments instead of colliding on the shared chunk span. No C2
schema change: C2 already carries per-record timestamps and 'caption' is a frozen
content.kind.

THE JOIN IS WALL-CLOCK, and it has to be. A replayed chunk's ``chunk_id`` / ``stream_id``
are freshly-minted ULIDs -- by design, since the whole point is that a replayed chunk is
indistinguishable from a live one -- so there is no id to look a description up by, and
inventing a contract field to carry one is precisely what the design forbade. What both
services DO share is the spine: recording stamps each chunk from the replay plan, and
this stage reads the caption index built from the same plan. A chunk's captions are the
index rows whose ``t_start`` falls inside its C1 span (half-open, the same attribution
rule the day-log uses).

Off unless ``INJECT_CAPTION_BACKEND=index``: with no backend selected the stage is a
no-op and the audio pipeline is byte-identical to a live capture's. It deliberately does
NOT declare a version_fragment -- like the acoustic sidecar, it only ADDS records, and
forking the whole chunk's dialect (which would re-key the ASR primary's record too) on a
sidecar toggle is what that precedent declines to do. Replay provenance lives where the
design put it: ``user_id``.
"""
from __future__ import annotations

import json
import os
from bisect import bisect_left
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...processing.base import ProcessedContent, ProcessedUnit, empty_enrichments
from ...stagegraph import Stage, StageContext, StageResult, register_stage

BACKENDS = ("off", "index")
# (path, mtime_ns, size) -> the index, so a re-deployed index is picked up but a
# 12k-row/15 MB file is parsed once per worker process, not once per chunk.
_CACHE: dict[tuple, tuple[list[float], list[dict[str, Any]]]] = {}


def _backend() -> str:
    value = (os.getenv("INJECT_CAPTION_BACKEND", "off") or "off").strip().lower()
    return value if value in BACKENDS else "off"


def _index_path() -> str:
    return os.getenv("INJECT_CAPTION_INDEX", "").strip()


def _epoch(raw: str) -> float:
    text = raw.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def load_index(path: str) -> tuple[list[float], list[dict[str, Any]]]:
    """The caption index as (sorted start epochs, rows) for a bisect lookup.

    A malformed or missing index RAISES rather than yielding an empty result: the stage
    is only enabled when an operator asked for injection, and silently producing zero
    captions would look exactly like a day with no descriptions.
    """
    stat = Path(path).stat()
    key = (path, stat.st_mtime_ns, stat.st_size)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    for row in rows:
        row["_start"] = _epoch(row["t_start"])
    rows.sort(key=lambda r: r["_start"])
    entry = ([r["_start"] for r in rows], rows)
    _CACHE.clear()          # one index per process; never grow unbounded on redeploys
    _CACHE[key] = entry
    return entry


@register_stage
class InjectedCaptionStage(Stage):
    name = "injected_caption"
    modality = "audio"
    kind = "sidecar"
    needs = ()          # reads only the C1 span -- runs concurrently with ASR
    order = 40          # after acoustic (30); assembly order only

    def enabled(self, settings) -> bool:
        return _backend() == "index" and bool(_index_path())

    def run_sync(self, ctx: StageContext) -> StageResult:
        starts, rows = load_index(_index_path())
        lo = bisect_left(starts, _epoch(ctx.c1["t_start"]))
        hi = bisect_left(starts, _epoch(ctx.c1["t_end"]))
        units = []
        for row in rows[lo:hi]:
            text = (row.get("text") or "").strip()
            if not text:
                continue
            units.append(ProcessedUnit(
                content=ProcessedContent(kind="caption", text=text),
                enrichments=empty_enrichments(),   # own empty block; NOT the diarized one
                # The index's own strings, verbatim: they are the spine recording
                # stamped from, and re-rendering them here is how a timezone or
                # trailing-Z difference silently moves a record out of its window.
                discriminator=f"injcap-{row['window']}",
                t_start=row["t_start"],
                t_end=row["t_end"],
            ))
        return StageResult(units=units)
