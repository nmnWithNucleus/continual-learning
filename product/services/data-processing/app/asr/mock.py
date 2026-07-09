"""Mock ASR — the DEFAULT, no-GPU path.

Returns a canned transcript that references the chunk_id (so it is unmistakably a
mock, not a real speech-to-text result) plus two fake segments that split the
chunk in half. The segment offsets sit inside [0, chunk_seconds], so their
absolute times land within the chunk span. The audio bytes are ignored.
"""
from __future__ import annotations

from ..config import Settings
from .result import AsrResult, AsrSegment

# Stamped into every C2 this backend produces; bumps fork a new record_id.
PIPELINE_VERSION = "asr-mock-v0"


def transcribe(
    settings: Settings,
    audio_bytes: bytes,
    codec: str,
    chunk_seconds: float,
    chunk_id: str,
) -> AsrResult:
    duration = chunk_seconds if chunk_seconds and chunk_seconds > 0 else 1.0
    half = duration / 2.0

    text = (
        f"[mock ASR · ASR_BACKEND=mock] Mock transcript for chunk {chunk_id}. "
        "Set ASR_BACKEND=faster_whisper for real speech-to-text."
    )
    segments = [
        AsrSegment(0.0, half, f"[mock ASR] first half of chunk {chunk_id}."),
        AsrSegment(half, duration, f"[mock ASR] second half of chunk {chunk_id}."),
    ]
    return AsrResult(text=text, language="en", segments=segments)
