"""Mock clip captioner — the DEFAULT clip backend, no GPU / no network (WS-D, D2).

Returns a canned ``ClipDesc`` whose text derives from **(n_frames, span_seconds,
chunk_id) ONLY** — never the pixel bytes, never the injected OCR text, never a prompt.
That is a determinism contract (exit criterion): headless fixtures produce byte-identical
records on any worker, a heterogeneous ffmpeg/JPEG fleet cannot perturb the mock corpus,
and — because ``prompt_tag`` is ``""`` — editing a ``.prompt.md`` does not re-key the
headless records (D-13). Pixels are ignored, so it works for real extracted frames and
the synthetic ``jpeg_lo=None`` fallback alike.

``PIPELINE_VERSION`` is the mock clip dialect, distinct from the vlm dialect (a backend
flip forks record_ids, version-forward) and from the legacy ``vidproc-mock-v0`` keyframe
dialect (the two pipelines never collide).
"""
from __future__ import annotations

from typing import Any

from ..clip_types import ClipDesc, ClipFrames
from ..config import VisionSettings

# The base clip dialect for the mock backend.
PIPELINE_VERSION = "vidclip-mock-v1"


def prompt_tag(vs: VisionSettings) -> str:
    """The mock never reads a prompt pack, so it contributes NOTHING to the dialect —
    a ``.prompt.md`` edit must not re-key the headless corpus (D-13, exit criterion)."""
    return ""


async def describe(
    vs: VisionSettings,
    clip: ClipFrames,
    ocr_text: str,
    c1: dict[str, Any],
    metrics: Any = None,
) -> ClipDesc:
    """A deterministic canned clip description. Async to match the backend contract
    (the vlm path is loop-native IO); here there is no await and no blocking work."""
    n = len(clip.frames)
    chunk_id = c1.get("chunk_id", "")
    description = (
        f"[mock clip caption · VIDEO_BACKEND=mock] {n} frame(s) over "
        f"{clip.span_s:.0f}s for chunk {chunk_id}: a person working at a computer "
        "display. Set VIDEO_BACKEND=vlm for a real clip description."
    )
    return ClipDesc(
        app="mock",
        activity="working at a computer",
        description=description,
        sensitive=False,
        raw=description,
        parsed=True,
    )
