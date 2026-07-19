"""Acoustic-event captioning backend switch — ACOUSTIC_BACKEND = off | mock | ast.

Acoustic captioning APPENDS a ``discriminator="acoustic"`` ``content.kind="caption"``
record for the chunk's non-speech audio — captioned, not dropped (CHARTER OQ12). Like
translation it never mutates the primary transcript, so it never tags the audio
``pipeline_version`` and never forks the primary.

When active it emits a caption for EVERY chunk (deterministic). A production deployment
may gate on real non-speech salience to avoid captioning every chunk; that gate is a
backend concern (the real ``ast`` path could threshold on non-speech energy), not a seam
change — documented, deferred.

``ast`` (transformers+torch) is LAZY-IMPORTED only inside ``select`` on the ast path, so
importing this package never pulls transformers/torch.
"""
from __future__ import annotations

from ..config import AudioConfig

_BACKENDS = {"mock", "ast"}


def _resolve(cfg: AudioConfig) -> str:
    """Canonical backend — ``'off' | 'mock' | 'ast'``; unrecognized → ``'off'``."""
    backend = cfg.acoustic_backend
    return backend if backend in _BACKENDS else "off"


def select(cfg: AudioConfig):
    """Return the resolved acoustic backend module, or ``None`` when off. The backend
    exposes ``caption(audio_bytes, codec, span_seconds, cfg, chunk_id)
    -> AcousticResult | None``."""
    name = _resolve(cfg)
    if name == "mock":
        from . import mock

        return mock
    if name == "ast":
        from . import ast  # lazy: pulls transformers + torch only here

        return ast
    return None
