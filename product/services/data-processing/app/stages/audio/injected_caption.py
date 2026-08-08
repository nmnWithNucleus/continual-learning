"""injected_caption — replay-injected descriptions: built + tested, NOT
REGISTERED.

NO ``@register_stage`` on purpose: dogfood-only (the Phase-3 replay drill needs
real caption-shaped data on the audio time spine), the ratified dialect
excludes it, and C2 v1 has no ``injected_caption`` slot. Enabling it is a
CODE-LEVEL graph composition — construct the stage WITH ITS INDEX PATH and
resolve an explicit stage set (plus the additive contract slot). The v0 env
knobs are dead (L4): INJECT_CAPTION_BACKEND died into the ``index`` backend
name in the dialect segment, INJECT_CAPTION_INDEX became the constructor
argument.

Ported v0 semantics (the old injected-caption stage, verbatim where it matters):
  * THE JOIN IS WALL-CLOCK, and it has to be — a replayed chunk's ids are
    freshly-minted ULIDs, so the shared spine is the only join key. A chunk's
    captions are the index rows whose ``t_start`` falls inside the chunk's C1
    span (HALF-OPEN [t_start, t_end), the day-log's attribution rule), found
    by bisect over the sorted row starts.
  * the emitted splits carry the index's OWN time strings VERBATIM (v0's rule:
    they are the spine recording stamped from; re-rendering them is how a
    timezone or trailing-Z difference silently moves a record out of its
    window).
  * blank-text rows are skipped; a missing/malformed index RAISES rather than
    yielding an empty result (a silent zero-caption day reads exactly like a
    day nobody described); the parsed index is cached per (mtime_ns, size) so
    a redeployed file is picked up but a 15 MB index parses once, not per
    chunk.

Slot value: ``{"splits": [{t_start, t_end, value}...]}``; ``[]`` = ran, no
described window in this span (honest empty claim, L11).
"""
from __future__ import annotations

import json
from bisect import bisect_left
from pathlib import Path
from typing import Any, Optional

from ...timeutil import parse_rfc3339
from ...stagegraph.stage import Backend, Stage, StageContext, StageOutput


def _epoch(value: str) -> float:
    return parse_rfc3339(value).timestamp()


class InjectedCaptionStage(Stage):
    name = "injected_caption"
    modality = "audio"
    stage_version = 1
    backend = Backend("index", 1)
    needs = ()              # reads only the C1 span
    required = False
    byte_budget = 16384     # ~12 five-second windows x ~200-char descriptions
                            # in a 60 s chunk stays well under 4 KB
    server = ""             # no model: the index file is the backend
    # L10: the dogfood day-log renders injected descriptions as Scene lines.
    consumer = "daylog:scene"

    def __init__(self, index_path: str, *, backend: Optional[Backend] = None,
                 experiment: Optional[str] = None) -> None:
        super().__init__(backend=backend, experiment=experiment)
        if not isinstance(index_path, str) or not index_path.strip():
            raise ValueError(
                "InjectedCaptionStage needs a non-empty index_path (the v0 "
                "INJECT_CAPTION_INDEX env knob is dead — the path is code)"
            )
        self.index_path = index_path.strip()
        # (mtime_ns, size) -> (sorted start epochs, rows); one entry only.
        self._cache: Optional[tuple[tuple[int, int],
                                    tuple[list[float], list[dict]]]] = None

    # ---- index ------------------------------------------------------------------
    def _load_index(self) -> tuple[list[float], list[dict[str, Any]]]:
        """The caption index as (sorted start epochs, rows). Raises loudly on a
        missing/malformed file — v0's rule: injection was asked for, so a
        silent empty result is indistinguishable from an undescribed day."""
        stat = Path(self.index_path).stat()   # missing file raises here
        key = (stat.st_mtime_ns, stat.st_size)
        if self._cache is not None and self._cache[0] == key:
            return self._cache[1]
        rows = [json.loads(line)
                for line in Path(self.index_path).read_text().splitlines()
                if line.strip()]
        rows.sort(key=lambda r: _epoch(r["t_start"]))
        entry = ([_epoch(r["t_start"]) for r in rows], rows)
        self._cache = (key, entry)
        return entry

    # ---- run --------------------------------------------------------------------
    def run_sync(self, ctx: StageContext) -> StageOutput:
        starts, rows = self._load_index()
        lo = bisect_left(starts, _epoch(ctx.c1["t_start"]))
        hi = bisect_left(starts, _epoch(ctx.c1["t_end"]))
        splits: list[dict[str, Any]] = []
        for row in rows[lo:hi]:
            text = (row.get("text") or "").strip()
            if not text:
                continue
            splits.append({
                "t_start": row["t_start"],   # the index's strings, VERBATIM
                "t_end": row["t_end"],
                "value": text,
            })
        return StageOutput(value={"splits": splits})
