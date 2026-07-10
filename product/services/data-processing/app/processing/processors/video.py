"""Video plugin (STUB): MANY 'caption' units — one chunk -> many keyframe records.

This is the seam's headline pressure-test: a single C1 video chunk fans out to
several ProcessedUnits (mock keyframes), each with a distinct ``discriminator`` (the
keyframe index) so the core mints a stable, DISTINCT ``record_id`` per keyframe. It
stands in for the CHARTER video pipeline (VidProc chunking/windows -> per-keyframe
OCR pass -> dense caption with OCR woven in -> world-data injection).

Mock transform only — no VLM. Real VidProc/keyframe-selection/caption models land in
the video-modality session by editing ONLY this file (and, if per-keyframe timing
needs a home in C2, the additive-field discussion in the build report).
"""
from __future__ import annotations

from typing import Any

from ...config import Settings
from ..base import ProcessedContent, ProcessedUnit, Processor, empty_enrichments
from ..registry import register

PIPELINE_VERSION = "vidproc-mock-v0"

# Mock keyframe cadence: how many keyframes we "extract" per chunk. Real VidProc
# picks this from motion/scene-change; here it's fixed so the 1-chunk-many-records
# fan-out is exercised deterministically.
KEYFRAMES_PER_CHUNK = 3


@register
class VideoProcessor(Processor):
    modality = "video"
    content_kind = "caption"

    def pipeline_version(self, settings: Settings) -> str:
        return PIPELINE_VERSION

    def process(
        self,
        c1: dict[str, Any],
        blob: bytes,
        settings: Settings,
        span_seconds: float,
    ) -> list[ProcessedUnit]:
        chunk_id = c1["chunk_id"]
        units: list[ProcessedUnit] = []
        for idx in range(KEYFRAMES_PER_CHUNK):
            caption = (
                f"[mock video caption for chunk {chunk_id} keyframe {idx}] "
                f"Keyframe {idx} of {KEYFRAMES_PER_CHUNK}: a person at a desk working on a laptop. "
                f"On-screen text: [mock OCR] 'slide {idx + 1}'."
            )
            units.append(
                ProcessedUnit(
                    content=ProcessedContent(kind="caption", text=caption),
                    enrichments=empty_enrichments(),
                    discriminator=str(idx),  # keyframe index -> distinct record_id
                )
            )
        return units
