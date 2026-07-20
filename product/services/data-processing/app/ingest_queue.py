"""Async /ingest worker pool — ACK 202 fast, process off the request (charter M7).

Recording pushes a C1 and we ACK ``202`` the instant the chunk is claimed; a pool of
worker tasks drains the queue and runs the SAME ``process_chunk`` core the inline path
runs. So capture never blocks on pipeline latency (a fully-loaded chunk — real ASR +
diarization + VLM — can exceed recording's delivery timeout), and retry safety still
rides ``chunk_id`` dedup + deterministic ``record_id`` upserts.

Bounded on purpose: a finite queue turns overload into an honest ``503`` (recording
retries → visible ``gaps``) instead of an unbounded backlog an OOM-kill would drop
silently. Disjoint counters — ``queued`` (in the queue) vs ``processing`` (in a worker)
— so the drain barrier and the queue-depth gauge never double-count a claimed item.

Failure handling per chunk:
  * ``TransientError`` (blob 5xx / timeout, /context blip) → retry up to
    ``INGEST_MAX_RETRIES`` with linear backoff, then dead-letter;
  * ``TerminalError`` (since-deleted blob, sha mismatch, no units, invalid C2) →
    dead-letter immediately (no futile head-of-line backoff on the pool);
  * dead-letter = release the dedup claim + mark the sequence ``dead_lettered`` in
    continuity (recording's report reads that as ``gaps`` — visible loss, never a
    silent ``clean``) + bump a counter + append to a capped in-memory list. It is a
    COUNTER, not a recoverable DLQ: nothing re-drives it in this slice (durable
    pending-journal auto-recovery stays M7). The loss is VISIBLE, not silent.

Graceful drain: on shutdown, ``await wait_for(queue.join(), INGEST_DRAIN_TIMEOUT)`` lets
in-flight + queued work finish, then workers are cancelled. A chunk cancelled mid-flight
(drain timeout, or kill) releases its claim in a ``finally`` so it is never orphaned —
it reads ``recording`` in the report (in-flight/unconfirmed), re-drivable from
recording's durable ledger. ``task_done()`` runs in a ``finally`` too, so a mid-process
cancel can't wedge ``queue.join()`` forever.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool

from .ingest_core import ProcessingError, process_chunk
from .timeutil import now_iso

logger = logging.getLogger("data-processing.ingest_queue")

# Cap on retained dead-letter samples (the COUNTER stays exact; samples are for triage).
_MAX_DEADLETTER_SAMPLES = 100


class QueueFull(Exception):
    """Raised by ``submit`` when the bounded queue is at capacity (→ 503 backpressure)."""


class IngestQueue:
    """A bounded asyncio queue + N worker tasks, pinned to the running loop."""

    def __init__(self, app: FastAPI, *, workers: int, maxsize: int,
                 max_retries: int, backoff: float, modality_limits: str = "") -> None:
        self._app = app
        self._n_workers = max(1, workers)
        self._max_retries = max(0, max_retries)
        self._backoff = backoff
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max(1, maxsize))
        self._tasks: list[asyncio.Task] = []
        self._processing = 0
        self.dead_letters: list[dict[str, Any]] = []
        self.loop = asyncio.get_running_loop()
        # Per-modality fairness: a max-in-flight semaphore per modality so a burst of one
        # (e.g. video) can't occupy every worker and starve another's (audio) latency.
        # Created on THIS loop (loop-affinity). Empty config -> flat pool (today's path,
        # byte-identical). ⚠️ EXPERIMENTAL / off by default: the permit is acquired AFTER
        # the shared-FIFO dequeue, so under load a blocked worker HOL-blocks the pool
        # (review finding #3). Do NOT enable in production until per-modality queue
        # partitioning lands — see handoff/ws-dp-stage-graph.md §Review follow-ups.
        self._modality_sems: dict[str, asyncio.Semaphore] = {}
        for part in modality_limits.split(","):
            part = part.strip()
            if not part or "=" not in part:
                continue
            name, _, raw = part.partition("=")
            try:
                limit = int(raw)
            except ValueError:
                continue
            if name.strip() and limit > 0:
                self._modality_sems[name.strip()] = asyncio.Semaphore(limit)
        if self._modality_sems:
            logger.warning(
                "INGEST_MODALITY_LIMITS=%r is EXPERIMENTAL: the permit is acquired after "
                "the shared-FIFO dequeue, so it can head-of-line-block the pool. Not for "
                "production until per-modality queue partitioning lands (ws-dp-stage-graph).",
                modality_limits,
            )

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self._tasks = [
            self.loop.create_task(self._worker(i)) for i in range(self._n_workers)
        ]
        logger.info("ingest queue started: %d workers, maxsize=%d",
                    self._n_workers, self._queue.maxsize)

    async def drain_and_close(self, timeout: float) -> None:
        """Finish queued + in-flight work (bounded by ``timeout``), then stop workers.

        Whatever couldn't drain in time is cancelled; each cancelled chunk releases its
        dedup claim in a ``finally`` (never orphaned) and stays re-drivable. Anything
        still merely QUEUED at a hard timeout is lost-but-visible (reads 'recording' /
        re-POST-able) — the honest boundary of this slice (durable-queue recovery = M7)."""
        try:
            await asyncio.wait_for(self._queue.join(), timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "ingest drain timed out after %.1fs: %d queued, %d processing — "
                "cancelling; unfinished chunks are re-drivable from recording's ledger",
                timeout, self._queue.qsize(), self._processing,
            )
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    # ------------------------------------------------------------------- counters
    def queued(self) -> int:
        return self._queue.qsize()

    def processing(self) -> int:
        return self._processing

    def depth(self) -> int:
        """Total outstanding work = queued + in a worker (disjoint sum)."""
        return self._queue.qsize() + self._processing

    # ---------------------------------------------------------------------- submit
    def submit(self, job: dict[str, Any]) -> None:
        """Enqueue a claimed chunk. Raises ``QueueFull`` if at capacity — the caller
        MUST release the dedup claim + return 503 (never a silent drop)."""
        try:
            self._queue.put_nowait(job)
        except asyncio.QueueFull as exc:
            raise QueueFull() from exc

    async def submit_wait(self, job: dict[str, Any]) -> None:
        """Enqueue, WAITING for capacity — the startup re-drive path. Unlike the HTTP
        accept (which must answer now: full -> 503), the re-driver is a background task
        that can simply wait for workers to drain a slot, so a pending backlog larger
        than the queue bound is re-driven completely instead of stranded."""
        await self._queue.put(job)

    # ---------------------------------------------------------------------- worker
    async def _worker(self, idx: int) -> None:
        while True:
            job = await self._queue.get()  # cancel here → no item taken, no task_done
            chunk_id = job["c1"]["chunk_id"]
            self._processing += 1
            try:
                await self._run_job(job)
            except asyncio.CancelledError:
                # Drain/shutdown mid-flight: release the claim so it's never orphaned.
                self._deps().dedup.release_inflight(chunk_id)
                raise
            except Exception:  # noqa: BLE001 — a worker must NEVER die on a stray error
                # _run_job already dead-letters ProcessingError + generic failures; this is
                # the last backstop (e.g. a bug in the success/metrics path) so the pool
                # keeps its worker. Release the claim so the chunk isn't orphaned.
                self._deps().dedup.release_inflight(chunk_id)
                logger.exception("ingest worker %d: unexpected error on chunk %s",
                                 idx, chunk_id)
            finally:
                self._processing -= 1
                self._queue.task_done()  # exactly one per get() — join() stays correct

    def _sem_for(self, modality: str):
        """Per-modality fairness gate for ONE processing attempt (nullcontext when no
        limit is configured). Held only around process_chunk — NOT across the retry
        backoff sleep, so a chunk waiting to retry never occupies a modality slot."""
        sem = self._modality_sems.get(modality)
        return sem if sem is not None else contextlib.nullcontext()

    async def _run_job(self, job: dict[str, Any]) -> None:
        c1 = job["c1"]
        deps = self._deps()
        # Crash-loop attribution: charge a re-drive processing attempt to THIS chunk once,
        # before touching it — so a chunk whose processing hard-crashes the service accrues
        # toward the DP_REDRIVE_MAX_ATTEMPTS cap while a chunk merely queued behind it does
        # not (per-chunk, not per-restart). Live (non-re-drive) chunks never pay this write.
        if job.get("redrive"):
            await run_in_threadpool(deps.journal.note_redrive_attempt, c1["chunk_id"], now_iso())
        attempt = 0
        while True:
            try:
                async with self._sem_for(c1["modality"]):
                    await process_chunk(
                        c1=c1,
                        settings=job["settings"],
                        processor=job["processor"],
                        pipeline_version=job["pipeline_version"],
                        storage=self._app.state.storage,  # read per call (test seam)
                        dedup=deps.dedup,
                        metrics=deps.metrics,
                        journal=deps.journal,          # durable receipt inside the core,
                        epoch=job.get("epoch", 0),     # epoch-guarded against stale workers
                        app_state=self._app.state,
                    )
            except asyncio.CancelledError:
                raise
            except ProcessingError as exc:
                transient, detail = exc.transient, exc.detail
            except Exception as exc:  # noqa: BLE001
                # An UNEXPECTED error out of the processor is an infra hiccup (model
                # cold-load 503, CUDA OOM, an ffmpeg/subprocess RuntimeError) far more
                # often than a poison chunk. Inline mode 500s → recording retries, so the
                # async worker must retry too rather than dead-letter on the first blip
                # (bounded by INGEST_MAX_RETRIES, then dead-lettered). Genuinely-terminal
                # conditions (no units, invalid C2) are raised as ProcessingError(terminal)
                # inside process_chunk and handled above.
                transient, detail = True, {"error": f"{type(exc).__name__}: {exc}"}
            else:
                # The durable receipt already landed inside process_chunk (journal-
                # before-dedup); here only the in-memory note + metrics remain.
                deps.continuity.note_processed(c1["stream_id"], c1["sequence"])
                self._inc("dp_ingest_total",
                          {"modality": c1["modality"], "result": "processed"})
                return

            if transient and attempt < self._max_retries:
                attempt += 1
                self._inc("dp_ingest_retries_total", {"modality": c1["modality"]})
                logger.warning("transient failure on chunk %s (retry %d/%d): %s",
                               c1["chunk_id"], attempt, self._max_retries, detail)
                await asyncio.sleep(self._backoff * attempt)
                continue
            await self._dead_letter(c1, detail, transient=transient, epoch=job.get("epoch", 0))
            return

    # ------------------------------------------------------------------- internals
    def _deps(self):
        return self._app.state

    def _inc(self, name: str, labels: dict) -> None:
        metrics = getattr(self._app.state, "metrics", None)
        if metrics is not None:
            metrics.inc(name, labels)

    async def _dead_letter(self, c1: dict, detail: dict, *, transient: bool, epoch: int = 0) -> None:
        deps = self._deps()
        # ORDER MATTERS: the durable dead-letter mark + continuity note land BEFORE the
        # claim is released. Release-first would open a window where a redelivery gets
        # 202-accepted (fresh journal 'accepted' row) and THIS losing worker then
        # clobbers that row to 'dead_letter' — a chunk recording believes is in flight
        # would read as terminally lost. Release-last, the redelivery can only re-claim
        # after the mark is in place, and its accept() resets the row cleanly.
        await run_in_threadpool(
            deps.journal.mark_dead_letter,
            c1["chunk_id"], str(detail.get("error", detail)), now_iso(), epoch,
        )
        deps.continuity.note_dead_letter(c1["stream_id"], c1["sequence"])
        deps.dedup.release_inflight(c1["chunk_id"])
        self._inc("dp_dead_letter_total", {"modality": c1["modality"]})
        if len(self.dead_letters) < _MAX_DEADLETTER_SAMPLES:
            self.dead_letters.append({
                "chunk_id": c1["chunk_id"],
                "stream_id": c1["stream_id"],
                "sequence": c1["sequence"],
                "modality": c1["modality"],
                "transient_exhausted": transient,
                "detail": detail,
            })
        logger.error(
            "DEAD-LETTER chunk %s (stream %s seq %s): %s — no C2 written; recording's "
            "report will read this as loss (gaps). %s",
            c1["chunk_id"], c1["stream_id"], c1["sequence"], detail,
            "retries exhausted" if transient else "terminal",
        )
