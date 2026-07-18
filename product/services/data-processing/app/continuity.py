"""Ingest continuity observation — the break/dup detector behind "zero silent loss".

C1 delivery is at-least-once and ordered only by the producer-assigned ``sequence``,
so the receiving side is where loss becomes OBSERVABLE: a sequence that never
arrives is a silent gap unless someone is watching. ``ContinuityTracker`` is that
watcher — updated on EVERY schema-valid ``/ingest`` delivery (fresh, dedup-hit, and
in-flight-dup paths alike), and read back via ``GET /continuity`` so recording's gap
report can close the loop across both legs.

Per stream it keeps:
  * identity (``user_id``/``device_id``/``modality``) + ``first_seen``/``last_seen``
    (RFC3339, stamped by the caller so time is injectable);
  * ``max_sequence`` and ``received`` — the TOTAL delivery count, duplicates and
    conflicts included (unique coverage is the interval mass, below);
  * seen sequences as sorted, merged, non-adjacent ``[lo, hi]`` runs — compact for
    dense streams (a million in-order chunks is one run), and every hole between
    runs is by construction a real gap;
  * ``duplicate_deliveries`` — the same ``(sequence, chunk_id)`` re-seen: expected
    at-least-once noise, counted but not alarming;
  * ``sequence_conflicts`` — the same ``sequence`` claimed by a DIFFERENT
    ``chunk_id``: an anomaly (two distinct chunks in one slot) flagged loudly via a
    warning log plus a capped per-stream sample list.

``missing`` in a report is every gap below ``max_sequence``, INCLUDING the leading
gap ``[0, first-1]`` when the first run starts above 0 — per C1, sequences start at
0, so a non-zero first-seen sequence IS lost chunks.

Memory posture mirrors ``DedupStore``: in-memory, single-process, dev-scale. The
per-sequence first-``chunk_id`` map (what tells a duplicate from a conflict) grows
with unique sequences seen — honest and unbounded at dev scale, exactly like the
dedup map. A restart resets the observation window; the durable backstop remains
``/context`` provenance.

Plain sync class guarded by a ``threading.Lock``: call sites sit inside async
handlers, but every critical section is a tiny dict/list update with no await, so
a thread lock is cheap and correct — no async machinery needed.
"""
from __future__ import annotations

import logging
import threading
from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("data-processing.continuity")

# Cap on STORED conflict samples per stream — the counter stays exact regardless;
# samples exist for diagnosis, not accounting.
_MAX_CONFLICT_SAMPLES = 20


@dataclass
class _StreamState:
    """Mutable per-stream observation state (guarded by the tracker's lock)."""

    user_id: str
    device_id: str
    modality: str
    first_seen: str
    last_seen: str
    max_sequence: int
    received: int = 0
    duplicate_deliveries: int = 0
    sequence_conflicts: int = 0
    # Disjoint, sorted, NON-ADJACENT [lo, hi] runs of sequences seen.
    intervals: list[list[int]] = field(default_factory=list)
    # sequence -> FIRST chunk_id seen there. Doubles as the O(1) seen-set (its
    # keys are exactly the covered sequences) and as the duplicate-vs-conflict
    # discriminator. Unbounded at dev scale — see the module docstring.
    first_chunk: dict[int, str] = field(default_factory=dict)
    conflict_samples: list[dict[str, Any]] = field(default_factory=list)


def _insert(intervals: list[list[int]], sequence: int) -> None:
    """Insert a NOT-yet-seen sequence into the sorted runs, merging with adjacent
    runs so holes between runs are always real gaps."""
    i = bisect_right(intervals, sequence, key=lambda run: run[0])
    left = intervals[i - 1] if i > 0 else None
    right = intervals[i] if i < len(intervals) else None
    joins_left = left is not None and left[1] == sequence - 1
    joins_right = right is not None and right[0] == sequence + 1
    if joins_left and joins_right:  # bridges two runs into one
        left[1] = right[1]
        del intervals[i]
    elif joins_left:
        left[1] = sequence
    elif joins_right:
        right[0] = sequence
    else:
        intervals.insert(i, [sequence, sequence])


