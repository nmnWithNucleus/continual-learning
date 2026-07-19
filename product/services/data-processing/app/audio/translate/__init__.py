"""Translation backend switch — TRANSLATE_BACKEND = off | mock | whisper.

Translation is active only when a backend is selected AND ``TRANSLATE_TARGET`` is set
(the target is the master switch; empty = off). Unlike diarization, translation does NOT
mutate the primary transcript — it APPENDS a separate ``discriminator="translation"``
record — so it never tags the audio ``pipeline_version`` and never forks the primary.

The whisper backend rides Whisper's ``task="translate"``, which is X→English ONLY, so it
supports ``TRANSLATE_TARGET=en`` only. A ``whisper`` + non-``en`` target is a misconfig:
we log it and treat translation as off (rather than 500-ing the whole ingest and losing
the primary transcript, or silently emitting an English record mislabeled with the wrong
language). Non-English targets are a future MT backend, not this path.

``whisper`` reuses the ASR ``WhisperModel`` (same cache) and is reached only through
``select``, so importing this package never loads faster-whisper.
"""
from __future__ import annotations

import logging

from ..config import AudioConfig

logger = logging.getLogger("data-processing.audio.translate")

_BACKENDS = {"mock", "whisper"}


def _resolve(cfg: AudioConfig) -> str:
    """Canonical backend — ``'off' | 'mock' | 'whisper'``. Off unless a known backend is
    selected AND a target language is set. The whisper/non-en misconfig degrades to off."""
    backend = cfg.translate_backend
    if backend not in _BACKENDS or not cfg.translate_target:
        return "off"
    if backend == "whisper" and cfg.translate_target != "en":
        logger.warning(
            "TRANSLATE_BACKEND=whisper supports only TRANSLATE_TARGET='en' "
            "(Whisper task=translate is X->English); target=%r -> translation disabled. "
            "Use a real MT backend for non-English targets.",
            cfg.translate_target,
        )
        return "off"
    return backend


def select(cfg: AudioConfig):
    """Return the resolved translate backend module, or ``None`` when off. The backend
    exposes ``translate(settings, audio_bytes, span_seconds, asr_result, target)
    -> TranslationResult``."""
    name = _resolve(cfg)
    if name == "mock":
        from . import mock

        return mock
    if name == "whisper":
        from . import whisper  # lazy: reuses the faster-whisper model, loaded on demand

        return whisper
    return None
