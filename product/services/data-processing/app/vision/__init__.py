"""Vision backends behind the VIDEO_BACKEND switch — the video-modality's seam.

Mirrors ``app/asr`` exactly: a captioner backend exposes

  * ``PIPELINE_VERSION``: the stamped pipeline dialect for records it produces.
  * ``caption(settings, keyframes, c1) -> list[KeyframeCaption]``

``mock`` is the DEFAULT and imports no heavy deps / needs no GPU, so the whole
learn loop runs headless on any box. ``vlm`` is LATE-BOUND only when selected
(pure httpx against an OpenAI-compatible VL endpoint — the Qwen3-VL served on the
GPU node, or any compatible captioner), so the mock default (and every mock unit
test) never needs a model, a GPU, or a network.

Frame *extraction* (``frames.py``) is backend-independent: it turns the raw video
blob into timestamped keyframe images that either backend then captions.
"""
from __future__ import annotations

from .config import VisionSettings


def select(vs: VisionSettings):
    """Return the captioner backend module for the configured VIDEO_BACKEND."""
    if vs.backend == "vlm":
        from . import vlm  # late-bound: only import the real captioner on this path
        return vlm
    # Default (and any unrecognized value) -> mock, the no-GPU / no-network path.
    from . import mock
    return mock
