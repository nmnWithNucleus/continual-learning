"""The capture session — carve the continuous stream, then per chunk, blob-first:

  1. compute bytes + sha256 for the chunk
  2. mint a stable chunk_id (reused on retry; the dedup key)
  3. PUT the bytes to storage /raw/blobs  -> blob_ref            (BLOB LEG, first)
  4. build the C1 envelope carrying that blob_ref + integrity fields
  5. validate the envelope against the frozen C1 schema (defensive)
  6. POST the C1 envelope to data-processing /ingest -> record_id (ENVELOPE LEG)

One globally-unique stream_id for the whole session; dense zero-based sequence
(0,1,2,... +1 per chunk); at-least-once with retry on both legs (retry inside the
client, so chunk_id/sequence never change across attempts).
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from . import contracts, timeutil, wav
from .clients import DataProcessingClient, StorageClient
from .config import Settings
from .ids import new_ulid
from .models import C1Envelope

logger = logging.getLogger("recording.capturer")


def _load_source(
    source: str | None, settings: Settings, sample_seconds: float | None
) -> wav.WavAudio:
    """Resolve the continuous audio source: a caller .wav path, else a synthetic sample.

    Models the always-on stream — there is no real mic on this box (CHARTER M1+).
    """
    if source:
        data = Path(source).read_bytes()
        return wav.read_wav(data)
    seconds = sample_seconds if sample_seconds is not None else settings.sample_seconds
    sample = wav.generate_sample_wav(seconds=seconds, sample_rate=settings.sample_rate)
    return wav.read_wav(sample)


def _build_envelope(
    *,
    settings: Settings,
    user_id: str,
    device_id: str,
    stream_id: str,
    sequence: int,
    chunk_id: str,
    base,
    chunk: wav.Chunk,
    blob_ref: str,
    sha256: str,
    nbytes: int,
) -> dict[str, Any]:
    envelope: dict[str, Any] = {
        "contract": "C1",
        "version": "0",
        "user_id": user_id,
        "device_id": device_id,
        "stream_id": stream_id,
        "sequence": sequence,
        "chunk_id": chunk_id,
        "modality": "audio",
        "codec": settings.codec,
        "t_start": timeutil.offset(base, chunk.t_start_seconds),
        "t_end": timeutil.offset(base, chunk.t_end_seconds),
        "blob_ref": blob_ref,
        "blob_sha256": sha256,
        "blob_bytes": nbytes,
    }
    # Authoritative gate: the frozen C1 JSON Schema. Then the pydantic mirror.
    contracts.validate_c1(envelope)
    C1Envelope.model_validate(envelope)
    return envelope


async def run_session(
    *,
    settings: Settings,
    storage_url: str,
    dp_url: str,
    source: str | None = None,
    chunk_seconds: float | None = None,
    base_wallclock: str | None = None,
    user_id: str | None = None,
    device_id: str | None = None,
    sample_seconds: float | None = None,
) -> dict[str, Any]:
    """Run one capture session end-to-end. Returns the session summary dict."""
    chunk_seconds = chunk_seconds if chunk_seconds is not None else settings.chunk_seconds
    user_id = user_id or settings.user_id
    device_id = device_id or settings.device_id

    audio = _load_source(source, settings, sample_seconds)
    chunks = wav.carve(audio, chunk_seconds)

    # ONE globally-unique stream_id for the whole always-on session.
    stream_id = new_ulid()
    base = timeutil.parse_wallclock(base_wallclock)

    storage = StorageClient(
        storage_url,
        timeout=settings.http_timeout,
        attempts=settings.retry_attempts,
        backoff=settings.retry_backoff,
    )
    dp = DataProcessingClient(
        dp_url,
        timeout=settings.http_timeout,
        attempts=settings.retry_attempts,
        backoff=settings.retry_backoff,
    )

    chunk_ids: list[str] = []
    sequences: list[int] = []
    record_ids: list[str] = []
    try:
        for chunk in chunks:
            sequence = chunk.index                 # dense, zero-based, +1 per chunk
            chunk_id = new_ulid()                   # stable per chunk; reused on retry
            data = chunk.data
            sha256 = hashlib.sha256(data).hexdigest()
            nbytes = len(data)

            # --- BLOB LEG FIRST: bytes durable in /raw before the envelope emits ---
            blob = await storage.put_blob(
                user_id=user_id,
                device_id=device_id,
                chunk_id=chunk_id,
                codec=settings.codec,
                sha256=sha256,
                nbytes=nbytes,
                data=data,
            )
            blob_ref = blob["blob_ref"]
            # Integrity cross-check: storage echoes size/sha; warn (don't fail) on drift.
            if blob.get("bytes") not in (None, nbytes) or (
                blob.get("sha256") not in (None, sha256)
            ):
                logger.warning(
                    "storage echo mismatch for chunk %s: sent bytes=%d sha=%s got %r",
                    chunk_id, nbytes, sha256, blob,
                )

            # --- ENVELOPE LEG: push the validated C1 to data-processing ---
            envelope = _build_envelope(
                settings=settings,
                user_id=user_id,
                device_id=device_id,
                stream_id=stream_id,
                sequence=sequence,
                chunk_id=chunk_id,
                base=base,
                chunk=chunk,
                blob_ref=blob_ref,
                sha256=sha256,
                nbytes=nbytes,
            )
            ack = await dp.ingest(envelope)

            chunk_ids.append(chunk_id)
            sequences.append(sequence)
            record_ids.append(ack.get("record_id"))
    finally:
        await storage.aclose()
        await dp.aclose()

    return {
        "stream_id": stream_id,
        "chunks_emitted": len(chunk_ids),
        "chunk_ids": chunk_ids,
        "sequences": sequences,
        "record_ids": record_ids,
    }
