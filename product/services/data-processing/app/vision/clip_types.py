"""Frozen dataclasses shared by the clip video stages.

Foundation shapes for the screen-video pipeline. Do not change these without a
contract review — every stage imports this module rather than redefining types locally.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Frame:
    """One extracted still. Two JPEG renditions: ``jpeg_lo`` downscaled to the
    caption frame width for the VLM, ``jpeg_hi`` at the OCR frame width (native)
    for OCR. Either may be ``None`` (synthetic / undecodable), and a captioner/OCR
    backend must tolerate that."""

    index: int
    t_offset_s: float
    jpeg_lo: bytes | None
    jpeg_hi: bytes | None


@dataclass(frozen=True)
class DeltaCell:
    """One 32x32 cell of a change map between two analysis frames."""

    peak: int      # 0..255 max binarized change within the cell
    spread: int    # 0..1024 count of cells over the change threshold


@dataclass(frozen=True)
class Delta:
    """The analysis-pass output: per-analysis-time change maps + the anchor
 accumulator (per-cell change accumulated since the last OCR read)."""

    times: tuple[float, ...]
    cells: tuple[DeltaCell, ...]
    accum: tuple[int, ...]


@dataclass(frozen=True)
class ClipFrames:
    """What ``clipprep`` provides: the captioner frames, the OCR-frame times the
 delta gate selected, an idle flag, and the chunk span in seconds."""

    frames: tuple[Frame, ...]
    ocr_times: tuple[float, ...]
    idle: bool
    span_s: float


@dataclass(frozen=True)
class OcrRegion:
    """One recognized text region: the string, a coarse role assigned from bbox
 position, the pixel bbox (used internally for reading order + role, then
 discarded — NOT emitted to C2, see), and the engine confidence."""

    text: str
    role: str      # titlebar|tab|sidebar|main|compose|message|toolbar|statusbar|dialog|notification
    bbox: tuple[float, float, float, float]
    conf: float


@dataclass(frozen=True)
class OcrRead:
    """All regions recognized at one OCR-frame time (chunk-relative seconds)."""

    t_offset_s: float
    regions: tuple[OcrRegion, ...]


@dataclass(frozen=True)
class ClipDesc:
    """What the clip captioner returns: the structured fields the prompt asks for
 plus the raw reply and whether the strict parse succeeded (a lenient fallback
 sets ``parsed=False``)."""

    app: str
    activity: str
    description: str
    sensitive: bool
    raw: str
    parsed: bool
