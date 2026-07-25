"""Video SIDECAR stage: OCR the changed frames at native resolution → ONE `kind='ocr'`.

The DP-side half of D-06/D-07/D-08. Consumes ``clip_frames`` (from ``clipprep``), reads
the OCR-selected frames at native ``VIDEO_OCR_FRAME_WIDTH`` through the backend seam
(``off | mock | ppocr | vlm``), runs the D-07 post-processing (confidence gate, region-role
assignment, min-chars, deterministic secret redaction, within-chunk dedup, budget) to ONE
self-anchored single line, then:

  * ``provides`` that line as the ``ocr_text`` slot the caption injects (D-09), AND
  * emits EXACTLY ONE ``kind='ocr'`` unit — ``discriminator="ocr"``, ``t_start=None`` so
    ``build_c2`` carries the C1 span verbatim (D-05) — ALWAYS, with ``content.text == ""``
    when nothing legible was found (record PRESENCE is the coverage signal, §4 R3(e)).

WIRING (Architecture A, ratified): ``kind='sidecar'``, ``policy='required'``, ``order 15``,
``needs=("clipprep",)``, ``provides=("ocr_text",)``. The locked clip band is
``clipprep=5``, ``screentext=15``, ``clipcap=20`` — all distinct from the retained legacy
``keyframes=0`` / ``captions=10`` (order is unique per modality, so ``order 10`` would COLLIDE
with the legacy ``captions`` stage at registration). It FEEDS the caption, so by §4.3 R1 its
``version_fragment`` is non-empty in every enabled configuration (``ocr.version_tag`` — even
``off`` carries ``+ocr-off-v1``). ``required`` (not ``best_effort``): a skipped OCR silently
changes the caption (its input), the "a lost mutation would be a silent lie" class that
R3(b)/R4 forbid for a fragment-bearing stage; the honest escape from an outage is
``VIDEO_OCR_BACKEND=off`` (which says so in the dialect), never a policy flip.

REGISTRATION is standard and unconditional (``@register_stage``), so the stage is statically
discoverable and any order collision or dangling need surfaces loudly at discovery. It is
dormant unless clip mode is on (``enabled()`` is clip-mode-only, default ``keyframe``). On the
integration base ``clipprep`` (WS-B) is a permanent sibling, so ``needs=("clipprep",)``
closes; on a WS-C-only branch where clipprep is absent, ``_discover()`` raises
``needs unknown stage 'clipprep'`` — expected, and correct against the integration base. Unit
tests drive the class directly (they do not require the registry).
"""
from __future__ import annotations

import logging

from ...processing.base import ProcessedContent, ProcessedUnit, empty_enrichments
from ...stagegraph import Stage, StageContext, StageResult, register_stage
from ...vision import ocr
from ...vision.clip_types import ClipFrames, Frame
from ...vision.mode import resolve_pipeline
from ...vision.ocr import assemble
from ...vision.ocr.config import get_ocr_config

logger = logging.getLogger("data-processing.stages.video.screentext")

# Match tolerance between an ``ocr_times`` value and a frame's ``t_offset_s`` (they come
# from the same clipprep pass; this only guards float re-parse jitter).
_MATCH_TOL_S = 0.25


def _count(metrics, name: str, labels: dict | None = None, amount: float = 1.0) -> None:
    """Increment a counter, guarded: metrics may be None (isolation) or the family may be
    undeclared on this branch (WS-F declares them) — a metric must never fail a chunk."""
    if metrics is None:
        return
    try:
        metrics.inc(name, labels or {}, amount)
    except Exception:  # noqa: BLE001 - metrics are best-effort by contract
        logger.debug("metric %s not recorded", name, exc_info=True)


def _match_frame(frames: tuple[Frame, ...], t: float) -> Frame | None:
    """The frame whose ``t_offset_s`` is closest to an OCR-selected time ``t`` (within
    tolerance), or ``None`` if the requested time has no extracted frame."""
    best, best_d = None, _MATCH_TOL_S
    for f in frames:
        d = abs(f.t_offset_s - t)
        if d <= best_d:
            best, best_d = f, d
    return best


@register_stage
class ScreentextStage(Stage):
    name = "screentext"
    modality = "video"
    kind = "sidecar"
    policy = "required"
    needs = ("clipprep",)
    provides = ("ocr_text",)
    order = 15  # locked clip band: clipprep=5, screentext=15, clipcap=20 (10 is legacy captions)

    def enabled(self, settings) -> bool:
        return resolve_pipeline() == "clip"

    def version_fragment(self, settings) -> str:
        # Non-empty whenever enabled (R1): the OCR text is an INPUT to the caption, so its
        # config is caption-affecting and must fork. Unknown backend -> 'off' -> still
        # non-empty (+ocr-off-v1), never '' — see app/vision/ocr/__init__.py.
        return ocr.version_tag(get_ocr_config())

    def run_sync(self, ctx: StageContext) -> StageResult:
        cfg = get_ocr_config()
        # (D-06) fail loud NOW if the sidecar serves a different model than config pins —
        # before any read, before any record is assembled. No-op for mock/off/vlm.
        ocr.assert_health(cfg)

        clip_frames: ClipFrames = ctx.slots["clip_frames"]
        backend = ocr.select(cfg)  # module, or None when off
        metrics = getattr(ctx.resources, "metrics", None)
        chunk_id = ctx.c1.get("chunk_id", "")

        reads = []
        attempted = errors = 0
        if backend is not None:
            client = backend.make_client(cfg)
            try:
                for t in clip_frames.ocr_times:
                    frame = _match_frame(clip_frames.frames, t)
                    if frame is None:
                        continue  # a selected time with no extracted frame — nothing to read
                    attempted += 1
                    try:
                        reads.append(backend.read(cfg, frame, client, chunk_id))
                    except Exception as exc:  # noqa: BLE001 - per-frame errors are absorbed
                        errors += 1
                        _count(metrics, "dp_ocr_frame_errors_total")
                        logger.warning(
                            "OCR read failed for chunk %s at t+%.2fs: %s",
                            chunk_id, t, exc,
                        )
            finally:
                if client is not None:
                    client.close()

        # >50% of a chunk's OCR frames erroring RAISES (required -> chunk fails ->
        # at-least-once redelivery); a minority is absorbed + counted (A-10).
        if attempted and errors / attempted > 0.5:
            raise RuntimeError(
                f"OCR failed on {errors}/{attempted} frames (>50%) for chunk {chunk_id} — "
                "refusing to write a corpus with the majority of on-screen text lost"
            )

        text, n_redactions = assemble.render(reads, cfg, ctx.span_seconds)
        if n_redactions:
            _count(metrics, "dp_ocr_redactions_total", amount=n_redactions)
        _count(metrics, "dp_video_ocr_events", amount=float(attempted))

        # Exactly ONE ocr unit, ALWAYS (D-05): fixed discriminator, chunk-span record,
        # empty text when nothing legible. t_start=None -> build_c2 carries the C1 span
        # verbatim (never calls abs_time -> structurally immune to the Z vs +00:00 defect).
        unit = ProcessedUnit(
            content=ProcessedContent(kind="ocr", text=text),
            enrichments=empty_enrichments(),
            discriminator="ocr",
            t_start=None,
            t_end=None,
        )
        return StageResult(slots={"ocr_text": text}, units=[unit])
