"""The capture session — pull a modality's ChunkSource, then per chunk, blob-first:

  1. compute bytes + sha256 for the chunk
  2. mint a stable chunk_id (reused on retry; the dedup key)
  3. PUT the bytes to storage /raw/blobs  -> blob_ref            (BLOB LEG, first)
  4. build the C1 envelope carrying that blob_ref + integrity fields
  5. validate the envelope against the frozen C1 schema (defensive)
  6. POST the C1 envelope to data-processing /ingest -> record_ids (ENVELOPE LEG)

This emit path is MODALITY-AGNOSTIC: it consumes a ``ChunkSource`` (see app/sources/)
which yields ordered ``SourceChunk``s of opaque bytes + wall-clock span for one
modality+codec. Audio (WAV) is the one M0 source; future modalities (image/video/text,
screen/webcam/wearable) drop in a new source file — this path never changes.

One globally-unique stream_id for the whole session; dense zero-based sequence
(0,1,2,... assigned here as chunks emit); at-least-once with retry on both legs (retry
inside the client, so chunk_id/sequence never change across attempts).
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

from . import contracts, sources
from .clients import DataProcessingClient, StorageClient
from .config import Settings
from .ids import new_ulid
from .models import C1Envelope

logger = logging.getLogger("recording.capturer")


def _build_envelope(
    *,
    user_id: str,
    device_id: str,
    stream_id: str,
    sequence: int,
    chunk_id: str,
    modality: str,
    codec: str,
    t_start: str,
    t_end: str,
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
        "modality": modality,
        "codec": codec,
        "t_start": t_start,
        "t_end": t_end,
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
    modality: str = "audio",
) -> dict[str, Any]:
    """Run one capture session end-to-end. Returns the session summary dict.

    ``modality`` selects the ChunkSource from the registry (default 'audio'). The rest of
    this function is modality-agnostic — it only reads ``source.modality`` / ``.codec``
    and each ``SourceChunk``'s bytes + wall-clock span.
    """
    user_id = user_id or settings.user_id
    device_id = device_id or settings.device_id

    # The modality-agnostic source: audio (WAV) today, image/video/text/etc. tomorrow.
    source_obj = sources.build_source(
        modality,
        settings=settings,
        source=source,
        chunk_seconds=chunk_seconds,
        sample_seconds=sample_seconds,
        base_wallclock=base_wallclock,
    )

    # ONE globally-unique stream_id for the whole always-on session.
    stream_id = new_ulid()

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
        # sequence assigned HERE (dense, zero-based, +1 per emitted chunk) — a stream
        # concern, not the source's; the source only promises capture order.
        for sequence, chunk in enumerate(source_obj.chunks()):
            chunk_id = new_ulid()                   # stable per chunk; reused on retry
            data = chunk.data
            sha256 = hashlib.sha256(data).hexdigest()
            nbytes = len(data)

            # --- BLOB LEG FIRST: bytes durable in /raw before the envelope emits ---
            blob = await storage.put_blob(
                user_id=user_id,
                device_id=device_id,
                chunk_id=chunk_id,
                codec=source_obj.codec,
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
                user_id=user_id,
                device_id=device_id,
                stream_id=stream_id,
                sequence=sequence,
                chunk_id=chunk_id,
                modality=source_obj.modality,
                codec=source_obj.codec,
                t_start=chunk.t_start,
                t_end=chunk.t_end,
                blob_ref=blob_ref,
                sha256=sha256,
                nbytes=nbytes,
            )
            ack = await dp.ingest(envelope)

            chunk_ids.append(chunk_id)
            sequences.append(sequence)
            # /ingest returns {ok, record_ids:[...]}: one C1 (chunk) may fan out to
            # >1 C2 record (e.g. video keyframes), so flatten across chunks.
            record_ids.extend(ack.get("record_ids") or [])
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
