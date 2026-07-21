"""Video PRIMARY stage: caption each keyframe (mock | vlm) + assemble all units.

Byte-identical transplant of the monolithic processor's steps 2-3, with ONE behavioural
gain: under the real VLM backend the keyframes are now captioned CONCURRENTLY (a shared
thread-safe httpx client fanned out across the threadpool via ``asyncio.gather``,
order-preserving) instead of a sequential per-chunk loop — an 8-keyframe chunk pays ~1×
VL latency, not 8×. The mock path stays a plain (cheap) sync call, offloaded so the event
loop is never touched by pixel work.

``assemble`` (pure, after every stage finishes) emits the records EXACTLY as before: one
caption unit per keyframe with OCR woven in (D8) and its own time-spine sub-span (the
partition invariant — first/last records pinned to the C1 span verbatim), plus the
optional ``kind='ocr'`` sidecar records interleaved right after their caption when
``VIDEO_OCR_RECORDS=1``. Record emission order (``0, 0:ocr, 1, 1:ocr, …``) is part of the
frozen reply contract for ``vidproc-*-v0``.

``version_fragment`` is the base video dialect from the selected captioner
(``vidproc-mock-v0`` / ``vidproc-vlm-v0``), so a backend flip forks record_ids exactly as
``pipeline_version`` always did.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from starlette.concurrency import run_in_threadpool

from ...processing.base import ProcessedContent, ProcessedUnit, empty_enrichments
from ...stagegraph import Stage, StageContext, StageResult, register_stage
from ...timeutil import abs_time, parse_rfc3339
from ...vision import select as select_captioner
from ...vision import vlm
from ...vision.config import get_vision_settings
from ...vision.result import Keyframe, KeyframeCaption


def _weave_ocr(caption: str, ocr_text: Optional[str]) -> str:
    """D8: fold the OCR-transcribed on-screen text into the caption target."""
    caption = caption.rstrip()
    if not ocr_text:
        return caption
    sep = "" if caption.endswith(".") or not caption else "."
    return f"{caption}{sep} On-screen text: '{ocr_text}'."


def _sub_span(
    kf: Keyframe, base, span_seconds: float, c1: dict[str, Any],
    *, is_first: bool = False, is_last: bool = False,
) -> tuple[Optional[str], Optional[str]]:
    """Map a keyframe's chunk-relative offsets to an absolute RFC3339 sub-span, clamped
    into the chunk span. (None, None) for a synthetic keyframe -> build_c2 carries the
    C1 span verbatim. PARTITION INVARIANT: the union of a chunk's sub-spans equals the
    declared C1 span exactly — the first record is pinned to the chunk start (covers a
    dropped opening frame) and the last to the chunk end (covers media short of the
    declared span), reusing the C1 span strings verbatim so there is no tz/float drift."""
    if kf.t_offset_s is None:
        return None, None
    start = 0.0 if is_first else min(max(kf.t_offset_s, 0.0), span_seconds)
    if is_last:
        end = span_seconds
    else:
        end = kf.t_end_offset_s if kf.t_end_offset_s is not None else span_seconds
        end = min(max(end, start), span_seconds)
    t_start = c1["t_start"] if start <= 1e-9 else abs_time(base, start)
    t_end = c1["t_end"] if end >= span_seconds - 1e-9 else abs_time(base, end)
    return t_start, t_end


@register_stage
class CaptionsStage(Stage):
    name = "captions"
    modality = "video"
    kind = "primary"
    needs = ("keyframes",)
    provides = ("captions",)   # slot commits are declared (ownership is reviewable)
    order = 10

    def version_fragment(self, settings) -> str:
        # Base video dialect from the selected captioner (vidproc-mock-v0 | vidproc-vlm-v0).
        return select_captioner(get_vision_settings()).PIPELINE_VERSION

    async def run_async(self, ctx: StageContext) -> StageResult:
        vs = ctx.slots["vision_settings"]
        keyframes: list[Keyframe] = ctx.slots["keyframes"]
        if vs.backend == "vlm":
            # Concurrent fan-out: one shared thread-safe client, one threadpool task per
            # keyframe, order preserved. ``return_exceptions=True`` lets every in-flight
            # thread FINISH before we close the client — closing it out from under a
            # sibling still in ``client.post`` would raise spurious errors that mask the
            # real first failure. Then re-raise the first real error (the captions primary
            # is required → the chunk fails → redelivery), exactly as the sequential path.
            client = vlm.make_client(vs)
            try:
                results = await asyncio.gather(
                    *[run_in_threadpool(vlm.caption_one, client, vs, kf) for kf in keyframes],
                    return_exceptions=True,
                )
            finally:
                client.close()
            for r in results:
                if isinstance(r, BaseException):
                    raise r
            captions = list(results)
        else:
            # Mock (or any sync captioner): cheap, but still off the event loop.
            captions = await run_in_threadpool(
                select_captioner(vs).caption, vs, keyframes, ctx.c1
            )
        return StageResult(slots={"captions": {c.index: c for c in captions}})

    def assemble(self, ctx: StageContext) -> list[ProcessedUnit]:
        vs = ctx.slots["vision_settings"]
        keyframes: list[Keyframe] = ctx.slots["keyframes"]
        by_index: dict[int, KeyframeCaption] = ctx.slots["captions"]
        base = parse_rfc3339(ctx.c1["t_start"])
        units: list[ProcessedUnit] = []
        for pos, kf in enumerate(keyframes):
            cap = by_index.get(
                kf.index, KeyframeCaption(index=kf.index, caption="", ocr_text=None)
            )
            t_start, t_end = _sub_span(
                kf, base, ctx.span_seconds, ctx.c1,
                is_first=(pos == 0), is_last=(pos == len(keyframes) - 1),
            )
            units.append(
                ProcessedUnit(
                    content=ProcessedContent(
                        kind="caption", text=_weave_ocr(cap.caption, cap.ocr_text),
                    ),
                    enrichments=empty_enrichments(),
                    discriminator=str(kf.index),
                    t_start=t_start,
                    t_end=t_end,
                )
            )
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
