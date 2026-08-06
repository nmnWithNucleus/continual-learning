"""``screentext`` — thin client over the ocr model server → the ``ocr`` slot.

The DP-side half of D-06/D-07/D-08 under the rebuild. Consumes ``clipprep``'s
transient ``ClipFrames``, sends each delta-gate-selected hi-res frame to the ocr
server (``ctx.clients["ocr"]``, the framework ``/infer`` envelope: one base64 JPEG
per call), then runs the KEPT client-side pipeline — bbox normalization → assemble
(confidence gate, reading order + role, min-chars, deterministic redaction,
within-chunk dedup, char budget) — down to ONE self-anchored line.

Slot ``ocr`` = ``{"value": <digest text>}``, emitted WHENEVER the stage ran:
``value == ""`` is the honest empty claim (OCR ran, the screen carried no legible
text — the coverage signal, L11). The slot is absent only when the stage FAILED (a
hole). OPTIONAL under L7 — v0 wired this ``policy='required'`` (a skipped OCR
silently changed the caption); v1 gets the same guarantee structurally: a screentext
failure cancels ``clipcap``'s cone (it ``needs`` this stage), so a caption can never
ship computed from missing OCR — and the record ships with BOTH holes, healed by
redrive (L8), instead of dead-lettering the chunk.

Ported v0 posture (from the v0 stage adapter, documented there as A-10): per-frame
server errors are ABSORBED and counted; >50% of a chunk's attempted frames erroring
RAISES (refusing a corpus with the majority of on-screen text lost) — now a hole +
heal instead of v0's redelivery.

Identity: the det/rec model shas the v0 config pinned (``VIDEO_OCR_MODEL_SHA_DET/REC``
asserted against the sidecar's ``/health``) now live in ``servers/manifest.json``
``expected_identity.weights.det_sha256/rec_sha256``, verified by ``ModelClient``
before a replica serves its first call — same guarantee, one home. The server's
engine settings (PP-OCRv4 det+rec, cls off, 4 threads, CPU EP) are pinned in
``servers/ocr/server.py``; together they ARE ``Backend("ppocr", 1)``.

Code pins below (L4) — the v0 env knobs they replace (v0 defaults carried forward):

  =========================  ==================  =====
  v0 env knob (dead)         pin                 value
  =========================  ==================  =====
  VIDEO_OCR_MIN_CONF         MIN_CONF            0.60
  VIDEO_OCR_MIN_CHARS        MIN_CHARS           4
  VIDEO_OCR_DEDUP_RATIO      DEDUP_RATIO         0.92
  (22 − 16, derived D-11)    OCR_CHARS_PER_SEC   6.0
  =========================  ==================  =====

Frame-time matching keeps v0's ``_MATCH_TOL_S = 0.25`` float-jitter tolerance.
The old sidecar's 48 MB request-body cap is NOT carried forward: clipprep pins
``ocr_frame_width=1728``, so a frame JPEG is bounded well under 2 MB by construction
and the cap is unreachable — a framework-level body limit would guard nothing.
"""
from __future__ import annotations

import base64
import logging

from ...stagegraph.stage import Backend, Stage, StageContext, StageOutput, register_stage
from ...vision.clip_types import ClipFrames, Frame, OcrRead, OcrRegion
from ...vision.ocr import assemble

logger = logging.getLogger("data-processing.stages.video.screentext")

# ---- code pins (L4). Editing any of these is a vB bump (they change the ocr slot's
# bytes AND, injected via D-09, the caption's). ------------------------------------
MIN_CONF = 0.60          # drop server regions below this confidence
MIN_CHARS = 4            # drop lines shorter than this
DEDUP_RATIO = 0.92       # drop a line >= this similar to the previous kept (chunk-local)
OCR_CHARS_PER_SEC = 6.0  # D-11: the OCR share of the 22 chars/second-of-life dose

# Match tolerance between an ``ocr_times`` value and a frame's ``t_offset_s`` (they come
# from the same clipprep pass; this only guards float re-parse jitter). v0 value.
_MATCH_TOL_S = 0.25

# Ported v0 posture: a minority of per-frame errors is absorbed; a majority raises.
_MAX_ERROR_FRACTION = 0.5


def _match_frame(frames: tuple[Frame, ...], t: float) -> Frame | None:
    """The frame whose ``t_offset_s`` is closest to an OCR-selected time ``t`` (within
    tolerance), or ``None`` if the requested time has no extracted frame."""
    best, best_d = None, _MATCH_TOL_S
    for f in frames:
        d = abs(f.t_offset_s - t)
        if d <= best_d:
            best, best_d = f, d
    return best


def _jpeg_dims(jpeg: bytes) -> tuple[int, int] | None:
    """Parse (width, height) from a JPEG's SOF marker — dependency-free (no PIL).
    Ported verbatim from the retired v0 client (vision/ocr/ppocr.py)."""
    if not jpeg or len(jpeg) < 4 or jpeg[0] != 0xFF or jpeg[1] != 0xD8:
        return None
    i, n = 2, len(jpeg)
    while i + 9 < n:
        if jpeg[i] != 0xFF:
            i += 1
            continue
        marker = jpeg[i + 1]
        # SOF0..SOF15 (excluding DHT/JPG/DAC 0xC4/0xC8/0xCC) carry the frame dimensions.
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height = (jpeg[i + 5] << 8) | jpeg[i + 6]
            width = (jpeg[i + 7] << 8) | jpeg[i + 8]
            return (width, height) if width and height else None
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg_len = (jpeg[i + 2] << 8) | jpeg[i + 3]
        if seg_len < 2:
            return None
        i += 2 + seg_len
    return None


