"""Mock video captioner — the DEFAULT, no-GPU / no-network path.

Returns a canned caption per keyframe that references the chunk_id + keyframe
index (so it is unmistakably a mock, never a real VLM description) plus a mock
on-screen-text string, kept separate so the Processor weaves it into the caption
per D8 exactly as the real captioner's OCR will be. Pixels are ignored, so it
works for both real extracted keyframes and the synthetic fallback.

``PIPELINE_VERSION`` is the mock dialect stamped into every C2 this backend
produces; distinct from the vlm dialect so a reprocess under a different backend
forks a new record_id (version-forward), per the C2 contract.
"""
from __future__ import annotations

from typing import Any

from .config import VisionSettings
from .result import Keyframe, KeyframeCaption

# Stamped into every C2 this backend produces. Kept byte-for-byte equal to the
# pre-real-pipeline stub's version so the mock video dialect (and its record_ids)
# never forks when the real pipeline lands behind the default-off vlm switch.
PIPELINE_VERSION = "vidproc-mock-v0"


def caption(
    vs: VisionSettings, keyframes: list[Keyframe], c1: dict[str, Any]
) -> list[KeyframeCaption]:
    chunk_id = c1["chunk_id"]
    n = len(keyframes)
    out: list[KeyframeCaption] = []
    for kf in keyframes:
        idx = kf.index
        when = "" if kf.t_offset_s is None else f" at t+{kf.t_offset_s:.1f}s"
        desc = (
            f"[mock video caption · VIDEO_BACKEND=mock] Keyframe {idx} of {n} "
            f"for chunk {chunk_id}{when}: a person at a desk working on a laptop. "
            "Set VIDEO_BACKEND=vlm for a real VLM description."
        )
        out.append(
            KeyframeCaption(index=idx, caption=desc, ocr_text=f"slide {idx + 1}")
        )
    return out
