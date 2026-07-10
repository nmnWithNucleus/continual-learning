"""Text plugin (STUB): one normalized 'text' unit.

Mock transform only. It stands in for the CHARTER text pipeline (normalization:
encoding, whitespace, structure) for typed / clipboard / extension capture. The
real normalizer replaces the mock body by editing ONLY this file; the seam stays
put. content.kind is the frozen C2 ``text`` value.

One text chunk -> one record (``discriminator=''``).
"""
from __future__ import annotations

from typing import Any

from ...config import Settings
from ..base import ProcessedContent, ProcessedUnit, Processor, empty_enrichments
from ..registry import register

PIPELINE_VERSION = "textnorm-mock-v0"


def _mock_normalize(raw: bytes) -> str:
    """Stand-in normalizer: decode as UTF-8 (lenient), collapse whitespace runs,
    strip NULs and edges. Real normalization (encoding/structure) lands later."""
    text = raw.decode("utf-8", errors="replace").replace("\x00", "")
    return " ".join(text.split())


@register
class TextProcessor(Processor):
    modality = "text"
    content_kind = "text"

    def pipeline_version(self, settings: Settings) -> str:
        return PIPELINE_VERSION

    def process(
        self,
        c1: dict[str, Any],
        blob: bytes,
        settings: Settings,
        span_seconds: float,
    ) -> list[ProcessedUnit]:
        normalized = _mock_normalize(blob)
        text = f"[mock text normalization for chunk {c1['chunk_id']}] {normalized}"
        content = ProcessedContent(kind="text", text=text, language="en")
        return [
            ProcessedUnit(
                content=content,
                enrichments=empty_enrichments(),
                discriminator="",  # 1:1 — one text capture, one record
            )
        ]