def _gaps(intervals: list[list[int]]) -> list[list[int]]:
    """The missing runs below the max seen — every hole between runs, including
    the leading hole ``[0, lo-1]`` when the first run starts above 0."""
    gaps: list[list[int]] = []
    prev_hi = -1
    for lo, hi in intervals:
        if lo > prev_hi + 1:
            gaps.append([prev_hi + 1, lo - 1])
        prev_hi = hi
    return gaps


class ContinuityTracker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._streams: dict[str, _StreamState] = {}

    def note(
        self,
        stream_id: str,
        sequence: int,
        chunk_id: str,
        *,
        user_id: str,
        device_id: str,
        modality: str,
        now_iso: str,
    ) -> None:
        """Record one schema-valid delivery. Called on EVERY /ingest path — fresh,
        dedup-hit, and in-flight-dup — so dedup can never silently absorb a break
        signal. ``now_iso`` is caller-stamped (injectable time)."""
        with self._lock:
            state = self._streams.get(stream_id)
            if state is None:
                state = _StreamState(
                    user_id=user_id,
                    device_id=device_id,
                    modality=modality,
                    first_seen=now_iso,
                    last_seen=now_iso,
                    max_sequence=sequence,
                )
                self._streams[stream_id] = state
            state.last_seen = now_iso
            state.received += 1
            state.max_sequence = max(state.max_sequence, sequence)

            first = state.first_chunk.get(sequence)
            if first is None:  # new coverage
                state.first_chunk[sequence] = chunk_id
                _insert(state.intervals, sequence)
            elif first == chunk_id:  # at-least-once redelivery
                state.duplicate_deliveries += 1
            else:  # two DISTINCT chunks claim one slot — flag loudly
                state.sequence_conflicts += 1
                if len(state.conflict_samples) < _MAX_CONFLICT_SAMPLES:
                    state.conflict_samples.append(
                        {
                            "sequence": sequence,
                            "first_chunk_id": first,
                            "conflicting_chunk_id": chunk_id,
                            "at": now_iso,
                        }
                    )
                logger.warning(
                    "sequence conflict on stream %s: sequence %d first seen as "
                    "chunk_id=%s, now delivered as chunk_id=%s",
                    stream_id,
                    sequence,
                    first,
                    chunk_id,
                )

    def report(self) -> dict[str, Any]:
        """The ``GET /continuity`` payload: every observed stream's entry, in
        first-observation order."""
        with self._lock:
            return {
                "streams": [
                    self._entry(stream_id, state)
                    for stream_id, state in self._streams.items()
                ]
            }

    def report_stream(self, stream_id: str) -> Optional[dict[str, Any]]:
        """One stream's entry, or None if the stream was never observed."""
        with self._lock:
            state = self._streams.get(stream_id)
            return None if state is None else self._entry(stream_id, state)

    def conflict_samples(self, stream_id: str) -> list[dict[str, Any]]:
        """Copies of the recorded conflict samples for a stream (diagnosis
        surface, capped at _MAX_CONFLICT_SAMPLES; empty for unknown streams)."""
        with self._lock:
            state = self._streams.get(stream_id)
            return [dict(s) for s in state.conflict_samples] if state else []

    @staticmethod
    def _entry(stream_id: str, state: _StreamState) -> dict[str, Any]:
        # Fresh lists throughout — the caller gets a snapshot, never live state.
        return {
            "stream_id": stream_id,
            "modality": state.modality,
            "user_id": state.user_id,
            "device_id": state.device_id,
            "max_sequence": state.max_sequence,
            "received": state.received,
            "missing": _gaps(state.intervals),
            "duplicate_deliveries": state.duplicate_deliveries,
            "sequence_conflicts": state.sequence_conflicts,
            "first_seen": state.first_seen,
            "last_seen": state.last_seen,
        }
