"""
WS4 — ASR module for live_video_chat V0.

Drop-in speech-to-text: a browser audio blob -> transcribed text.

WS2 (backend) uses it as:

    from asr import transcribe
    text = transcribe(audio_bytes, mime)   # mime from the upload, optional

Pipeline:
    raw blob (iOS audio/mp4 AAC  or  desktop audio/webm Opus)
      --ffmpeg-->  16 kHz mono float32 PCM
      --faster-whisper-->  text

Design notes (see handoff/ws4-asr.md for the full rationale):
  * faster-whisper is NOT asked to demux the browser container. We always decode
    first with ffmpeg (7.1, in the `moe` env) because iOS `audio/mp4` and some
    `audio/webm` blobs are not reliably read by the bundled av/ffmpeg path inside
    faster-whisper. ffmpeg on stdin handles every container/codec uniformly.
  * The model is a module-level singleton, loaded lazily on first use (or via
    warmup()) and reused for every call — cold-loading per request would dominate
    latency.
  * Empty / silent / garbled audio -> "" (ffmpeg yields no samples, or VAD finds
    no speech).

Measured on one idle H100 (small.en, float16): ~40-120 ms for a 6-11 s clip
after warmup; ffmpeg decode ~0.1-0.2 s. Comfortably sub-second. See bench_asr.py.
"""

from __future__ import annotations

import os
import subprocess
import threading

import numpy as np

# ---------------------------------------------------------------------------
# Configuration (env-overridable so WS2/WS6 can tune without editing code)
# ---------------------------------------------------------------------------
# Model size. `small.en` is the V0 default: lowest latency + smallest footprint
# while still transcribing short English questions accurately. Bump to
# `medium.en`, `distil-large-v3`, or `large-v3-turbo` (multilingual) via env if
# you want more headroom for accents/noise.
_MODEL_NAME = os.environ.get("ASR_MODEL", "small.en")

# Device + compute type. Auto-detects CUDA; falls back to CPU (int8) so the
# module still works on a CPU-only box. This is GPU-light — do not grab a full
# GPU away from WS1; a shared/fractional device or CPU is fine.
_DEVICE = os.environ.get("ASR_DEVICE", "auto")
_COMPUTE_TYPE = os.environ.get("ASR_COMPUTE_TYPE", "")  # "" => choose per device
# Optional: pin to a specific CUDA index (e.g. share WS1's last GPU). Default 0.
_DEVICE_INDEX = int(os.environ.get("ASR_DEVICE_INDEX", "0"))

_DEFAULT_LANGUAGE = os.environ.get("ASR_LANGUAGE", "en")
_BEAM_SIZE = int(os.environ.get("ASR_BEAM_SIZE", "1"))

_TARGET_SR = 16000  # faster-whisper expects 16 kHz mono

# ---------------------------------------------------------------------------
# Model singleton (thread-safe lazy load)
# ---------------------------------------------------------------------------
_model = None
_model_lock = threading.Lock()


def _resolve_device_and_compute() -> tuple[str, str, int]:
    """Pick (device, compute_type, device_index), honouring env overrides."""
    device = _DEVICE
    if device == "auto":
        try:
            import ctranslate2

            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            device = "cpu"

    compute_type = _COMPUTE_TYPE
    if not compute_type:
        # float16 is the fast/accurate default on H100; int8 keeps CPU usable.
        compute_type = "float16" if device == "cuda" else "int8"

    device_index = _DEVICE_INDEX if device == "cuda" else 0
    return device, compute_type, device_index


def _get_model():
    """Return the shared WhisperModel, loading it once on first call."""
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is None:  # double-checked under lock
            from faster_whisper import WhisperModel

            device, compute_type, device_index = _resolve_device_and_compute()
            _model = WhisperModel(
                _MODEL_NAME,
                device=device,
                compute_type=compute_type,
                device_index=device_index,
            )
    return _model


def warmup() -> None:
    """
    Load the model and prime the VAD/JIT paths so the FIRST real request is fast.

    Optional but recommended: WS2 can call asr.warmup() at FastAPI startup. If
    skipped, the first transcribe() simply pays the one-time load cost itself.
    """
    model = _get_model()
    silence = np.zeros(_TARGET_SR // 2, dtype=np.float32)  # 0.5 s of silence
    try:
        segments, _ = model.transcribe(
            silence,
            language=_DEFAULT_LANGUAGE,
            beam_size=1,
            vad_filter=True,
        )
        # Consume the generator so the VAD model + kernels actually initialize.
        list(segments)
    except Exception:
        # Warmup is best-effort; never let it crash startup.
        pass


# ---------------------------------------------------------------------------
# Audio decode (ffmpeg, any container/codec -> 16 kHz mono float32 PCM)
# ---------------------------------------------------------------------------
def _decode_to_pcm(audio_bytes: bytes) -> np.ndarray:
    """
    Decode an arbitrary browser audio blob to a mono 16 kHz float32 numpy array
    in [-1, 1], using ffmpeg on stdin/stdout (no temp files).

    Returns an empty array if the input is empty or not decodable (garbled blob,
    wrong/missing data) — the caller maps that to "".
    """
    if not audio_bytes:
        return np.zeros(0, dtype=np.float32)

    cmd = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel", "error",
        "-i", "pipe:0",      # read the blob from stdin; container is auto-detected
        "-f", "f32le",       # raw 32-bit float little-endian PCM
        "-acodec", "pcm_f32le",
        "-ac", "1",          # mono
        "-ar", str(_TARGET_SR),  # 16 kHz
        "pipe:1",            # write PCM to stdout
    ]
    try:
        proc = subprocess.run(
            cmd,
            input=audio_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError as exc:  # ffmpeg not on PATH
        raise RuntimeError(
            "ffmpeg not found on PATH; ASR requires ffmpeg (7.1 in the `moe` env) "
            "to decode browser audio blobs."
        ) from exc

    if proc.returncode != 0 or not proc.stdout:
        # Unreadable/garbled input — treat as no audio rather than raising.
        return np.zeros(0, dtype=np.float32)

    return np.frombuffer(proc.stdout, dtype=np.float32)


# ---------------------------------------------------------------------------
# Public contract (WS2 depends on EXACTLY this signature)
# ---------------------------------------------------------------------------
def transcribe(audio_bytes: bytes, mime: str | None = None) -> str:
    """
    Browser audio blob -> text. Handles iOS audio/mp4 (AAC) and webm/opus.

    Args:
        audio_bytes: raw bytes of the recorded audio blob (whatever
            MediaRecorder produced: iOS -> audio/mp4 AAC, else -> audio/webm
            Opus). The container is auto-detected by ffmpeg, so `mime` is only
            advisory.
        mime: optional MIME type of the blob (e.g. "audio/mp4", "audio/webm").
            Currently informational only; decoding does not depend on it.

    Returns:
        The transcribed text, stripped. Empty/silent/garbled audio -> "".
    """
    pcm = _decode_to_pcm(audio_bytes)
    # ~0.05 s of audio = 800 samples; below that there is nothing to transcribe.
    if pcm.size < 800:
        return ""

    model = _get_model()
    try:
        segments, _info = model.transcribe(
            pcm,
            language=_DEFAULT_LANGUAGE,
            beam_size=_BEAM_SIZE,
            vad_filter=True,  # drop non-speech so silence/noise -> ""
        )
        text = "".join(segment.text for segment in segments)
    except Exception:
        # Never propagate a transcription failure to the request path; an empty
        # string lets the user retry or type instead.
        return ""

    return text.strip()


__all__ = ["transcribe", "warmup"]
