"""Day-log fetch — the C10-evolved read.

The lean loop's second verb: continuum asks for "the day-log for (user, window)"
and gets the rendered segment/block day-log back. It does NOT build the day-log
itself — that is storage's job (a scheduled materialization over C2, served by
the evolved C10). This client is where that boundary lives.

Two implementations, one interface:

  LocalDayLogClient   builds the day-log HERE, exactly as before — records from a
                      provider (synthetic day, or the beta /context range read)
                      through the same `build_daylog` + renderer. Byte-identical
                      to the pre-2c inline path; the parity contract is unchanged.
  (future) HttpDayLogClient  GETs an already-materialized day-log from storage.

The `RecordProvider` seam is why the local client can stand in for storage without
continuum knowing which it is talking to: records→day-log is fully behind the
interface. When storage owns materialization, the provider disappears and the
HTTP client returns storage's day-log directly.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any, Callable, Protocol

from ..daylog import DayLog, build_daylog, corpus_blocks
from ..daylog import Block
from ..window import Window

RecordProvider = Callable[[Window], list[dict[str, Any]]]


def daylog_fingerprint(daylog: DayLog) -> str:
    """A stable content hash of the day-log.

    The cycle keys its daylog stage on this. Hashing the day-LOG rather than the
    raw records is both correct and forward-looking: two different record sets
    that render to the same day-log are legitimately the same night's input, and
    when storage owns materialization the raw records never reach continuum at
    all — only the day-log does."""
    payload = json.dumps(
        {"window_id": daylog.window_id, "user_id": daylog.user_id,
         "segments": [asdict(s) for s in daylog.segments],
         "blocks": [asdict(b) for b in daylog.blocks]},
        sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


class DayLogClient(Protocol):
    def fetch_daylog(self, win: Window) -> DayLog:
        """The rendered segment/block day-log for this window."""

    def eligible_blocks(self, daylog: DayLog, quality_min: float) -> list[Block]:
        """The blocks that pass the amplification quality gate."""

    def render(self, daylog: DayLog, outdir) -> dict[str, str]:
        """Materialize the day-log as trainer-seam files (segments/blocks/day.txt)."""

    def fingerprint(self, daylog: DayLog) -> str:
        """A stable content hash, for the cycle's idempotency key."""


class LocalDayLogClient:
    """Local backend: materialize the day-log from a record provider, in-process.

    Segmentation is fixed at construction because the day-log's shape is
    recipe-versioned (storage will serve a "recipe-versioned format" per the
    storage charter). The local client reproduces exactly the shape the pre-2c
    cycle built — same buckets, same blocks, same rendered bytes.
    """

    def __init__(self, record_provider: RecordProvider, *,
                 segment_seconds: int = 10, block_segments: int = 12):
        self._records = record_provider
        self.segment_seconds = segment_seconds
        self.block_segments = block_segments

    @classmethod
    def from_records(cls, records: list[dict[str, Any]], *,
                     segment_seconds: int = 10, block_segments: int = 12) -> "LocalDayLogClient":
        """A client backed by a fixed record list — the shape a test or a
        single-window run holds records already in hand."""
        return cls(lambda _win: records, segment_seconds=segment_seconds,
                   block_segments=block_segments)

    def fetch_daylog(self, win: Window) -> DayLog:
        return build_daylog(self._records(win), win,
                            segment_seconds=self.segment_seconds,
                            block_segments=self.block_segments)

    def eligible_blocks(self, daylog: DayLog, quality_min: float) -> list[Block]:
        return corpus_blocks(daylog, quality_min)

    def render(self, daylog: DayLog, outdir) -> dict[str, str]:
        from ..renderer import render_daylog_files
        return render_daylog_files(daylog, outdir)

    def fingerprint(self, daylog: DayLog) -> str:
        return daylog_fingerprint(daylog)
