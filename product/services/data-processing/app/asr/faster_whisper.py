"""faster-whisper ASR — the real path (POC Phase-1 stack).

LAZY-IMPORTED: ``faster_whisper`` is imported only inside the functions here, so
importing this module (or running the mock loop) never requires torch / av /
faster-whisper to be installed. Selected via ASR_BACKEND=faster_whisper.

CPU-capable for the skeleton (device=cpu, compute_type=int8 by default), GPU is an
optimization. Segment times come out chunk-relative (seconds from the start of the
audio we handed it, i.e. the chunk start); the pipeline maps them to absolute
wall-clock. NOT exercised by the mock unit tests.

VAD gate (ASR_VAD, default on): drop no-speech spans before decoding, so an
all-silence chunk yields NO segments -> ``AsrResult(text="", segments=[])`` -> a
valid C2 with an empty transcript (C2 allows ``text: ""``; the record still
documents the span). This kills the Whisper silence-hallucination failure mode:
ambient-only chunks come out honest instead of inventing speech.
"""
from __future__ import annotations

import io

from ..config import Settings
from .result import AsrResult, AsrSegment

# Stamped into every C2 this backend produces; distinct from the mock dialect so a
# reprocess under a different backend forks a new record_id. v0 -> v1: the VAD
# gate changed the output dialect (silence now transcribes empty), so the bump
# forks new records — version-forward reprocessing, per the C2 contract.
PIPELINE_VERSION = "asr-fw-v1"

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
    # vad_parameters applies only when the gate is on; 500ms keeps natural pauses
    # inside one segment instead of shredding speech into fragments.
    # language: pinned via ASR_LANGUAGE ('' = auto-detect). Auto-detect on faint
    # ambient audio guesses wrong scripts and hallucinates (seen on the first real
    # phone session), so beta deployments pin 'en'. Runtime knob, not a pipeline
    # dialect: same-language reprocessing stays an idempotent upsert.
    segment_iter, info = model.transcribe(
        io.BytesIO(audio_bytes),
        beam_size=settings.asr_beam_size,
        language=settings.asr_language or None,
        vad_filter=settings.asr_vad,
        vad_parameters={"min_silence_duration_ms": 500},
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
