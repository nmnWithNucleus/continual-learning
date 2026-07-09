"""faster-whisper ASR — the real path (POC Phase-1 stack).

LAZY-IMPORTED: ``faster_whisper`` is imported only inside the functions here, so
importing this module (or running the mock loop) never requires torch / av /
faster-whisper to be installed. Selected via ASR_BACKEND=faster_whisper.

CPU-capable for the skeleton (device=cpu, compute_type=int8 by default), GPU is an
optimization. Segment times come out chunk-relative (seconds from the start of the
audio we handed it, i.e. the chunk start); the pipeline maps them to absolute
wall-clock. NOT exercised by the mock unit tests.
"""
from __future__ import annotations

import io

from ..config import Settings
from .result import AsrResult, AsrSegment

# Stamped into every C2 this backend produces; distinct from the mock dialect so a
# reprocess under a different backend forks a new record_id.
PIPELINE_VERSION = "asr-fw-v0"

# Loading a Whisper model is expensive; cache per (model, device, compute_type).
_MODEL_CACHE: dict[tuple[str, str, str], object] = {}


def _get_model(settings: Settings):
    from faster_whisper import WhisperModel  # lazy import

    key = (settings.asr_model, settings.asr_device, settings.asr_compute_type)
    model = _MODEL_CACHE.get(key)
    if model is None:
        model = WhisperModel(
            settings.asr_model,
            device=settings.asr_device,
            compute_type=settings.asr_compute_type,
        )
        _MODEL_CACHE[key] = model
    return model


def transcribe(
    settings: Settings,
    audio_bytes: bytes,
    codec: str,
    chunk_seconds: float,
    chunk_id: str,
) -> AsrResult:
    model = _get_model(settings)
    # faster-whisper decodes a file-like object via ffmpeg/av; no temp file needed.
    segment_iter, info = model.transcribe(
        io.BytesIO(audio_bytes),
        beam_size=settings.asr_beam_size,
    )

    segments: list[AsrSegment] = []
    parts: list[str] = []
    for seg in segment_iter:
        seg_text = (seg.text or "").strip()
        segments.append(AsrSegment(float(seg.start), float(seg.end), seg_text))
        if seg_text:
            parts.append(seg_text)

    language = getattr(info, "language", None) or "en"
    text = " ".join(parts).strip()
    return AsrResult(text=text, language=language, segments=segments)
