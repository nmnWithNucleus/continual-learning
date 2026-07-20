"""In-process dedup on chunk_id — the C1 idempotency key.

C1 delivery is at-least-once, so the same chunk_id can arrive more than once
(retries, redelivery). We must be idempotent: a re-delivered chunk_id returns the
prior record_ids WITHOUT re-pulling the blob, re-running the Processor, or
re-writing to /context.

A chunk can now yield MANY records (e.g. one video chunk -> several keyframe
records), so the map caches ``chunk_id -> list[record_id]`` — the full set the
chunk produced, in order.

Two cases, both covered here:
  * already-processed: a fast in-memory map chunk_id -> [record_id, ...].
  * in-flight (concurrent redelivery of a not-yet-finished chunk):
      - INLINE mode: a per-chunk asyncio.Lock serializes them; the second waiter
        re-checks the map and returns the first's record_ids instead of
        double-processing.
      - ASYNC mode: an ``_inflight`` set marks chunk_ids that are queued or being
        processed; ``claim_for_async`` atomically decides done / in-flight / claim,
        so a concurrent redelivery is ACKed (202 duplicate) without a second enqueue.

M0 is in-memory (single process). Because each record_id is itself deterministic on
(chunk_id, pipeline_version, discriminator), storage's /context upsert is the
durable backstop — even across a restart that clears this map, a reprocess is an
upsert, not a dup.
"""
from __future__ import annotations

import asyncio
from typing import Callable, Optional


class DedupStore:
    def __init__(
        self, done_fallback: Optional[Callable[[str], Optional[list[str]]]] = None
    ) -> None:
        self._done: dict[str, list[str]] = {}    # chunk_id -> [record_id, ...]
        self._inflight: set[str] = set()         # queued/processing (async mode)
        self._locks: dict[str, asyncio.Lock] = {}
        self._guard = asyncio.Lock()
        # Durable backstop (the journal's processed table): consulted on a miss so a
        # redelivery AFTER a restart is answered with the prior record_ids instead of a
        # reprocess. Hits are cached back into the in-memory map.
        self._done_fallback = done_fallback

    def get(self, chunk_id: str) -> Optional[list[str]]:
        ids = self._done.get(chunk_id)
        if ids is None and self._done_fallback is not None:
            ids = self._done_fallback(chunk_id)
            if ids is not None:
                self._done[chunk_id] = ids
        return ids

    def put(self, chunk_id: str, record_ids: list[str]) -> None:
        """Record a chunk's final record_ids AND release any async in-flight claim —
        a processed chunk is no longer in flight."""
        self._done[chunk_id] = record_ids
        self._inflight.discard(chunk_id)

    async def lock_for(self, chunk_id: str) -> asyncio.Lock:
        """Return the per-chunk lock, creating it under a global guard. (Inline path.)"""
        async with self._guard:
            lock = self._locks.get(chunk_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[chunk_id] = lock
            return lock

    async def claim_for_async(self, chunk_id: str) -> str:
        """Atomically classify a chunk for the async accept path:

          * ``'done'``     — already processed; caller returns its record_ids (200).
          * ``'inflight'`` — already queued/processing; caller ACKs 202 (duplicate),
                             does NOT enqueue again.
          * ``'claimed'``  — freshly claimed by THIS caller; caller enqueues it and
                             MUST later release via ``put`` (success) or
                             ``release_inflight`` (dead-letter / failure / cancel).

        The claim + the two prior checks happen under one lock, so two concurrent
        redeliveries of the same chunk can never both come back ``'claimed'``. The
        durable-journal backstop is consulted BEFORE the lock (a blocking sqlite read
        must never sit inside the global guard, or one busy-timeout stall would freeze
        every ingest); the read is idempotent, and ``get`` caches a hit into ``_done``
        so the in-guard re-check sees it."""
        if self._done_fallback is not None:
            self.get(chunk_id)  # outside the guard: caches a durable hit into _done
        async with self._guard:
            if chunk_id in self._done:
                return "done"
            if chunk_id in self._inflight:
                return "inflight"
            self._inflight.add(chunk_id)
            return "claimed"

    def release_inflight(self, chunk_id: str) -> None:
        """Drop an async in-flight claim WITHOUT recording a result — for a
        dead-letter, a terminal failure, or a worker cancelled mid-drain. Idempotent.
        A subsequent redelivery re-claims and reprocesses (self-healing at-least-once);
        this must run in a ``finally`` so a claim is never orphaned (an orphan would
        make every future redelivery ACK 202-duplicate and never process)."""
        self._inflight.discard(chunk_id)

    def reset_inflight(self) -> None:
        """Clear ALL in-flight claims — belt-and-suspenders for app-object reuse
        across event loops (TestClient runs one app on a fresh loop per client), so a
        prior loop's abandoned claims can't poison a later lifespan."""
        self._inflight.clear()
