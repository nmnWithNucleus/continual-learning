"""The shared per-chunk processing core — ONE code path for inline + async /ingest.

Pull the blob by ref -> verify sha256 -> run the modality's stage graph (the
GraphProcessor seam) -> assemble the ONE C2 v1 record from its slots -> validate
(contract gate + pydantic mirror) -> ONE atomic POST /context (L6) -> journal
receipt -> mark the chunk done in the dedup map. A chunk is durably processed
exactly when ``dedup.put`` runs (the C2 written), which is also when continuity
is told ``note_processed``.

Failures are raised as ``ProcessingError`` carrying BOTH:
  * ``http_status`` — the exact status the INLINE path returns (502 for
    blob/sha/context, 500 for bad-C2), mapped only at the HTTP boundary;
  * ``transient`` — whether the async worker should RETRY (a 5xx / timeout /
    connect blip: yes) or DEAD-LETTER immediately (a since-deleted blob, a
    corrupt-bytes sha mismatch, an invalid C2: no). Misclassifying a terminal
    failure as transient head-of-line-stalls the worker pool; misclassifying a
    transient one as terminal dead-letters recoverable work. A required stage's
    failure propagates as the stage raised it (the executor's leaf re-raise) —
    the worker taxonomy treats non-ProcessingError exceptions as it always has.

Pure of FastAPI: takes the storage client + dedup store explicitly (the caller
reads ``app.state.storage`` per call, preserving the test transport-injection
seam), so the same function serves the HTTP handler and the background worker.
"""
from __future__ import annotations

import hashlib
from time import perf_counter
from typing import Any, Optional

import httpx
from starlette.concurrency import run_in_threadpool

from . import schemas
from .config import Settings
from .dedup import DedupStore
from .models import C2RecordV1
from .pipeline import build_c2, chunk_span_seconds
from .storage_client import StorageClient
from .timeutil import now_iso


class ProcessingError(Exception):
    """A per-chunk processing failure with an inline HTTP status + a transient flag."""

    def __init__(self, detail: dict[str, Any], *, http_status: int, transient: bool) -> None:
        super().__init__(detail.get("error", "processing failed"))
        self.detail = detail
        self.http_status = http_status
        self.transient = transient


def _observe(metrics, name: str, value: float, labels: Optional[dict] = None) -> None:
    if metrics is not None:
        metrics.observe(name, value, labels)


def _sha256_hex(data: bytes) -> str:
    """Hex sha256 of a blob — module-level so it can be handed to a threadpool
    (hashing a whole video blob is CPU-heavy and MUST NOT run on the event loop)."""
    return hashlib.sha256(data).hexdigest()


def _is_empty_claim(slot: dict) -> bool:
    """An honest empty claim (L11): a present slot whose payload is empty —
    ``value: ""`` (asr/ocr/caption) or ``values: []`` (acoustic)."""
    if "value" in slot:
        return not str(slot["value"]).strip()
    if "values" in slot:
        return not slot["values"]
    if "splits" in slot:
        return not slot["splits"]
    return False


