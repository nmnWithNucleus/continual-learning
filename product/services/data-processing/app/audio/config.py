"""Audio-pipeline runtime knobs, read fresh from the environment per request.

The shared ``app/config.py`` Settings is READ-ONLY to the audio workstream (a parallel
video session shares this repo), so the diarization / translation / acoustic knobs live
HERE and are read via ``os.getenv()`` — mirroring ``app/config.py``'s per-request
``get_settings()`` posture (read at call time so a test can flip a knob without
re-importing the app).

DEFAULTS ARE OFF for every capability. ``off`` means the corresponding audio stage is a
pure no-op, so the default pipeline output is byte-identical to the pre-fill processor:

  * the mock ASR dialect (``asr-mock-v0``) stays untouched;
  * the M0 + Processor-seam test baseline stays green;
  * ``run_learn.sh`` (ASR_BACKEND=mock) still runs the loop headless with no new deps.

Turning a capability on is an explicit opt-in (set its ``*_BACKEND`` to mock/pyannote/
whisper/ast). Diarization mutates the primary transcript record, so activating it
version-forks the audio ``pipeline_version`` (see ``diarize.version_tag``); translation
and acoustic captioning only ADD sidecar records, so they never disturb the primary.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _norm(value: str) -> str:
    return value.strip().lower()


def _int(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: str, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class AudioConfig:
    # ---- diarization (stage 2) ----
    diarize_backend: str        # off (default) | mock | pyannote
    diarize_speakers: int       # mock: how many synthetic speakers to lay down (>=1)
    diarize_min_speakers: int   # pyannote hint (0 = let the model decide)
    diarize_max_speakers: int   # pyannote hint (0 = let the model decide)
    hf_token: str               # HF auth for the gated pyannote model ('' = none)
    # ---- translation (stage 3) ----
    translate_backend: str      # off (default) | mock | whisper
    translate_target: str       # '' = off | BCP-47 target (whisper backend => 'en')
    # ---- acoustic events (stage 4) ----
    acoustic_backend: str       # off (default) | mock | ast
    acoustic_top_k: int         # top-k event tags folded into the caption (>=1)
    acoustic_threshold: float   # min per-tag score to include (real backend only)


def get_audio_config() -> AudioConfig:
    return AudioConfig(
        diarize_backend=_norm(os.getenv("DIARIZE_BACKEND", "off")) or "off",
        diarize_speakers=max(1, _int(os.getenv("DIARIZE_SPEAKERS", "2"), 2)),
        diarize_min_speakers=max(0, _int(os.getenv("DIARIZE_MIN_SPEAKERS", "0"), 0)),
        diarize_max_speakers=max(0, _int(os.getenv("DIARIZE_MAX_SPEAKERS", "0"), 0)),
        hf_token=(os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN") or "").strip(),
        translate_backend=_norm(os.getenv("TRANSLATE_BACKEND", "off")) or "off",
        translate_target=_norm(os.getenv("TRANSLATE_TARGET", "")),
        acoustic_backend=_norm(os.getenv("ACOUSTIC_BACKEND", "off")) or "off",
        acoustic_top_k=max(1, _int(os.getenv("ACOUSTIC_TOP_K", "3"), 3)),
        acoustic_threshold=_float(os.getenv("ACOUSTIC_THRESHOLD", "0.1"), 0.1),
    )
