"""C1 envelope + one chunk's slots -> the ONE C2 v1 record. MODALITY-AGNOSTIC.

Pure functions (no I/O). The stage graph has already produced the slots map
(each value stamped with its producer's dialect segment by the executor); here
we only assemble the C2 v1 envelope around it (D24).

``record_id`` = sha256(chunk_id NUL pipeline_version) — NUL-joined, lowercase hex,
EXACTLY TWO components (L3). One record per (chunk_id, pipeline_version) (L2) is why
two components are enough: same pair -> byte-identical id, so a
redelivery/reprocess/heal re-POST is an idempotent /context upsert; a
``pipeline_version`` bump forks a new record BESIDE the old (version-forward, L8
case 2).

``t_start``/``t_end`` are the C1 span strings carried VERBATIM: never
reparsed, reformatted or normalized — a re-rendered timestamp is how a record silently
moves out of its day-log window. ``source{}`` is provenance verbatim from C1 minus
``modality`` (lifted to the record root — a C1 chunk is strictly single-modality) and
minus the transport-only fields (sequence, codec, blob_sha256, blob_bytes). The D17
civil-time trio (device_tz, device_utc_offset_minutes, device_clock) + device_location
ride verbatim when present, omitted when absent — we derive nothing, validate nothing,
infer nothing.

No wall-clock field exists inside the record (``processed_at`` dropped, ruled
2026-08-06): a full reprocess under fixed versions is byte-identical (T-1).
"""
from __future__ import annotations

import hashlib
from typing import Any, Mapping

from .timeutil import parse_rfc3339

# NUL separator between id components so no combination can collide by concatenation.
_ID_SEP = b"\x00"

# D17 provenance passthrough: carried verbatim when the device reported them.
_SOURCE_OPTIONAL = ("device_tz", "device_utc_offset_minutes", "device_clock",
                    "device_location")


def compute_record_id(chunk_id: str, pipeline_version: str) -> str:
    """Deterministic, URL-safe (hex) record id for (chunk_id, pipeline_version) —
    the L3 two-component identity. Blind upsert key."""
    digest = hashlib.sha256()
    digest.update(chunk_id.encode("utf-8"))
    digest.update(_ID_SEP)
    digest.update(pipeline_version.encode("utf-8"))
    return digest.hexdigest()


def chunk_span_seconds(c1: Mapping[str, Any]) -> float:
    """Wall-clock duration of the chunk from its C1 t_start/t_end (>= 0)."""
    start = parse_rfc3339(c1["t_start"])
    end = parse_rfc3339(c1["t_end"])
    return max(0.0, (end - start).total_seconds())


def build_c2(
    c1: Mapping[str, Any],
    slots: dict[str, Any],
    pipeline_version: str,
) -> dict[str, Any]:
    """Assemble the ONE C2 v1 record for a chunk from its slots map (L2/L5).

    ``slots`` is the executor's GraphResult.slots — every value already carries
    its producer's segment as ``version``. An empty map is legal and honest
    (all-optional-failure under L7: the dialect states what was attempted).
    """
    source: dict[str, Any] = {
        "device_id": c1["device_id"],
        "stream_id": c1["stream_id"],
        "chunk_id": c1["chunk_id"],
        "blob_ref": c1["blob_ref"],
    }
    for field in _SOURCE_OPTIONAL:
        value = c1.get(field)
        if value is not None:
            source[field] = value

    return {
        "contract": "C2",
        "version": "1",
        "record_id": compute_record_id(c1["chunk_id"], pipeline_version),
        "user_id": c1["user_id"],
        "modality": c1["modality"],
        "source": source,
        "t_start": c1["t_start"],   # VERBATIM C1 strings, never re-rendered
        "t_end": c1["t_end"],
        "pipeline_version": pipeline_version,
        "content": {"slots": slots},
    }
