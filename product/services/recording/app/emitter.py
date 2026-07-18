"""Per-session, in-order emit worker: spooled segment -> demux -> C1 chunks downstream.

For each received segment (in received order per session): demux into per-modality
chunk files, then per chunk — get-or-create the session's stream for that modality,
mint+persist ``chunk_id``/``sequence`` in the ledger (BEFORE the first attempt, so a
retry or restart re-emits the SAME identity), PUT the bytes to storage /raw, push the
validated C1 envelope to data-processing, record the acks, and finally mark the
segment ``emitted`` + delete its spool file (kept under RECORDING_KEEP_SPOOL=1 — the
future consent-gate holdback point, D13).

Ordering: one asyncio worker task per session drains a per-session FIFO queue, so a
session's segments are processed strictly in received order while sessions proceed
concurrently. A terminal per-segment failure marks it ``failed`` (visible in the gap
report; re-enqueued only by /capture/sessions/{id}/retry) and does NOT stall the
session's later segments. Chunks allocated before the failure keep their sequence, so
a retry slots back into the stream exactly where it was minted.

At-least-once delivery lives in clients.StorageClient/DataProcessingClient (their
retry loop); both downstreams dedup on chunk_id, so re-emits have exactly-once effect.

Restart safety: segments acked (spool + ledger row) but never emitted survive as
state='received'; ``reenqueue_pending`` re-queues them at startup (see main.lifespan).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI

from . import capturer, demux, ledger
from .clients import DataProcessingClient, StorageClient
from .config import Settings, get_settings
from .timeutil import rfc3339

logger = logging.getLogger("recording.emitter")


class Emitter:
    """Per-session FIFO segment processing on one event loop."""

    def __init__(self) -> None:
        self.loop = asyncio.get_running_loop()
        self._workers: dict[str, tuple[asyncio.Queue, asyncio.Task]] = {}

    def enqueue(self, session_id: str, seq: int) -> asyncio.Future:
        """Queue one segment; the future resolves when its processing finishes.

        Sync-mode callers await the future; async-mode callers drop it (a done-
        callback pre-retrieves any exception so nothing warns at GC — the failure
        is already recorded in the ledger, which is the source of truth).
        """
        fut: asyncio.Future = self.loop.create_future()
        fut.add_done_callback(lambda f: f.cancelled() or f.exception())
        entry = self._workers.get(session_id)
        if entry is None:
            queue: asyncio.Queue = asyncio.Queue()
            task = self.loop.create_task(self._worker(session_id, queue))
            self._workers[session_id] = (queue, task)
        else:
            queue = entry[0]
        queue.put_nowait((seq, fut))
        return fut

    async def _worker(self, session_id: str, queue: asyncio.Queue) -> None:
        while True:
            try:
                seq, fut = queue.get_nowait()
            except asyncio.QueueEmpty:
                # Drained: retire. No await sits between this check and the pop from
                # _workers, so enqueue() can never race a dying worker on this loop.
                self._workers.pop(session_id, None)
                return
            try:
                await process_segment(session_id, seq)
            except Exception as exc:
                logger.warning("segment (%s, %d) processing failed: %s", session_id, seq, exc)
                if not fut.done():
                    fut.set_exception(exc)
            else:
                if not fut.done():
                    fut.set_result(None)

    async def aclose(self) -> None:
        """Cancel workers. In-flight segments stay 'received' in the ledger and are
        re-enqueued (same chunk_ids) at the next startup — shutdown loses nothing."""
        tasks = [task for _queue, task in self._workers.values()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._workers.clear()


def get_emitter(app: FastAPI) -> Emitter:
    """The app's Emitter, created lazily and pinned to the RUNNING loop.

    Loop-affinity matters: asyncio queues/tasks are unusable across loops, and test
    harnesses run the same app object on a fresh loop per client — a stale emitter
    from a previous loop is replaced, never reused.
    """
    loop = asyncio.get_running_loop()
    emitter = getattr(app.state, "emitter", None)
    if emitter is None or emitter.loop is not loop:
        emitter = Emitter()
        app.state.emitter = emitter
    return emitter


def reenqueue_pending(app: FastAPI) -> int:
    """Re-enqueue every acked-but-unemitted segment (startup, via main.lifespan).

    Only state='received' comes back — an ack-then-crash must not silently lose
    segments. 'failed' stays failed until an explicit /capture/sessions/{id}/retry.
    """
    led = ledger.for_settings(get_settings())
    emitter = get_emitter(app)
    pending = led.pending_segments()
    for session_id, seq in pending:
        emitter.enqueue(session_id, seq)
    if pending:
        logger.info("re-enqueued %d acked-but-unemitted segment(s)", len(pending))
    return len(pending)


async def shutdown(app: FastAPI) -> None:
    emitter = getattr(app.state, "emitter", None)
    if emitter is not None and emitter.loop is asyncio.get_running_loop():
        await emitter.aclose()


async def process_segment(session_id: str, seq: int) -> None:
    """Demux + emit one spooled segment. Raises after marking the segment 'failed'."""
    settings = get_settings()
    led = ledger.for_settings(settings)
    segment = led.segment(session_id, seq)
    if segment is None:
        raise RuntimeError(f"segment ({session_id}, {seq}) not in the ledger")
    if segment["state"] == "emitted":
        return  # duplicate enqueue (e.g. restart race) — nothing to do
    session = led.get_session(session_id)
    assert session is not None  # segments can't exist without their session row

    scratch = Path(settings.var_dir) / "chunks" / session_id / str(seq)
    try:
        spool = Path(segment["spool_path"])
        if not spool.is_file():
            raise RuntimeError(f"spool file missing: {spool}")
        tracks = await asyncio.to_thread(
            demux.demux_segment,
            spool,
            mime=segment["mime"],
            out_dir=scratch,
            ffmpeg_bin=settings.ffmpeg_bin,
            ffprobe_bin=settings.ffprobe_bin,
        )
        await _emit_tracks(settings, led, session, segment, tracks)
        led.set_segment_state(session_id, seq, "emitted")
        if not settings.keep_spool:
            spool.unlink(missing_ok=True)
    except Exception as exc:
        # Terminal for this pass: visible in the report; /retry re-enqueues. The
        # spool file is deliberately kept so the retry has bytes to work from.
        led.set_segment_state(session_id, seq, "failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


async def _emit_tracks(
    settings: Settings,
    led: ledger.Ledger,
    session: dict,
    segment: dict,
    tracks: list[demux.DemuxedTrack],
) -> None:
    storage = StorageClient(
        settings.storage_url,
        timeout=settings.http_timeout,
        attempts=settings.retry_attempts,
        backoff=settings.retry_backoff,
    )
    dp = DataProcessingClient(
        settings.dp_url,
        timeout=settings.http_timeout,
        attempts=settings.retry_attempts,
        backoff=settings.retry_backoff,
    )
    try:
        # ALLOCATE EVERY track's chunk identity BEFORE emitting any: a terminal
        # failure mid-emit (e.g. audio's DP push exhausts retries before video was
        # reached) must not leave a track unallocated — later segments would consume
        # the next sequences and a /retry would mint this track's chunk at the END
        # of the stream, breaking sequence-order == capture-order. (A segment whose
        # DEMUX failed outright still has no allocations; its /retry after later
        # segments emits late sequences — visible as `failed` in the report, and
        # t_start stays the honest time axis.)
        prepared: list[tuple[demux.DemuxedTrack, bytes, str, dict, int, str]] = []
        for track in tracks:
            data = await asyncio.to_thread(track.path.read_bytes)
            sha256 = await asyncio.to_thread(
                lambda d=data: hashlib.sha256(d).hexdigest()
            )
            stream = led.get_or_create_stream(
                segment["session_id"], track.modality, track.codec
            )
            sequence, chunk_id = led.allocate_chunk(
                stream_id=stream["stream_id"],
                session_id=segment["session_id"],
                seq=segment["seq"],
                modality=track.modality,
                codec=track.codec,
                nbytes=len(data),
                sha256=sha256,
            )
            prepared.append((track, data, sha256, stream, sequence, chunk_id))

        for track, data, sha256, stream, sequence, chunk_id in prepared:
            # Blob leg first, then the C1 envelope — same order and same envelope
            # builder (schema + pydantic gates) as the M0 capture path.
            blob = await storage.put_blob(
                user_id=session["user_id"],
                device_id=session["device_id"],
                chunk_id=chunk_id,
                codec=track.codec,
                sha256=sha256,
                nbytes=len(data),
                data=data,
            )
            envelope = capturer._build_envelope(
                user_id=session["user_id"],
                device_id=session["device_id"],
                stream_id=stream["stream_id"],
                sequence=sequence,
                chunk_id=chunk_id,
                modality=track.modality,
                codec=track.codec,
                # Both chunks carry the SEGMENT's wall-clock span (D-M1 demux note).
                t_start=segment["t_start"],
                t_end=segment["t_end"],
                blob_ref=blob["blob_ref"],
                sha256=sha256,
                nbytes=len(data),
            )
            ack = await dp.ingest(envelope)
            led.finalize_chunk(
                stream["stream_id"],
                sequence,
                blob_ref=blob["blob_ref"],
                record_ids=ack.get("record_ids") or [],
                emitted_at=rfc3339(datetime.now(timezone.utc)),
            )
    finally:
        await storage.aclose()
        await dp.aclose()
