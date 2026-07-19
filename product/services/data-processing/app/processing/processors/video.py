"""Video plugin: VidProc keyframe pipeline behind a VIDEO_BACKEND switch.

One C1 video chunk fans out to MANY C2 keyframe records — the seam's headline
1-chunk-many-records case. The real pipeline (replacing the former mock stub):

    extract keyframes (ffmpeg scene-change) -> caption each (mock | vlm) ->
    weave OCR into the caption (D8) -> one ProcessedUnit per keyframe, each with
    its OWN time-spine sub-span (CHARTER OQ14a)

* ``VIDEO_BACKEND=mock`` (DEFAULT, no GPU / no network): canned captions, so the
  whole learn loop runs headless on any box. ``vlm`` late-binds a real captioner
  (``app.vision.vlm``, httpx to an OpenAI-compatible VL endpoint — the Qwen3-VL on
  the GPU node, or a lighter local captioner). The switch + all knobs are read via
  ``app.vision.config`` (``os.getenv``), so the shared-core config file is untouched.
* Keyframe **timing** comes from real frame extraction, independent of the caption
  backend: when the blob decodes (ffmpeg present + real video), each keyframe gets
  a distinct ``[t_start, t_end)`` sub-span so a chunk's records no longer collide
  on storage's ``(user_id, t_start)`` index. When the blob does NOT decode (the
  seam's tiny non-video fixture, or a box without ffmpeg), we fall back to
  ``SYNTHETIC_KEYFRAMES`` timing-less keyframes carrying the chunk span verbatim —
  byte-identical to the pre-real-pipeline stub, so the existing seam tests stay
  green with or without ffmpeg installed.
* **OCR (D8):** the captioner returns on-screen text separately; we weave it into
  the caption written to /context (so the user model learns text from the
  description target, not by reading pixels at inference). Optionally
  (``VIDEO_OCR_RECORDS=1``) we ALSO emit a distinct ``content.kind='ocr'`` record
  per keyframe. Structured bbox geometry is a later additive-C2 field, out of
  frozen scope (owned by the image build per CHARTER OQ14b).

Real VLM / keyframe-selection models evolve by editing ONLY this file + the
``app/vision/`` namespace; the seam and shared core stay put.
"""
from __future__ import annotations

from typing import Any, Optional

from ...config import Settings
from ...timeutil import abs_time, parse_rfc3339
from ...vision import select as select_captioner
from ...vision.config import get_vision_settings
from ...vision.frames import extract_keyframes
from ...vision.result import Keyframe, KeyframeCaption
from ..base import ProcessedContent, ProcessedUnit, Processor, empty_enrichments
from ..registry import register

# Timing-less keyframes emitted when the blob can't be decoded (non-video bytes /
# no ffmpeg). Matches the pre-real-pipeline stub's fan-out so the seam contract
# (one video chunk -> a few distinct keyframe records) holds even with no decoder.
SYNTHETIC_KEYFRAMES = 3

# Re-exported so callers/tests that reference the mock video dialect keep a stable
# handle (mirrors how the seam tests import the audio backend's PIPELINE_VERSION).
from ...vision.mock import PIPELINE_VERSION  # noqa: E402  (mock dialect = default)


def _weave_ocr(caption: str, ocr_text: Optional[str]) -> str:
    """D8: fold the OCR-transcribed on-screen text into the caption target."""
    caption = caption.rstrip()
    if not ocr_text:
        return caption
    sep = "" if caption.endswith(".") or not caption else "."
    return f"{caption}{sep} On-screen text: '{ocr_text}'."


@register
class VideoProcessor(Processor):
    modality = "video"
    content_kind = "caption"

    def pipeline_version(self, settings: Settings) -> str:
        return select_captioner(get_vision_settings()).PIPELINE_VERSION

    def process(
        self,
        c1: dict[str, Any],
        blob: bytes,
        settings: Settings,
        span_seconds: float,
    ) -> list[ProcessedUnit]:
        vs = get_vision_settings()

        # 1) Extract timestamped keyframes; fall back to timing-less synthetic ones
        #    when the blob doesn't decode (keeps the loop headless + seam-green).
        keyframes = extract_keyframes(blob, c1["codec"], span_seconds, vs)
        if not keyframes:
            keyframes = [
                Keyframe(index=i, t_offset_s=None, t_end_offset_s=None, image_jpeg=None)
                for i in range(SYNTHETIC_KEYFRAMES)
            ]

        # 2) Caption each keyframe via the selected backend (mock default / vlm).
        captions = select_captioner(vs).caption(vs, keyframes, c1)
        by_index = {c.index: c for c in captions}

        # 3) Assemble one (or two, with OCR records) ProcessedUnit per keyframe.
        base = parse_rfc3339(c1["t_start"])
        units: list[ProcessedUnit] = []
        for kf in keyframes:
            cap: KeyframeCaption = by_index.get(
                kf.index, KeyframeCaption(index=kf.index, caption="", ocr_text=None)
            )
            t_start, t_end = self._sub_span(kf, base, span_seconds, c1)

            units.append(
                ProcessedUnit(
                    content=ProcessedContent(
                        kind="caption",
                        text=_weave_ocr(cap.caption, cap.ocr_text),
                    ),
                    enrichments=empty_enrichments(),
                    discriminator=str(kf.index),  # keyframe index -> distinct record_id
                    t_start=t_start,
                    t_end=t_end,
                )
            )

            # D8 (optional): also emit the OCR text as its own kind='ocr' record.
            if vs.ocr_records and cap.ocr_text:
                units.append(
                    ProcessedUnit(
                        content=ProcessedContent(kind="ocr", text=cap.ocr_text),
                        enrichments=empty_enrichments(),
                        discriminator=f"{kf.index}:ocr",
                        t_start=t_start,
                        t_end=t_end,
                    )
                )

        return units

    @staticmethod
    def _sub_span(
        kf: Keyframe, base, span_seconds: float, c1: dict[str, Any]
    ) -> tuple[Optional[str], Optional[str]]:
        """Map a keyframe's chunk-relative offsets to an absolute RFC3339 sub-span,
        clamped into the chunk span. Returns (None, None) for a synthetic keyframe
        -> the record carries the chunk's C1 span verbatim (byte-identical).

        The outer boundaries reuse the C1 span strings verbatim (opening keyframe
        starts at the chunk start; a keyframe running to the chunk end ends at the
        chunk end), so the union of keyframe sub-spans exactly equals the declared
        chunk span with no timezone-format or float drift at the edges."""
        if kf.t_offset_s is None:
            return None, None
        start = min(max(kf.t_offset_s, 0.0), span_seconds)
        end = kf.t_end_offset_s if kf.t_end_offset_s is not None else span_seconds
        end = min(max(end, start), span_seconds)
        t_start = c1["t_start"] if start <= 1e-9 else abs_time(base, start)
        t_end = c1["t_end"] if end >= span_seconds - 1e-9 else abs_time(base, end)
        return t_start, t_end
