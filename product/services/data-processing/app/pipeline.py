"""C1 envelope + a ProcessedUnit -> a C2 processed record. MODALITY-AGNOSTIC.

Pure functions (no I/O), reusable across the batch path and, later, the C8
synchronous path. This module knows nothing about audio/image/video/text — a
Processor (see ``processing/``) has already produced the modality's content; here
we only assemble the C2 envelope around it.

``record_id`` is a deterministic function of ``(chunk_id, pipeline_version,
discriminator)`` — a hex SHA-256 of the components joined by NUL:

  * same (chunk_id, pipeline_version, discriminator) -> byte-identical id, so a
    redelivery/reprocess is an idempotent /context upsert for EVERY record;
  * a ``pipeline_version`` bump forks new records (version-forward reprocessing);
  * the within-chunk ``discriminator`` (e.g. a video keyframe index) makes each of
    a chunk's many records stable AND distinct.

The ``discriminator`` is folded in ONLY when non-empty, so a 1:1 modality
(``discriminator=''``) keeps the exact v0 two-component id — a re-delivery of a
chunk processed before this seam landed still upserts the SAME record, not a fork.
"""
from __future__ import annotations

import hashlib
from typing import Any

from .processing.base import ProcessedUnit
from .timeutil import parse_rfc3339

# NUL separator between id components so no combination can collide by concatenation.
_ID_SEP = b"\x00"


def compute_record_id(
    chunk_id: str, pipeline_version: str, discriminator: str = ""
) -> str:
    """Deterministic, URL-safe (hex) record id for (chunk_id, pipeline_version,
    discriminator). ``discriminator=''`` (the 1:1 case) reproduces the v0
    two-component id."""
    digest = hashlib.sha256()
    digest.update(chunk_id.encode("utf-8"))
    digest.update(_ID_SEP)
    digest.update(pipeline_version.encode("utf-8"))
    if discriminator:  # fold in only for 1:many; 1:1 keeps the v0 id byte-for-byte
        digest.update(_ID_SEP)
        digest.update(discriminator.encode("utf-8"))
    return digest.hexdigest()


def chunk_span_seconds(c1: dict[str, Any]) -> float:
    """Wall-clock duration of the chunk from its C1 t_start/t_end (>= 0)."""
    start = parse_rfc3339(c1["t_start"])
    end = parse_rfc3339(c1["t_end"])
    return max(0.0, (end - start).total_seconds())


def build_c2(
    c1: dict[str, Any],
    unit: ProcessedUnit,
    pipeline_version: str,
    processed_at: str,
) -> dict[str, Any]:
    """Assemble a C2 record from the C1 envelope and one ProcessedUnit.

    - source provenance (device/stream/chunk/blob/modality) carried from C1;
    - t_start/t_end: the unit's optional per-unit sub-span when it set one, else
      carried VERBATIM from C1 (the time-spine axis storage indexes on). Defaulting
      to the C1 span keeps every 1:1 modality byte-identical; a video keyframe that
      knows its own timing supplies a sub-span so a chunk's many records no longer
      collide on the shared chunk span (CHARTER OQ14a). No C2 schema change — C2
      already carries per-record timestamps;
    - content is the unit's content, exactly as the Processor emitted it (segments,
      when present, already carry absolute RFC3339 times);
    - enrichments carried from the unit (present-but-empty in v0);
    - record_id folds in the unit's within-chunk discriminator.
    """
    record_id = compute_record_id(c1["chunk_id"], pipeline_version, unit.discriminator)

    # Per-unit sub-span when the Processor set one; otherwise the chunk's C1 span.
    t_start = unit.t_start if unit.t_start is not None else c1["t_start"]
    t_end = unit.t_end if unit.t_end is not None else c1["t_end"]

    content: dict[str, Any] = {"kind": unit.content.kind, "text": unit.content.text}
    if unit.content.language:
        content["language"] = unit.content.language
    if unit.content.segments:
        content["segments"] = unit.content.segments

    return {
        "contract": "C2",
        "version": "0",
        "record_id": record_id,
        "user_id": c1["user_id"],
        "source": {
            "device_id": c1["device_id"],
            "stream_id": c1["stream_id"],
            "chunk_id": c1["chunk_id"],
            "blob_ref": c1["blob_ref"],
            "modality": c1["modality"],
        },
        "t_start": t_start,
        "t_end": t_end,
        "content": content,
        "enrichments": unit.enrichments,
        "pipeline_version": pipeline_version,
        "processed_at": processed_at,
    }
