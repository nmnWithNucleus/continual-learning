"""Runtime configuration, read fresh per request from the environment.

Reading env per request (rather than freezing at import) keeps the service
trivially testable: a test can flip ASR_BACKEND or point STORAGE_URL at a stub
without re-importing the app. Mirrors the serve-loop inference service.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _as_bool(value: str) -> bool:
    return value.strip().lower() not in ("0", "false", "no", "off", "")


@dataclass(frozen=True)
class Settings:
    asr_backend: str          # "mock" (default, no GPU) | "faster_whisper"
    storage_url: str          # /raw blob read + /context C2 write live here
    http_timeout: float       # inter-service httpx timeout (seconds)
    verify_blob_sha256: bool  # end-to-end integrity check of the pulled blob
    # faster-whisper knobs (only read when asr_backend == "faster_whisper")
    asr_model: str
    asr_device: str
    asr_compute_type: str
    asr_beam_size: int
    asr_language: str         # BCP-47 hint pinned for ASR ('' = auto-detect). First real
                              # phone data showed auto-detect hallucinating other scripts
                              # on faint room audio — pin 'en' for the beta fleet.
    asr_vad: bool             # VAD gate: skip no-speech spans (default ON) so an
                              # all-silence chunk yields an honest empty
                              # transcript, not a Whisper hallucination
    # --- Async /ingest (charter M7, arriving early) --------------------------------
    # INLINE by default (async OFF), so the whole loop stays byte-identical + headless
    # green with zero new behavior. Flip INGEST_ASYNC=1 to ACK 202 fast and process on
    # a worker pool decoupled from capture cadence (retires RECORDING_HTTP_TIMEOUT=120).
    ingest_async: bool        # 202 ACK + worker queue instead of inline processing
    ingest_workers: int       # worker pool size (>=1; 0 would accept-forever/lose-all)
    ingest_queue_max: int     # bounded queue capacity (>=1); full -> 503 backpressure
    ingest_max_retries: int   # transient-failure retries per chunk in the worker
    ingest_retry_backoff: float  # base backoff seconds between worker retries
    ingest_drain_timeout: float  # seconds to drain the queue on graceful shutdown
    # --- D9 observability ----------------------------------------------------------
    metrics_enabled: bool     # expose /metrics + record request/pipeline metrics


def get_settings() -> Settings:
    return Settings(
        asr_backend=os.getenv("ASR_BACKEND", "mock").strip().lower(),
        storage_url=os.getenv("STORAGE_URL", "http://localhost:8083").rstrip("/"),
        http_timeout=float(os.getenv("DP_HTTP_TIMEOUT", "30")),
        verify_blob_sha256=_as_bool(os.getenv("VERIFY_BLOB_SHA256", "1")),
        asr_model=os.getenv("ASR_MODEL", "base"),
        asr_device=os.getenv("ASR_DEVICE", "cpu"),
        asr_compute_type=os.getenv("ASR_COMPUTE_TYPE", "int8"),
        asr_beam_size=int(os.getenv("ASR_BEAM_SIZE", "1")),
        asr_language=os.getenv("ASR_LANGUAGE", "").strip().lower(),
        asr_vad=_as_bool(os.getenv("ASR_VAD", "1")),
        ingest_async=_as_bool(os.getenv("INGEST_ASYNC", "0")),
        # >=1: zero drainers under async would accept forever and lose everything.
        ingest_workers=max(1, int(os.getenv("INGEST_WORKERS", "4"))),
        # >=1: a finite queue makes overload visible as 503 -> recording retries ->
        # 'gaps' verdict, instead of an unbounded backlog that an OOM-kill drops silently.
        ingest_queue_max=max(1, int(os.getenv("INGEST_QUEUE_MAX", "256"))),
        ingest_max_retries=max(0, int(os.getenv("INGEST_MAX_RETRIES", "3"))),
        ingest_retry_backoff=float(os.getenv("INGEST_RETRY_BACKOFF", "0.5")),
        ingest_drain_timeout=float(os.getenv("INGEST_DRAIN_TIMEOUT", "30")),
        metrics_enabled=_as_bool(os.getenv("METRICS_ENABLED", "1")),
    )
