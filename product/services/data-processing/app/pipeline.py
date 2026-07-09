"""C1 + ASR result -> C2 processed record.

Pure functions (no I/O) so they are trivially testable and reusable across the
batch path and, later, the C8 synchronous path.

``record_id`` is a deterministic function of ``(chunk_id, pipeline_version)`` — a
hex SHA-256 of the two joined — so redelivery/reprocess under the same version is
an idempotent /context upsert, and a pipeline_version bump forks a new record
(version-forward reprocessing). It is hex, hence URL-safe.
"""
from __future__ import annotations

import hashlib
from typing import Any

from .asr.result import AsrResult
from .timeutil import abs_time, parse_rfc3339

# NUL separator between the two id components so no chunk_id/pipeline_version pair
# can collide with another by concatenation.
_ID_SEP = b"\x00"


def compute_record_id(chunk_id: str, pipeline_version: str) -> str:
    """Deterministic, URL-safe (hex) record id for (chunk_id, pipeline_version)."""
    digest = hashlib.sha256()
    digest.update(chunk_id.encode("utf-8"))
    digest.update(_ID_SEP)
    digest.update(pipeline_version.encode("utf-8"))
    return digest.hexdigest()


def chunk_span_seconds(c1: dict[str, Any]) -> float:
    """Wall-clock duration of the chunk from its C1 t_start/t_end (>= 0)."""
    start = parse_rfc3339(c1["t_start"])
    end = parse_rfc3339(c1["t_end"])
    return max(0.0, (end - start).total_seconds())


def build_c2(
    c1: dict[str, Any],
    asr: AsrResult,
    pipeline_version: str,
    processed_at: str,
) -> dict[str, Any]:
    """Assemble a C2 record from the C1 envelope and an ASR result.

    - source provenance (device/stream/chunk/blob/modality) carried from C1
    - t_start/t_end carried VERBATIM from C1 (the time-spine axis storage indexes)
    - each ASR segment offset -> absolute RFC3339, clamped into the chunk span so a
      segment can never spill past the chunk edge
    - enrichments present-but-empty; content.kind = 'transcript'
    """
    base = parse_rfc3339(c1["t_start"])
    span = chunk_span_seconds(c1)
    record_id = compute_record_id(c1["chunk_id"], pipeline_version)

    content: dict[str, Any] = {"kind": "transcript", "text": asr.text}
    if asr.language:
        content["language"] = asr.language

    segments: list[dict[str, Any]] = []
    for seg in asr.segments:
        start = min(max(seg.start_s, 0.0), span)
        end = min(max(seg.end_s, start), span)
        segments.append(
            {
                "t_start": abs_time(base, start),
                "t_end": abs_time(base, end),
                "text": seg.text,
                "speaker": None,  # required-nullable; no diarization in v0
            }
        )
    if segments:
        content["segments"] = segments

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
        "t_start": c1["t_start"],
        "t_end": c1["t_end"],
        "content": content,
        "enrichments": {"speakers": [], "faces": [], "places": [], "objects": []},
        "pipeline_version": pipeline_version,
        "processed_at": processed_at,
    }
