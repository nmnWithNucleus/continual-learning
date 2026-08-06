"""The L8 claim tree — redelivery verdicts on chunk_id, decided from DP's own ledger.

C1 delivery is at-least-once, so the same chunk_id can arrive many times (retries,
redrives, the heal drill). Every delivery is classified into one of L8's verdicts,
decided ONLY from the journal's done-row (the ``row_lookup`` callback) — never from
a storage read:

  1. no row                                  -> ``fresh``            (claim, process)
  2. row exists, stored pv != current pv     -> ``version_forward``  (fresh reprocess;
     the new record lands BESIDE the old in storage — L3 gives it a new id)
  3. pv matches, all stages ok (or the row is finalized / budget-exhausted, or a
     pre-D row with no hole evidence)        -> ``skip``             (200 + record_ids,
     touch nothing, never re-pull the blob)
  4. pv matches, holes, budget left          -> ``heal``             (claim: full-graph
     re-run off THIS delivery's envelope, re-POST the SAME record_id)
  5. already queued/processing               -> ``inflight``         (re-ACK 202)

One-lock discipline, unchanged from the v0 store: INLINE serializes per-chunk on
``lock_for``; ASYNC classifies + claims atomically under the global guard
(``claim_for_async``), so two concurrent redeliveries can never both claim. The
ledger read runs in the THREADPOOL (``classify`` is async — a sqlite read,
busy_timeout included, must never block the event loop) and always outside the
guard; the guard's own critical section never awaits, and it re-checks the
in-memory state before claiming, so the read's answer is advisory and the
claim decision stays atomic.

The in-memory ``_done`` map caches STABLE skip verdicts only (green, finalized, or
budget-exhausted rows). A holey-but-healable chunk is deliberately never cached:
each delivery re-judges it from the ledger, so the budget arithmetic — which lives
in the journal's own transactions, not here — is never answered stale. The claim's
``row`` is advisory (record_ids for replies); ``journal.mark_processed(heal=True)``
/ ``journal.heal_failed`` recompute against the CURRENT row in-transaction.

Because ``record_id`` is deterministic on (chunk_id, pipeline_version) — the L3
identity — storage's blind upsert is the durable backstop under every verdict: a
reprocess is an upsert (byte-identical -> no-op; fuller -> heal), never a dup.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from starlette.concurrency import run_in_threadpool

from .journal import HEAL_MAX_ATTEMPTS


@dataclass(frozen=True)
class Claim:
    """One delivery's verdict. ``record_ids`` rides skip/version_forward/heal
    (the existing ids — a skip's answer, a heal's 200-on-failure fallback);
    ``row`` is the ledger row the verdict was judged from (advisory)."""

    verdict: str                                   # fresh | version_forward | skip | heal | inflight
    record_ids: Optional[list[str]] = None
    row: Optional[dict[str, Any]] = field(default=None, compare=False)


class DedupStore:
    def __init__(
        self, row_lookup: Optional[Callable[[str], Optional[dict[str, Any]]]] = None
    ) -> None:
        self._done: dict[str, list[str]] = {}    # chunk_id -> ids (STABLE skips only)
        self._inflight: set[str] = set()         # queued/processing (async mode)
        self._locks: dict[str, asyncio.Lock] = {}
        self._guard = asyncio.Lock()
        # The journal's done-row (L8): consulted on every cache miss so the claim
        # tree always judges from durable state — restarts change nothing.
        self._row_lookup = row_lookup

    # ------------------------------------------------------------ decision tree
    async def classify(self, chunk_id: str, current_pv: Optional[str]) -> Claim:
        """The L8 tree, cases 1-4 (case 5, in-flight, is the async guard's).
        ``current_pv`` None (modality unresolvable right now) skips the version
        check — the stored row is served, the old pv_for_modality posture. The
        ledger read runs in the threadpool (review round: a synchronous sqlite
        read — up to its full busy_timeout — on the event loop would stall every
        coroutine, which no guard placement can prevent)."""
        ids = self._done.get(chunk_id)
        if ids is not None:
            return Claim("skip", record_ids=ids)
        row = (await run_in_threadpool(self._row_lookup, chunk_id)
               if self._row_lookup is not None else None)
        if row is None:
            return Claim("fresh")
        if current_pv is not None and row["pipeline_version"] != current_pv:
            return Claim("version_forward", record_ids=row["record_ids"], row=row)
        statuses = row.get("stage_status")
        green = statuses is None or all(v == "ok" for v in statuses.values())
        if green or row.get("done_final") or \
                row.get("heal_attempts", 0) >= HEAL_MAX_ATTEMPTS:
            # Stable under this dialect — cacheable. (A pre-D NULL-statuses row
            # carries no hole evidence: heals need evidence, so it skips.)
            self._done[chunk_id] = row["record_ids"]
            return Claim("skip", record_ids=row["record_ids"], row=row)
        return Claim("heal", record_ids=row["record_ids"], row=row)

    def get(self, chunk_id: str) -> Optional[list[str]]:
        """In-memory skip cache only (tests/diagnostics; the tree is classify)."""
        return self._done.get(chunk_id)

    def put(self, chunk_id: str, record_ids: list[str], green: bool = True) -> None:
        """Record a processed chunk AND release any async in-flight claim. Only a
        GREEN result is skip-cached — a holey ship must be re-judged from the
        ledger on its next delivery (it may heal)."""
        if green:
            self._done[chunk_id] = record_ids
        self._inflight.discard(chunk_id)

    async def lock_for(self, chunk_id: str) -> asyncio.Lock:
        """Return the per-chunk lock, creating it under a global guard. (Inline
        path: classify + process under this lock — the one-lock discipline.)"""
        async with self._guard:
            lock = self._locks.get(chunk_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[chunk_id] = lock
            return lock

    async def claim_for_async(self, chunk_id: str, current_pv: Optional[str]) -> Claim:
        """Atomically classify + claim for the async accept path. ``skip`` and
        ``inflight`` claim nothing; ``fresh`` / ``version_forward`` / ``heal``
        mark the chunk in-flight — the caller enqueues it and MUST later release
        via ``put`` (completion) or ``release_inflight`` (failure/cancel).

        The ledger read runs BEFORE the guard (idempotent; classify caches a
        stable skip into ``_done`` so the in-guard re-check sees it — the guard's
        critical section itself never awaits, so classify-then-claim races
        resolve at the re-check, exactly one winner). Two concurrent
        redeliveries can never both come back claimed; a redelivery arriving
        mid-heal re-ACKs 202 unchanged (L8 case 5)."""
        claim = await self.classify(chunk_id, current_pv)
        async with self._guard:
            ids = self._done.get(chunk_id)
            if ids is not None:
                return Claim("skip", record_ids=ids)
            if chunk_id in self._inflight:
                return Claim("inflight")
            self._inflight.add(chunk_id)
            return claim

    def release_inflight(self, chunk_id: str) -> None:
        """Drop an async in-flight claim WITHOUT recording a result — for a
        dead-letter, a terminal failure, a failed heal, or a worker cancelled
        mid-drain. Idempotent. A subsequent redelivery re-claims and re-judges
        (self-healing at-least-once); this must run in a ``finally`` so a claim
        is never orphaned (an orphan would make every future redelivery ACK
        202-duplicate and never process)."""
        self._inflight.discard(chunk_id)

    def reset_inflight(self) -> None:
        """Clear ALL in-flight claims — belt-and-suspenders for app-object reuse
        across event loops (TestClient runs one app on a fresh loop per client),
        so a prior loop's abandoned claims can't poison a later lifespan."""
        self._inflight.clear()
