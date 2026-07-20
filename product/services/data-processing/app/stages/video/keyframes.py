"""Video PREP stage: extract timestamped keyframes (ffmpeg scene-change ∪ uniform grid).

A sidecar-kind stage that emits NO units — it only ``provides`` the ``keyframes`` slot the
captions primary consumes. Byte-identical transplant of the monolithic processor's step 1,
including both fallbacks:

  * decode fails under the MOCK dev backend → ``SYNTHETIC_KEYFRAMES`` timing-less
    keyframes (the pre-real-pipeline shape, so the loop stays headless + seam tests green);
  * decode fails under the real VLM dialect → RAISE (the chunk is redelivered; placeholder
    captions must never persist as processed truth under ``vidproc-vlm-v0``).

Extraction is called through ``video_proc.extract_keyframes`` (the shim module attribute),
so the existing tests that monkeypatch ``video_proc.extract_keyframes`` keep intercepting —
a late-bound lookup, never a ``from … import`` snapshot.
"""
from __future__ import annotations

from ...processing.processors import video as video_proc
from ...stagegraph import Stage, StageContext, StageResult, register_stage
from ...vision.config import get_vision_settings
from ...vision.result import Keyframe


@register_stage
class KeyframesStage(Stage):
    name = "keyframes"
    modality = "video"
    kind = "sidecar"          # emits no units; only provides a slot
    order = 0
    provides = ("keyframes", "vision_settings")

    def run_sync(self, ctx: StageContext) -> StageResult:
        vs = get_vision_settings()
        keyframes = video_proc.extract_keyframes(
            ctx.blob, ctx.c1["codec"], ctx.span_seconds, vs
        )
        if not keyframes:
            if vs.backend == "vlm":
                raise RuntimeError(
                    f"video chunk {ctx.c1['chunk_id']}: no decodable keyframes "
                    "(ffmpeg absent/timed out or undecodable bytes) — refusing to "
                    "emit placeholder captions under the vlm dialect; the chunk "
                    "will be redelivered"
                )
            keyframes = [
                Keyframe(index=i, t_offset_s=None, t_end_offset_s=None, image_jpeg=None)
                for i in range(video_proc.SYNTHETIC_KEYFRAMES)
            ]
        return StageResult(slots={"keyframes": keyframes, "vision_settings": vs})
