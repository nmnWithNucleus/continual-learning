"""Mock OCR backend — the headless default (no GPU, no network, no new DP dependency).

Returns a small, canned set of regions per frame so the whole post-processing pipeline
(role assignment, min-chars, redaction, dedup, budget, single-line render) is exercised
with the mock default, keeping the suite green with ``VIDEO_OCR_BACKEND=mock``.

DETERMINISM (finding #5): the text derives ONLY from ``(chunk_id, frame.index)`` — never
from the JPEG bytes. A mock keyed on pixel/byte length would make CI text vary by ffmpeg
build, breaking the fleet-determinism contract (§4 R4). Pixels are ignored entirely, so
this works for real ``jpeg_hi`` frames AND the ``jpeg_hi is None`` synthetic fallback.

A per-chunk-constant "titlebar" line is emitted on every frame on purpose: across two
frames it is >= the dedup threshold, so the mock naturally exercises D-07 step 5.
"""
from __future__ import annotations

from ..clip_types import Frame, OcrRead, OcrRegion
from .config import OcrConfig


def make_client(cfg: OcrConfig):
    """No client — the mock reaches no network."""
    return None


def read(cfg: OcrConfig, frame: Frame, client, chunk_id: str) -> OcrRead:
    """Deterministic regions for one frame from ``(chunk_id, frame.index)`` only.

    ``role=""`` on both regions so ``assemble.assign_role`` derives the role from the
    normalized bbox — exercising that path too."""
    tag = (chunk_id or "chunk")[:8]
    idx = frame.index
    regions = (
        # Constant across frames within a chunk -> dedups on the 2nd+ frame (step 5).
        OcrRegion(
            text=f"mock window {tag}",
            role="",
            bbox=(0.0, 0.0, 0.55, 0.03),   # top strip -> titlebar
            conf=0.99,
        ),
        # Varies per frame -> survives dedup; > min_chars.
        OcrRegion(
            text=f"mock line {idx} lorem ipsum",
            role="",
            bbox=(0.10, 0.40, 0.92, 0.45),  # mid content -> main
            conf=0.95,
        ),
    )
    return OcrRead(t_offset_s=frame.t_offset_s, regions=regions)
