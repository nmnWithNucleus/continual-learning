"""The neutral shapes the vision backends speak.

A ``Keyframe`` is one selected still from the chunk: its JPEG bytes plus the
CHUNK-RELATIVE sub-span [t_offset_s, t_end_offset_s) it stands for (seconds from
the chunk start). The video Processor maps that offset window to absolute RFC3339
and hands it to ``build_c2`` as the unit's per-keyframe sub-span, so a chunk's many
keyframe records no longer collide on the shared chunk span (CHARTER OQ14a).

``image_jpeg`` is ``None`` for a SYNTHETIC keyframe — the fallback the Processor
emits when the blob can't be decoded (e.g. the seam's tiny non-video fixture, or a
box without ffmpeg). A synthetic keyframe has no real timing (``t_offset_s`` /
``t_end_offset_s`` are ``None``), so its record carries the chunk span verbatim —
byte-identical to the pre-real-pipeline mock. Captioners must tolerate a ``None``
image (emit a placeholder, make no model/network call).

``KeyframeCaption`` is what a backend returns per keyframe: the dense caption plus
the OCR-transcribed on-screen text (D8) kept separate so the Processor can both
weave it into the caption AND, if asked, emit a distinct ``kind='ocr'`` record.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Keyframe:
    index: int                          # 0-based keyframe index within the chunk
    t_offset_s: Optional[float]         # sub-span start, seconds from chunk start
    t_end_offset_s: Optional[float]     # sub-span end,   seconds from chunk start
    image_jpeg: Optional[bytes]         # JPEG bytes; None for a synthetic keyframe
    width: Optional[int] = None
    height: Optional[int] = None

    @property
    def synthetic(self) -> bool:
        """A placeholder keyframe with no decoded pixels and no real timing."""
        return self.image_jpeg is None


@dataclass
class KeyframeCaption:
    index: int                          # matches the Keyframe it captions
    caption: str                        # dense caption (world description)
    ocr_text: Optional[str] = None      # legible on-screen text, D8 ('' / None = none)
