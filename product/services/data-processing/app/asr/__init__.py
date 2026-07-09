"""ASR backends behind the ASR_BACKEND switch.

Each backend exposes:
  * ``PIPELINE_VERSION``: the stamped pipeline dialect string for records it produces.
  * ``transcribe(settings, audio_bytes, codec, chunk_seconds, chunk_id) -> AsrResult``

``mock`` is the DEFAULT and imports no heavy deps, so the whole learn loop runs
headless on any box. ``faster_whisper`` is LAZY-IMPORTED only when selected, so
mock unit tests need NOT install torch / faster-whisper.
"""
from __future__ import annotations

from ..config import Settings


def select(settings: Settings):
    """Return the ASR backend module for the configured ASR_BACKEND."""
    if settings.asr_backend == "faster_whisper":
        from . import faster_whisper  # lazy: pulls torch/av only on this path
        return faster_whisper
    # Default (and any unrecognized value) -> mock, the no-GPU path.
    from . import mock
    return mock