def _normalize_bbox(raw: list, frame_w: int, frame_h: int) -> tuple[float, float, float, float]:
    """Normalize a wire bbox ``[x0,y0,x1,y1]`` to [0,1]. The ocr server returns PIXEL
    coordinates of the submitted image (servers/ocr/server.py contract), which divide by
    the frame dims; already-normalized coordinates (all <= 1.0) pass through. Ported
    verbatim from the retired v0 client — the Stage B carry-over confirms the
    pass-through stays valid."""
    try:
        x0, y0, x1, y1 = (float(v) for v in raw[:4])
    except (TypeError, ValueError, IndexError):
        return (0.0, 0.0, 0.0, 0.0)
    if max(abs(x0), abs(y0), abs(x1), abs(y1)) <= 1.0:
        return (x0, y0, x1, y1)
    w = float(frame_w) or 1.0
    h = float(frame_h) or 1.0
    return (x0 / w, y0 / h, x1 / w, y1 / h)


def _regions_to_read(result: dict, frame: Frame) -> OcrRead:
    """Map the server's ``{"regions": [{text, bbox, conf}, ...]}`` result onto the kept
    client-side shapes. Role is assigned DP-side from the normalized bbox in
    ``assemble.render`` (role="" here), exactly as in v0."""
    dims = _jpeg_dims(frame.jpeg_hi or b"")
    # v0 fallback geometry: the pinned OCR width and a 16:10 height estimate.
    frame_w = dims[0] if dims else 1728
    frame_h = dims[1] if dims else max(1, round(1728 * 10 / 16))
    regions = []
    for r in result.get("regions") or []:
        regions.append(OcrRegion(
            text=str(r.get("text", "")),
            role="",
            bbox=_normalize_bbox(r.get("bbox") or [], frame_w, frame_h),
            conf=float(r.get("conf", 0.0) or 0.0),
        ))
    return OcrRead(t_offset_s=frame.t_offset_s, regions=tuple(regions))


@register_stage
class ScreentextStage(Stage):
    name = "screentext"
    modality = "video"
    slot = "ocr"
    stage_version = 1
    backend = Backend("ppocr", 1)
    needs = ("clipprep",)
    required = False          # L7: failure = hole + cancelled caption cone, healed later
    server = "ocr"
    # L5/L10 budget: version key + JSON overhead + the digest text. The text is capped at
    # OCR_CHARS_PER_SEC × span (360 chars at the 60 s design max), so 2048 bytes holds
    # even at ~4 UTF-8 bytes/char. Cost: ~6 chars per second of life.
    byte_budget = 2048

    async def run_async(self, ctx: StageContext) -> StageOutput:
        clip_frames: ClipFrames = ctx.inputs["clipprep"].bytes
        client = ctx.clients["ocr"]
        chunk_id = ctx.c1.get("chunk_id", "")

        reads: list[OcrRead] = []
        attempted = errors = 0
        for t in clip_frames.ocr_times:
            frame = _match_frame(clip_frames.frames, t)
            if frame is None:
                continue  # a selected time with no extracted frame — nothing to read
            attempted += 1
            try:
                if frame.jpeg_hi is None:
                    raise RuntimeError(
                        f"no hi-res frame at t+{frame.t_offset_s:.2f}s")
                result = await client.infer({
                    "input_b64": base64.b64encode(frame.jpeg_hi).decode("ascii"),
                    "codec": "image/jpeg",
                    "params": {},
                })
                reads.append(_regions_to_read(result, frame))
            except Exception as exc:  # noqa: BLE001 - per-frame errors are absorbed (v0 A-10)
                errors += 1
                logger.warning("OCR read failed for chunk %s at t+%.2fs: %s",
                               chunk_id, t, exc)

        # >50% of a chunk's OCR frames erroring RAISES: optional stage -> a HOLE and a
        # cancelled caption cone (v0 dead-lettered instead; v1 heals via redrive, L8).
        if attempted and errors / attempted > _MAX_ERROR_FRACTION:
            raise RuntimeError(
                f"OCR failed on {errors}/{attempted} frames (>50%) for chunk {chunk_id} — "
                "refusing to write a corpus with the majority of on-screen text lost"
            )

        text, n_redactions = assemble.render(
            reads, ctx.span_seconds,
            min_conf=MIN_CONF, min_chars=MIN_CHARS,
            dedup_ratio=DEDUP_RATIO, chars_per_second=OCR_CHARS_PER_SEC,
        )
        if n_redactions:
            logger.info("chunk %s: %d OCR redactions", chunk_id, n_redactions)

        # Ran -> the slot is ALWAYS emitted; "" is the honest empty claim (L11).
        return StageOutput(value={"value": text})
