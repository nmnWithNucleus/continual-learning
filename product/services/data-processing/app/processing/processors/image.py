"""Image plugin (STUB): one 'caption' unit with OCR woven into the caption.

Mock transform only — no VLM / OCR model. It stands in for the CHARTER image
pipeline (ImgProc -> OCR-specialist pass -> dense caption with OCR woven in ->
world-data injection) and the D8 decision to decouple OCR from the base model: the
on-screen text is written INTO the description target rather than read from pixels
at inference. So the mock caption ends with a ``. On-screen text: <mock>`` clause,
exactly the shape the real dense-caption+OCR pass will emit.

One image chunk -> one record (``discriminator=''``). Real ImgProc/OCR/caption
models land in the image-modality session by editing ONLY this file.
"""
from __future__ import annotations

from typing import Any

from ...config import Settings
from ..base import ProcessedContent, ProcessedUnit, Processor, empty_enrichments
from ..registry import register

PIPELINE_VERSION = "imgproc-mock-v0"


@register
class ImageProcessor(Processor):
    modality = "image"
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
        # Dense caption with the OCR-specialist text woven in (D8), mocked.
        caption = (
            f"[mock image caption for chunk {chunk_id}] "
            "A screenshot of a desktop application window with a toolbar and a text pane. "
            "On-screen text: [mock OCR] 'File  Edit  View  Help'."
        )
        content = ProcessedContent(kind="caption", text=caption)
        return [
            ProcessedUnit(
                content=content,
                enrichments=empty_enrichments(),
                discriminator="",  # 1:1 — one frame, one caption
            )
        ]
