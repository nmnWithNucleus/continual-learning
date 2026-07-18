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
    )