async def process_chunk(
    *,
    c1: dict[str, Any],
    settings: Settings,
    processor,
    pipeline_version: str,
    storage: StorageClient,
    dedup: DedupStore,
    metrics=None,
    journal=None,
    epoch: int = 0,
    app_state: Any = None,
) -> list[str]:
    """Process one accepted chunk end-to-end and return ``[record_id]`` (the D16
    wire keeps its list shape; exactly one id is derivable per chunk — D24).
    Raises ``ProcessingError`` on failure; on success the durable journal receipt
    lands FIRST (epoch-guarded, off the loop), then the chunk is marked done in
    ``dedup`` — journal-before-dedup, so a crash between them re-drives an
    already-written chunk (idempotent upsert) rather than ever forgetting one."""
    chunk_id = c1["chunk_id"]
    modality = c1["modality"]

    # ---- Pull the raw chunk bytes by blob_ref -------------------------------------
    t0 = perf_counter()
    try:
        blob_bytes = await storage.get_blob(c1["blob_ref"])
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        # Retry only genuinely-transient statuses: 5xx + 408 (timeout) + 429
        # (throttle). A permanent 4xx (404/410 since-deleted, 403 rotated cred,
        # 400 bad ref) is terminal — retrying it just storms storage.
        raise ProcessingError(
            {"error": "blob fetch failed", "status": status, "blob_ref": c1["blob_ref"]},
            http_status=502,
            transient=status >= 500 or status in (408, 429),
        )
    except httpx.HTTPError as exc:  # connect / timeout / etc. — transient
        raise ProcessingError(
            {"error": f"blob fetch error: {exc}", "blob_ref": c1["blob_ref"]},
            http_status=502,
            transient=True,
        )
    _observe(metrics, "dp_stage_seconds", perf_counter() - t0,
             {"modality": modality, "stage": "blob_fetch"})

    # ---- End-to-end integrity check against /raw ----------------------------------
    if settings.verify_blob_sha256:
        # OFF the event loop: a whole-video-blob sha256 would otherwise stall
        # every other in-flight request while it runs.
        actual = await run_in_threadpool(_sha256_hex, blob_bytes)
        if actual != c1["blob_sha256"]:
            # Corrupt bytes are terminal: a retry pulls the same bad bytes.
            raise ProcessingError(
                {"error": "blob sha256 mismatch",
                 "expected": c1["blob_sha256"], "actual": actual},
                http_status=502,
                transient=False,
            )

    # ---- Run the modality's stage graph -------------------------------------------
    span_seconds = chunk_span_seconds(c1)
    t0 = perf_counter()
    result = await processor.process_async(
        c1, blob_bytes, span_seconds,
        clients=getattr(app_state, "model_clients", None) if app_state else None,
        metrics=metrics,
    )
    _observe(metrics, "dp_stage_seconds", perf_counter() - t0,
             {"modality": modality, "stage": "process"})
    if result.pipeline_version != pipeline_version:
        # Both strings resolve from the same code; a mismatch means the caller's
        # pre-claim resolution and the graph's diverged mid-flight — a bug, never
        # something to paper over (the journal receipt would lie).
        raise ProcessingError(
            {"error": "pipeline_version drift between accept and graph run",
             "accepted": pipeline_version, "ran": result.pipeline_version},
            http_status=500,
            transient=False,
        )

    # VAD-empty rate: an asr slot with no text is a present-but-quiet (VAD-gated)
    # chunk, not a failure — count it so the dashboard shows the silence rate.
    asr_slot = result.slots.get("asr")
    if metrics is not None and asr_slot is not None and _is_empty_claim(asr_slot):
        metrics.inc("dp_vad_empty_total", {"modality": modality})

    # ---- Assemble + validate + ONE atomic POST /context (L2/L6) -------------------
    record = build_c2(c1, result.slots, pipeline_version)

    c2_problems = schemas.validate_c2(record)
    if c2_problems:  # a builder/stage-shape bug — terminal
        raise ProcessingError(
            {"error": "produced C2 failed schema validation", "violations": c2_problems},
            http_status=500,
            transient=False,
        )
    C2RecordV1.model_validate(record)

    t0 = perf_counter()
    try:
        resp = await storage.post_record(record)
    except httpx.HTTPError as exc:  # storage blip — transient
        raise ProcessingError(
            {"error": f"context write failed: {exc}"},
            http_status=502,
            transient=True,
        )
    _observe(metrics, "dp_stage_seconds", perf_counter() - t0,
             {"modality": modality, "stage": "context_write"})
    record_id = (resp or {}).get("record_id") or record["record_id"]

    # Per-slot accounting on the DURABLY-written record (a record whose POST
    # failed above never reaches here).
    if metrics is not None:
        for slot_name, slot in record["content"]["slots"].items():
            metrics.inc("dp_units_total", {"modality": modality, "kind": slot_name})
            if isinstance(slot.get("value"), str):
                metrics.observe("dp_content_chars", float(len(slot["value"])),
                                {"modality": modality, "kind": slot_name})
            if _is_empty_claim(slot):
                metrics.inc("dp_empty_output_total",
                            {"modality": modality, "kind": slot_name})

    record_ids = [record_id]
    if journal is not None:
        # Ledger v2 (L8): the executor's per-stage statuses ride the done-row
        # verbatim (failed vs cancelled distinct) — the claim tree's evidence.
        await run_in_threadpool(
            journal.mark_processed, c1, record_ids, pipeline_version, now_iso(), epoch,
            statuses=result.statuses,
        )
    dedup.put(chunk_id, record_ids)
    return record_ids
