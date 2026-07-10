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
  * in-flight (concurrent redelivery of a not-yet-finished chunk): a per-chunk
    asyncio.Lock serializes them; the second waiter re-checks the map and returns
    the first's record_ids instead of double-processing.

M0 is in-memory (single process). Because each record_id is itself deterministic on
(chunk_id, pipeline_version, discriminator), storage's /context upsert is the
durable backstop — even across a restart that clears this map, a reprocess is an
upsert, not a dup.
"""
from __future__ import annotations

import asyncio
from typing import Optional


class DedupStore:
    def __init__(self) -> None:
        self._done: dict[str, list[str]] = {}    # chunk_id -> [record_id, ...]
        self._locks: dict[str, asyncio.Lock] = {}
        self._guard = asyncio.Lock()

    def get(self, chunk_id: str) -> Optional[list[str]]:
        return self._done.get(chunk_id)

    def put(self, chunk_id: str, record_ids: list[str]) -> None:
        self._done[chunk_id] = record_ids

    async def lock_for(self, chunk_id: str) -> asyncio.Lock:
        """Return the per-chunk lock, creating it under a global guard."""
        async with self._guard:
            lock = self._locks.get(chunk_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[chunk_id] = lock
            return lock
