"""OCR seam runtime knobs — a LOCAL config shim, read fresh from the environment.

TEMPORARY HOME. Every field here is destined to migrate into WS-D's
``VisionSettings`` (``app/vision/config.py``), which this workstream does NOT own and
must not edit (a parallel session shares this repo; the shared-core config file has no
cross-session merge surface). So the ``VIDEO_OCR_*`` knobs live here and are read via
``os.getenv`` per call — exactly the posture ``app/audio/config.py`` uses for the audio
knobs it can't push into the shared ``Settings``. Each field is marked
``# TEMP -> VisionSettings (WS-D)`` so the migration is a mechanical lift-and-shift and
``select()`` / ``version_tag()`` keep working unchanged (they read ``cfg.ocr_backend``,
an attribute the future ``VisionSettings`` will carry too).

Defaults keep the loop headless: ``VIDEO_OCR_BACKEND=mock`` (no GPU, no network, no new
DP dependency), so the whole suite stays green with the OCR seam present but dormant.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger("data-processing.vision.ocr.config")


def _float(name: str, default: str) -> float:
    """Env float with a lenient-misconfig posture (matches ``app/vision/config``): a
    malformed value warns and falls back rather than 500-ing every video ingest (config
    is read per request, so a typo would otherwise be a permanent outage)."""
    raw = os.getenv(name, default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning("%s=%r is not a number — using default %s", name, raw, default)
        return float(default)


def _int(name: str, default: str) -> int:
    raw = os.getenv(name, default)
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning("%s=%r is not an integer — using default %s", name, raw, default)
        return int(default)


@dataclass(frozen=True)
class OcrConfig:
    """The resolved ``VIDEO_OCR_*`` view for one chunk. ``ocr_backend`` is the single
    field ``select()`` / ``version_tag()`` key off — kept name-compatible with the
    future ``VisionSettings.ocr_backend`` so the resolver survives the migration."""

    ocr_backend: str          # off | mock (default) | ppocr | vlm     # TEMP -> VisionSettings (WS-D)

    # ---- post-processing (D-07's second half — THIS workstream) --------------
    min_conf: float           # drop OCR boxes below this confidence     # TEMP -> VisionSettings (WS-D)
    min_chars: int            # drop rendered lines shorter than this     # TEMP -> VisionSettings (WS-D)
    dedup_ratio: float        # drop a line >= this similar to the prev   # TEMP -> VisionSettings (WS-D)
    ocr_chars_per_second: float  # budget: chars-per-second-of-life (D-11 ocr share)  # TEMP -> app/vision/budget.py (WS-D)

    # ---- event selection (D-07's FIRST half — WS-B's clipprep; carried here for a
    #      complete shim + so cfg_tag can hash them once WS-D lands) -----------
    max_events: int           # rank-free cap on OCR frames per chunk     # TEMP -> VisionSettings (WS-D)
    idle_peak: int            # <= this delta-peak => IDLE                 # TEMP -> VisionSettings (WS-D)
    layout_peak: int          # >  this delta-peak => LAYOUT              # TEMP -> VisionSettings (WS-D)
    floor_s: float            # floor-grid cadence (seconds)             # TEMP -> VisionSettings (WS-D)
    frame_width: int          # native OCR frame width (bbox normalization) # TEMP -> VisionSettings (WS-D)

    # ---- sidecar / vlm wire (OPERATIONAL_ONLY: urls/timeouts never fork the corpus,
    #      D-13; the model shas ARE output-affecting and are pinned + asserted) -
    url: str                  # co-located OCR sidecar base URL           # TEMP -> VisionSettings (WS-D)
    timeout: float            # per-request httpx timeout (seconds)       # TEMP -> VisionSettings (WS-D)
    model_sha_det: str        # pinned det model sha256, asserted @ /health  # TEMP -> VisionSettings (WS-D)
    model_sha_rec: str        # pinned rec model sha256, asserted @ /health  # TEMP -> VisionSettings (WS-D)
    ep: str                   # execution provider label (cpu default)    # TEMP -> VisionSettings (WS-D)
    model: str                # vlm-arm served model name ('' = none)     # TEMP -> VisionSettings (WS-D)
    api_key: str              # vlm-arm bearer token ('' = none)          # TEMP -> VisionSettings (WS-D)
    max_tokens: int           # vlm-arm decode cap                        # TEMP -> VisionSettings (WS-D)


def get_ocr_config() -> OcrConfig:
    """Read the ``VIDEO_OCR_*`` environment fresh (per chunk), like the audio/vision
    configs — a test flips a knob without re-importing and the value is resolved at
    graph-build/run time."""
    return OcrConfig(
        ocr_backend=(os.getenv("VIDEO_OCR_BACKEND", "mock") or "mock").strip().lower(),
        min_conf=_float("VIDEO_OCR_MIN_CONF", "0.60"),
        min_chars=_int("VIDEO_OCR_MIN_CHARS", "4"),
        dedup_ratio=_float("VIDEO_OCR_DEDUP_RATIO", "0.92"),
        # D-11: the OCR char rate is DERIVED (total − caption share), NOT an independent knob.
        # Reconciled at WS-D integration to read the SAME canonical knobs VisionSettings carries
        # (chars_per_second / caption_chars_share, both OUTPUT_AFFECTING) instead of a standalone
        # VIDEO_OCR_CHARS_PER_SECOND — which, living only on OcrConfig, was invisible to cfg_tag
        # and could rewrite the OCR record's bytes under an unchanged record_id (a silent
        # /context overwrite, the exact class D-13 closes). Defaults 22−16=6 preserve behaviour;
        # matches budget._ocr_rate. (Follow-up L-2: collapse assemble.ocr_cap to import
        # app.vision.budget.ocr_cap(span, vs) once screentext threads vs — this file's own TODO.)
        ocr_chars_per_second=max(
            0.0, _float("VIDEO_CHARS_PER_SECOND", "22") - _float("VIDEO_CAPTION_CHARS_SHARE", "16")
        ),
        max_events=_int("VIDEO_OCR_MAX_EVENTS", "3"),
        idle_peak=_int("VIDEO_OCR_IDLE_PEAK", "8"),
        layout_peak=_int("VIDEO_OCR_LAYOUT_PEAK", "40"),
        floor_s=_float("VIDEO_OCR_FLOOR_S", "120"),
        frame_width=_int("VIDEO_OCR_FRAME_WIDTH", "1728"),
        # Default matches the sidecar's frozen OCR_PORT=8091 (sidecars/ocr/README.md).
        url=os.getenv("VIDEO_OCR_URL", "http://127.0.0.1:8091").rstrip("/"),
        timeout=_float("VIDEO_OCR_TIMEOUT", "15"),
        model_sha_det=os.getenv("VIDEO_OCR_MODEL_SHA_DET", "").strip(),
        model_sha_rec=os.getenv("VIDEO_OCR_MODEL_SHA_REC", "").strip(),
        ep=(os.getenv("VIDEO_OCR_EP", "cpu") or "cpu").strip().lower(),
        model=os.getenv("VIDEO_OCR_MODEL", "").strip(),
        api_key=os.getenv("VIDEO_OCR_API_KEY", ""),
        max_tokens=_int("VIDEO_OCR_MAX_TOKENS", "900"),
    )
