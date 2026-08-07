"""Runtime configuration, read fresh per request from the environment.

OPERATIONAL-ONLY, by law (L4/D25): no output-affecting env knob exists anywhere
in this service. Every setting below can change endpoints, capacity, timeouts,
retries, durability locations or observability — never one byte of a record.
Names a reader might expect to find here and will not — each is baked into a
backend's version or absent by law:

  * ASR_BACKEND / ASR_MODEL / ASR_DEVICE / ASR_COMPUTE_TYPE — the model and its
    execution profile live in the whisper server's code + manifest identity;
    backend selection is in code, named in the dialect (mock included).
  * ASR_BEAM_SIZE / ASR_LANGUAGE / ASR_VAD — per-call params pinned in the asr
    stage file; changing them is a vB bump.
  * INGEST_ISOLATION / INGEST_SUBPROC_START — no per-chunk subprocess shield
    exists (D26): models run in the server fleet, not in the DP process, so
    there is nothing left for it to contain.

Reading env per request (rather than freezing at import) keeps the service
trivially testable: a test can point STORAGE_URL at a stub without re-importing
the app.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("data-processing.config")


def _as_bool(value: str) -> bool:
    return value.strip().lower() not in ("0", "false", "no", "off", "")


def _default_var_dir() -> str:
    """<service>/var — the durable ingest journal (dp.db) lives here."""
    return str(Path(__file__).resolve().parents[1] / "var")


@dataclass(frozen=True)
class Settings:
    storage_url: str          # /raw blob read + /context C2 write live here
    http_timeout: float       # inter-service httpx timeout (seconds)
    verify_blob_sha256: bool  # end-to-end integrity check of the pulled blob
    # --- Async /ingest -------------------------------------------------------------
    ingest_async: bool        # 202 ACK + worker queue instead of inline processing
    ingest_workers: int       # worker pool size (>=1; 0 would accept-forever/lose-all)
    ingest_queue_max: int     # bounded queue capacity (>=1); full -> 503 backpressure
    ingest_max_retries: int   # transient-failure retries per chunk in the worker
    ingest_retry_backoff: float  # base backoff seconds between worker retries
    ingest_drain_timeout: float  # seconds to drain the queue on graceful shutdown
    # --- Durable ingest journal ----------------------------------------------------
    dp_var_dir: str           # dp.db (pending/processed journal) lives here
    redrive_max_attempts: int  # startup re-drives before a chunk dead-letters
    # --- Fairness ------------------------------------------------------------------
    ingest_modality_limits: str  # per-modality max-in-flight, e.g. "video=2,audio=4"
    # --- D9 observability ----------------------------------------------------------
    metrics_enabled: bool     # expose /metrics + record request/pipeline metrics


def get_settings() -> Settings:
    return Settings(
        storage_url=os.getenv("STORAGE_URL", "http://localhost:8083").rstrip("/"),
        http_timeout=float(os.getenv("DP_HTTP_TIMEOUT", "30")),
        verify_blob_sha256=_as_bool(os.getenv("VERIFY_BLOB_SHA256", "1")),
        ingest_async=_as_bool(os.getenv("INGEST_ASYNC", "0")),
        # >=1: zero drainers under async would accept forever and lose everything.
        ingest_workers=max(1, int(os.getenv("INGEST_WORKERS", "4"))),
        # >=1: a finite queue makes overload visible as 503 -> recording retries ->
        # 'gaps' verdict, instead of an unbounded backlog an OOM-kill drops silently.
        ingest_queue_max=max(1, int(os.getenv("INGEST_QUEUE_MAX", "256"))),
        ingest_max_retries=max(0, int(os.getenv("INGEST_MAX_RETRIES", "3"))),
        ingest_retry_backoff=float(os.getenv("INGEST_RETRY_BACKOFF", "0.5")),
        ingest_drain_timeout=float(os.getenv("INGEST_DRAIN_TIMEOUT", "30")),
        dp_var_dir=os.getenv("DP_VAR_DIR", _default_var_dir()),
        redrive_max_attempts=max(1, int(os.getenv("DP_REDRIVE_MAX_ATTEMPTS", "5"))),
        ingest_modality_limits=os.getenv("INGEST_MODALITY_LIMITS", ""),
        metrics_enabled=_as_bool(os.getenv("METRICS_ENABLED", "1")),
    )
