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

Per-modality fairness (``INGEST_MODALITY_LIMITS``, e.g. ``"video=2"``) is enforced
AT DISPATCH: a worker atomically takes a modality permit in the same event-loop tick it
removes a job, and it only removes a job whose permit is available — scanning past
capped-modality jobs to the first eligible one. A capped burst therefore QUEUES without
occupying a single worker, and other modalities keep flowing around it (the old design
acquired the permit after a shared-FIFO ``get`` and head-of-line-blocked the pool —
review finding #3, fixed here). One shared bound still governs backpressure: capacity
counts every queued job regardless of modality, so the 503 story is unchanged. With the
knob empty (default), every job is always eligible and dispatch is exactly FIFO —
byte-identical to the unlimited pool. A chunk waiting out a retry backoff releases its
permit for the sleep (a sleeping chunk must not occupy a modality slot) and re-acquires
before the next attempt.

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

Graceful drain: on shutdown, wait (bounded by ``INGEST_DRAIN_TIMEOUT``) for queued +
in-flight work to finish, then workers are cancelled. A chunk cancelled mid-flight
(drain timeout, or kill) releases its claim in a ``finally`` so it is never orphaned —
it reads ``recording`` in the report (in-flight/unconfirmed), re-drivable from
recording's durable ledger. The done-accounting runs in a ``finally`` too, so a
mid-process cancel can't wedge the drain barrier forever.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
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
    """A bounded job buffer + N worker tasks with permit-at-dispatch fairness,
    pinned to the running loop.

    Wakeup discipline: every state change that could make a blocked coroutine
    runnable (enqueue, permit release, slot freed) wakes ALL relevant waiters, which
    re-scan and re-wait if still blocked. No permit/item is ever transferred through
    a future, so a waiter cancelled between wake and resume can never strand one
    (lost-wakeup-safe by construction; the herd is tiny at worker-pool scale).
    """

    def __init__(self, app: FastAPI, *, workers: int, maxsize: int,
                 max_retries: int, backoff: float, modality_limits: str = "") -> None:
        self._app = app
        self._n_workers = max(1, workers)
        self._max_retries = max(0, max_retries)
        self._backoff = backoff
        self._maxsize = max(1, maxsize)
        self._buf: deque[dict[str, Any]] = deque()   # ONE shared bound (503 story)
        self._tasks: list[asyncio.Task] = []
        self._processing = 0
        self._unfinished = 0                          # queued + in-worker (drain barrier)
        self._all_done = asyncio.Event()
        self._all_done.set()                          # empty queue == drained
        self._getters: deque[asyncio.Future] = deque()  # workers awaiting dispatchability
        self._putters: deque[asyncio.Future] = deque()  # submit_wait awaiting a free slot
        self.dead_letters: list[dict[str, Any]] = []
        self.loop = asyncio.get_running_loop()
        # Per-modality max-in-flight permits: available counts, taken at dispatch.
        # Empty config -> no limits -> pure FIFO (today's path, byte-identical).
        self._limits: dict[str, int] = {}
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
                self._limits[name.strip()] = limit
        self._permits: dict[str, int] = dict(self._limits)
        # Parked permit RE-ACQUIRERS (retriers coming back from a backoff sleep) hold a
        # RESERVATION: the dispatch scan may only take a permit beyond the reserved
        # count, so a finishing worker's same-tick rescan can never barge a freed permit
        # away from the oldest waiting retry (which would starve it unboundedly under a
        # sustained backlog — review-confirmed). Reservations are counters, not permit
        # transfers: a cancelled waiter just decrements in its finally, so nothing is
        # ever stranded.
        self._reacquiring: dict[str, int] = {}
        if self._limits:
            logger.info(
                "per-modality in-flight limits active: %r — permits are taken at "
                "dispatch (a capped modality's backlog queues without occupying a "
                "worker; other modalities flow around it)", self._limits,
            )

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self._tasks = [
            self.loop.create_task(self._worker(i)) for i in range(self._n_workers)
        ]
        logger.info("ingest queue started: %d workers, maxsize=%d",
                    self._n_workers, self._maxsize)

    async def drain_and_close(self, timeout: float) -> None:
        """Finish queued + in-flight work (bounded by ``timeout``), then stop workers.

        Whatever couldn't drain in time is cancelled; each cancelled chunk releases its
        dedup claim in a ``finally`` (never orphaned) and stays re-drivable. Anything
        still merely QUEUED at a hard timeout is lost-but-visible (reads 'recording' /
        re-POST-able) — the honest boundary of this slice (durable-queue recovery = M7)."""
        try:
            await asyncio.wait_for(self._all_done.wait(), timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "ingest drain timed out after %.1fs: %d queued, %d processing — "
                "cancelling; unfinished chunks are re-drivable from recording's ledger",
                timeout, len(self._buf), self._processing,
            )
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    # ------------------------------------------------------------------- counters
    def queued(self) -> int:
        return len(self._buf)

    def processing(self) -> int:
        return self._processing

    def depth(self) -> int:
        """Total outstanding work = queued + in a worker (disjoint sum)."""
        return len(self._buf) + self._processing

    # ---------------------------------------------------------------------- submit
    def submit(self, job: dict[str, Any]) -> None:
        """Enqueue a claimed chunk. Raises ``QueueFull`` if at capacity — the caller
        MUST release the dedup claim + return 503 (never a silent drop)."""
        if len(self._buf) >= self._maxsize:
            raise QueueFull()
        self._enqueue(job)

    async def submit_wait(self, job: dict[str, Any]) -> None:
        """Enqueue, WAITING for capacity — the startup re-drive path. Unlike the HTTP
        accept (which must answer now: full -> 503), the re-driver is a background task
        that can simply wait for workers to drain a slot, so a pending backlog larger
        than the queue bound is re-driven completely instead of stranded."""
        while len(self._buf) >= self._maxsize:
            await self._wait_on(self._putters)
        self._enqueue(job)

    def _enqueue(self, job: dict[str, Any]) -> None:
        self._buf.append(job)
        self._unfinished += 1
        self._all_done.clear()
        self._wake_all(self._getters)

    # ------------------------------------------------------- dispatch + permits
    def _wake_all(self, waiters: deque) -> None:
        while waiters:
            fut = waiters.popleft()
            if not fut.done():
                fut.set_result(None)

    async def _wait_on(self, waiters: deque) -> None:
        """Park until the matching state change wakes us; caller re-checks in a loop."""
        fut = self.loop.create_future()
        waiters.append(fut)
        try:
            await fut
        finally:
            with contextlib.suppress(ValueError):
                waiters.remove(fut)  # cancelled (or raced) — never leave a dead future

    def _take_permit(self, modality: str) -> bool:
        """Atomically (single loop tick) claim a modality slot FOR DISPATCH; True if
        unlimited. Permits reserved for parked re-acquirers are not up for grabs —
        dispatch may only take the surplus."""
        avail = self._permits.get(modality)
        if avail is None:
            return True
        if avail - self._reacquiring.get(modality, 0) <= 0:
            return False
        self._permits[modality] = avail - 1
        return True

    def _release_permit(self, modality: str) -> None:
        if modality in self._permits:
            self._permits[modality] += 1
            # A queued job of this modality may now be dispatchable; a backoff-waiting
            # retry may re-acquire. Both re-scan on wake.
            self._wake_all(self._getters)

    async def _acquire_permit(self, modality: str) -> None:
        """Blocking permit re-acquire (the retry path, after a backoff sleep). Holds a
        reservation while parked so the dispatch scan cannot barge every freed permit
        to newer queued jobs of the same modality (retriers are the OLDEST work)."""
        self._reacquiring[modality] = self._reacquiring.get(modality, 0) + 1
        try:
            while True:
                if self._permits[modality] > 0:
                    self._permits[modality] -= 1
                    return
                await self._wait_on(self._getters)
        finally:
            n = self._reacquiring.get(modality, 0) - 1
            if n > 0:
                self._reacquiring[modality] = n
            else:
                self._reacquiring.pop(modality, None)

    async def _get_dispatchable(self) -> dict[str, Any]:
        """Remove and return the FIRST job whose modality permit is available, taking
        the permit in the same tick (atomic w.r.t. the loop — no await between check,
        take and removal). Capped-modality jobs are scanned PAST, not blocked on: this
        is the finding-#3 fix. FIFO holds among eligible jobs, and per-modality FIFO
        holds absolutely (a skipped job stays ahead of its modality peers)."""
        while True:
            for i, job in enumerate(self._buf):
                if self._take_permit(job["c1"]["modality"]):
                    del self._buf[i]
                    self._wake_all(self._putters)  # a bounded slot was freed
                    return job
            await self._wait_on(self._getters)

    def _task_done(self) -> None:
        self._unfinished -= 1
        if self._unfinished <= 0:
            self._all_done.set()

    # ---------------------------------------------------------------------- worker
    async def _worker(self, idx: int) -> None:
        while True:
            job = await self._get_dispatchable()  # cancel here → nothing taken
            c1 = job["c1"]
            chunk_id, modality = c1["chunk_id"], c1["modality"]
            # Permit bookkeeping rides a mutable flag so a cancel during a backoff
            # sleep (permit already released) can never double-release in the finally.
            permit = {"held": modality in self._limits}
            self._processing += 1
            try:
                await self._run_job(job, permit)
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
                if permit["held"]:
                    self._release_permit(modality)
                self._task_done()  # exactly one per dispatch — the drain barrier holds

    async def _run_job(self, job: dict[str, Any], permit: dict[str, bool]) -> None:
        c1 = job["c1"]
        modality = c1["modality"]
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
                          {"modality": modality, "result": "processed"})
                return

            if transient and attempt < self._max_retries:
                attempt += 1
                self._inc("dp_ingest_retries_total", {"modality": modality})
                logger.warning("transient failure on chunk %s (retry %d/%d): %s",
                               c1["chunk_id"], attempt, self._max_retries, detail)
                # A chunk waiting out its backoff must not occupy a modality slot:
                # release for the sleep, re-acquire before the next attempt.
                if permit["held"]:
                    permit["held"] = False
                    self._release_permit(modality)
                await asyncio.sleep(self._backoff * attempt)
                if modality in self._limits:
                    await self._acquire_permit(modality)
                    permit["held"] = True
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
