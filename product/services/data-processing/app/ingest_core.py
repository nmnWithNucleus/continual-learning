"""The shared per-chunk processing core — ONE code path for inline + async /ingest.

Extracted verbatim from the M0 inline handler so the async worker and the synchronous
handler process a chunk IDENTICALLY: pull the blob by ref -> verify sha256 -> run the
modality Processor (off the event loop) -> assemble + validate + POST a C2 per unit ->
mark the chunk done in the dedup map. A chunk is durably processed exactly when
``dedup.put`` runs (all C2s written), which is also when continuity is told
``note_processed``.

Failures are raised as ``ProcessingError`` carrying BOTH:
  * ``http_status`` — the exact status the INLINE path returns (byte-identical to M0:
    502 for blob/sha/context, 500 for no-units / bad-C2), mapped only at the HTTP
    boundary in ``main.py``;
  * ``transient`` — whether the async worker should RETRY it (a 5xx / timeout / connect
    blip: yes) or DEAD-LETTER it immediately (a since-deleted blob, a corrupt-bytes sha
    mismatch, a processor that returns nothing, an invalid C2: no). Misclassifying a
    terminal failure as transient head-of-line-stalls the worker pool on futile
    backoff; misclassifying a transient one as terminal dead-letters work the inline
    path would have let recording retry. The split is the load-bearing decision here.

Pure of FastAPI: takes the storage client + dedup store explicitly (the caller reads
``app.state.storage`` per call, preserving the test transport-injection seam), so the
same function serves the HTTP handler and the background worker.
"""
from __future__ import annotations

import hashlib
from time import perf_counter
from types import SimpleNamespace
from typing import Any, Optional

import httpx
from starlette.concurrency import run_in_threadpool

from . import schemas
from .config import Settings
from .dedup import DedupStore
from .models import C2Record
from .pipeline import build_c2, chunk_span_seconds
from .processing.base import Processor
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


async def process_chunk(
    *,
    c1: dict[str, Any],
    settings: Settings,
    processor: Processor,
    pipeline_version: str,
    storage: StorageClient,
    dedup: DedupStore,
    metrics=None,
    journal=None,
    epoch: int = 0,
    app_state: Any = None,
) -> list[str]:
    """Process one accepted chunk end-to-end and return its record_ids. Raises
    ``ProcessingError`` on failure; on success the durable journal receipt lands FIRST
    (epoch-guarded pending delete + processed upsert, off the loop), then the chunk is
    marked done in ``dedup`` — journal-before-dedup, so a crash between them re-drives
    an already-written chunk (idempotent upsert) rather than ever forgetting one."""
    chunk_id = c1["chunk_id"]
    modality = c1["modality"]

    # ---- Pull the raw chunk bytes by blob_ref -------------------------------------
    t0 = perf_counter()
    try:
        blob_bytes = await storage.get_blob(c1["blob_ref"])
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        # Retry only genuinely-transient statuses: 5xx + 408 (timeout) + 429 (throttle).
        # A permanent 4xx (404/410 since-deleted, 403 rotated cred, 400 bad ref) is
        # terminal — retrying it just storms storage before the same dead-letter.
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
        actual = hashlib.sha256(blob_bytes).hexdigest()
        if actual != c1["blob_sha256"]:
            # Corrupt bytes are terminal: a retry pulls the same bad bytes.
            raise ProcessingError(
                {"error": "blob sha256 mismatch",
                 "expected": c1["blob_sha256"], "actual": actual},
                http_status=502,
                transient=False,
            )

    # ---- Run the modality Processor -----------------------------------------------
    # Three execution profiles, one metric: the whole run is a single
    # ``dp_stage_seconds{stage="process"}`` observation regardless of profile.
    #   * INGEST_ISOLATION=subprocess — the hardened path: the Processor step runs in a
    #     killable CHILD (poison chunk kills one chunk, not the service; a drain cancel
    #     SIGKILLs it instead of leaking a ghost thread). NOTE: the child re-resolves
    #     the processor from the registry by modality (same env → same plugin); the
    #     ``processor`` argument's pipeline_version was already computed in-parent.
    #   * a GraphProcessor's ``process_async``: awaited on the loop, stages self-offload
    #     (run_sync → threadpool, run_async → native IO) — real intra-chunk concurrency.
    #   * a legacy sync ``process``: one threadpool hop, as always.
    span_seconds = chunk_span_seconds(c1)
    t0 = perf_counter()
    if settings.ingest_isolation == "subprocess":
        from .isolation import run_processor_in_subprocess  # lazy: only when enabled
        units = await run_processor_in_subprocess(
            modality=modality, c1=c1, blob=blob_bytes,
            settings=settings, span_seconds=span_seconds,
        )
    else:
        process_async = getattr(processor, "process_async", None)
        if process_async is not None:
            resources = SimpleNamespace(
                metrics=metrics,
                vlm_pool=getattr(app_state, "vlm_pool", None) if app_state else None,
            )
            units = await process_async(c1, blob_bytes, settings, span_seconds, resources)
        else:
            units = await run_in_threadpool(
                processor.process, c1, blob_bytes, settings, span_seconds
            )
    _observe(metrics, "dp_stage_seconds", perf_counter() - t0,
             {"modality": modality, "stage": "process"})
    if not units:  # a Processor must return >= 1 unit — terminal (a plugin bug)
        raise ProcessingError(
            {"error": f"processor for {modality!r} returned no units"},
            http_status=500,
            transient=False,
        )

    # VAD-empty rate: an audio primary transcript with no text is a present-but-quiet
    # (VAD-gated) chunk, not a failure — count it so the dashboard shows the silence rate.
    if metrics is not None and modality == "audio" and not (units[0].content.text or "").strip():
        metrics.inc("dp_vad_empty_total", {"modality": modality})

    # ---- Assemble + write a C2 per unit (idempotent upsert on record_id) ----------
    processed_at = now_iso()  # one stamp for the whole processing run
    record_ids: list[str] = []
    for unit in units:
        c2 = build_c2(c1, unit, pipeline_version, processed_at)

        c2_problems = schemas.validate_c2(c2)
        if c2_problems:  # a builder bug — terminal
            raise ProcessingError(
                {"error": "produced C2 failed schema validation", "violations": c2_problems},
                http_status=500,
                transient=False,
            )
        C2Record.model_validate(c2)

        t0 = perf_counter()
        try:
            resp = await storage.post_record(c2)
        except httpx.HTTPError as exc:  # storage blip — transient
            raise ProcessingError(
                {"error": f"context write failed: {exc}"},
                http_status=502,
                transient=True,
            )
        _observe(metrics, "dp_stage_seconds", perf_counter() - t0,
                 {"modality": modality, "stage": "context_write"})
        record_ids.append((resp or {}).get("record_id") or c2["record_id"])

    if journal is not None:
        await run_in_threadpool(
            journal.mark_processed, c1, record_ids, pipeline_version, processed_at, epoch
        )
    dedup.put(chunk_id, record_ids)
    return record_ids
