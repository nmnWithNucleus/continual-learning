"""Runtime configuration, read fresh per request from the environment.

Reading env per call (rather than freezing at import) keeps the service trivially
testable and lets a POST /capture/run body OVERRIDE any default. The per-request
overrides (source, chunk_seconds, storage_url, dp_url, base_wallclock, user/device
id) fall back to these settings when omitted.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

# The two downstream services this capturer talks to (pinned dev ports).
DEFAULT_STORAGE_URL = "http://localhost:8083"   # storage /raw blob leg
DEFAULT_DP_URL = "http://localhost:8085"         # data-processing /ingest (C1 push)

# Chunking: recording carves the continuous stream into fixed-duration chunks.
DEFAULT_CHUNK_SECONDS = 5.0

# The M0 synthetic sample (there is no real mic on this box): a short tone we
# generate so the whole loop runs headless. Real OS/browser mic capture is a
# later milestone (CHARTER M1+).
DEFAULT_SAMPLE_SECONDS = 12.0     # 12s @ 5s chunks -> 3 chunks (2 full + 1 short)
DEFAULT_SAMPLE_RATE = 16000       # 16 kHz mono, faster-whisper's native rate

# v0.0 exercises one device+modality: computer mic -> audio/wav.
DEFAULT_USER_ID = "dev-user"
DEFAULT_DEVICE_ID = "dev-computer-mic"
CHUNK_CODEC = "audio/wav"


@dataclass(frozen=True)
class Settings:
    storage_url: str
    dp_url: str
    chunk_seconds: float
    sample_seconds: float
    sample_rate: int
    user_id: str
    device_id: str
    codec: str
    http_timeout: float
    retry_attempts: int      # total attempts per PUT/POST (1 try + N-1 retries)
    retry_backoff: float     # base backoff seconds between retries (0 in tests)


def get_settings() -> Settings:
    return Settings(
        storage_url=os.getenv("STORAGE_URL", DEFAULT_STORAGE_URL).rstrip("/"),
        dp_url=os.getenv("DP_URL", DEFAULT_DP_URL).rstrip("/"),
        chunk_seconds=float(os.getenv("CHUNK_SECONDS", str(DEFAULT_CHUNK_SECONDS))),
        sample_seconds=float(os.getenv("SAMPLE_SECONDS", str(DEFAULT_SAMPLE_SECONDS))),
        sample_rate=int(os.getenv("SAMPLE_RATE", str(DEFAULT_SAMPLE_RATE))),
        user_id=os.getenv("CAPTURE_USER_ID", DEFAULT_USER_ID),
        device_id=os.getenv("CAPTURE_DEVICE_ID", DEFAULT_DEVICE_ID),
        codec=os.getenv("CHUNK_CODEC", CHUNK_CODEC),
        http_timeout=float(os.getenv("RECORDING_HTTP_TIMEOUT", "30")),
        retry_attempts=int(os.getenv("RECORDING_RETRY_ATTEMPTS", "4")),
        retry_backoff=float(os.getenv("RECORDING_RETRY_BACKOFF", "0.25")),
    )
