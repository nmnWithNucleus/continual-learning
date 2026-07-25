"""VIDEO_* runtime configuration, read fresh from the environment.

The video pipeline lives entirely behind the modality seam (a disjoint plugin +
this new namespace), so its knobs are read here via ``os.getenv`` rather than
added to the shared ``app.config.Settings`` — keeping the shared-core config file
untouched (no cross-session merge surface) while still reading env per call, the
same discipline ``app.config`` uses. A malformed numeric degrades to its default
with a WARN (``_float`` / ``_int``) rather than 500-ing every video ingest, because
config is read per request and a fleet-wide typo would otherwise be a permanent
outage (design §8-12).

**Two pipelines share this one settings object.** The retained ``keyframe`` legacy
path (WS-G) reads ``scene_threshold`` / ``keyframe_interval_s`` / ``max_keyframes``
/ ``min_keyframes`` / ``sample_fps`` / ``frame_max_width`` / ``vlm_max_tokens`` /
``ocr_records``; the new ``clip`` screen path (WS-VC) reads the ``clip_*`` /
``ocr_*`` / ``scenario`` knobs below. Both read ``backend`` / ``vlm_model`` /
``vlm_url`` / ``vlm_api_key`` / ``vlm_timeout``. Adding a field here is not free:
``app/vision/version.py`` classifies EVERY field as ``OUTPUT_AFFECTING`` (folded
into the clip dialect ``cfg_tag``) or ``OPERATIONAL_ONLY``, and a completeness test
FAILS until a new field is classified — see D-13.

Defaults keep the loop headless: ``VIDEO_BACKEND=mock`` / ``VIDEO_OCR_BACKEND=mock``
(no GPU, no network), and ``VIDEO_PIPELINE=keyframe`` (the shipped legacy path), so
the whole suite stays green with the clip stages dormant.
"""
from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from .mode import resolve_pipeline

logger = logging.getLogger("data-processing.vision.config")


def _as_bool(value: str) -> bool:
    return value.strip().lower() not in ("0", "false", "no", "off", "")


def _float(name: str, default: str) -> float:
    """Env float with a lenient-misconfig posture (matches ``app/audio/config``):
    a malformed value logs a warning and falls back to the default, instead of a
    bare ValueError 500-ing EVERY video ingest until the operator notices (config
    is read per request — a typo would otherwise be a permanent outage)."""
    raw = os.getenv(name, default)
    try:
        return float(raw)
    except ValueError:
        logger.warning("%s=%r is not a number — using default %s", name, raw, default)
        return float(default)


def _int(name: str, default: str) -> int:
    raw = os.getenv(name, default)
    try:
        return int(raw)
    except ValueError:
        logger.warning("%s=%r is not an integer — using default %s", name, raw, default)
        return int(default)


def _norm(name: str, default: str) -> str:
    """Lower/strip an env string with a non-empty fallback (mirrors audio/config)."""
    return (os.getenv(name, default) or "").strip().lower() or default


def _clamp_int(name: str, default: str, *, lo: int, hi: int, warn_over: int | None = None) -> int:
    """An int knob clamped to ``[lo, hi]``. ``warn_over`` (e.g. clip_frame_width's
    1024) logs a cost WARN when the raw value exceeds it before clamping — A-16: two
    of the design's cost blowups are one keystroke away, so the price is logged where
    the dial is turned."""
    v = _int(name, default)
    if warn_over is not None and v > warn_over:
        logger.warning(
            "%s=%d exceeds the recommended ceiling %d — clamping to %d (each step up "
            "multiplies vision tokens on every call; see design A-16)", name, v, warn_over, hi,
        )
    return max(lo, min(v, hi))


@dataclass(frozen=True)
class VisionSettings:
    # =====================================================================
    # Pipeline selector (D-14). ``resolve_pipeline()`` maps VIDEO_PIPELINE →
    # "keyframe" (default/unknown, the safe legacy path) | "clip".
    # =====================================================================
    pipeline: str

    # =====================================================================
    # Captioner backend, shared by BOTH pipelines.
    #   legacy:  mock | vlm         clip: mock | vlm | vertex
    # =====================================================================
    backend: str
    vlm_model: str            # served-model-name to request
    vlm_url: str              # OpenAI-compatible base URL (…/v1/chat/completions)
    vlm_api_key: str          # bearer token if the endpoint wants one ('' = none)
    vlm_timeout: float        # per-request httpx timeout (seconds)

    # =====================================================================
    # LEGACY keyframe path (WS-G). A duration-driven UNIFORM base grid refined by
    # SCENE-CHANGE cuts; the legacy dialect is a FROZEN "" exemption (D-14), so none
    # of these fork any corpus — they are OPERATIONAL_ONLY w.r.t. every live dialect.
    # =====================================================================
    scene_threshold: float    # ffmpeg scene-change score in (0,1] a cut must beat
    keyframe_interval_s: float  # uniform base-grid cadence (seconds/keyframe)
    max_keyframes: int        # hard cap on keyframes per chunk (cost/latency guard)
    min_keyframes: int        # absolute floor on keyframes per chunk
    sample_fps: float         # cadence the ffmpeg scene detector samples the blob at
    frame_max_width: int      # downscale extracted JPEGs to <= this width (VLM cost)
    vlm_max_tokens: int       # legacy caption length cap per keyframe
    ocr_records: bool         # legacy: also emit a content.kind='ocr' unit per keyframe

    # =====================================================================
    # CLIP path — captioner sampling & payload (D-03, D-11). OUTPUT_AFFECTING.
    # =====================================================================
    clip_seconds_per_frame: float  # K = clamp(ceil(span/this), min, max)
    clip_max_frames: int           # hard prefill ceiling (<= server --limit-mm-per-prompt)
    clip_min_frames: int           # floor; with one frame "what changed" is unanswerable
    clip_frame_width: int          # captioner JPEG width; 768=360 Qwen tok, clamped 1024 (A-16)
    clip_max_tokens: int           # decode cap for the clip caption
    chars_per_second: int          # total per-second-of-life char budget (D-11) — a CORRECTNESS knob
    caption_chars_share: int       # of chars_per_second, the caption's share (rest -> ocr)

    # =====================================================================
    # CLIP path — analysis / delta gate (D-04, D-07). OUTPUT_AFFECTING.
    # =====================================================================
    analysis_period_s: float  # delta-probe period (Pass-A select cadence)
    pixel_threshold: int      # lutyuv binarize threshold (val > this -> 255)
    grid: int                 # NxN area-average change map (32 -> the pinned 2/255 floor)
    idle_peak: int            # class=IDLE when peak <= this
    layout_peak: int          # class=LAYOUT when peak > this
    layout_spread: int        # class=LAYOUT when spread > this

    # =====================================================================
    # CLIP path — OCR specialist (D-06, D-07, D-08). OUTPUT_AFFECTING.
    # =====================================================================
    ocr_backend: str          # off | mock (headless default) | ppocr | vlm
    ocr_ep: str               # ONNX execution provider (cpu) — in the tag, not bit-exact across EPs
    ocr_model_sha_det: str    # pinned det model sha256 ('' = unpinned; asserted vs /health)
    ocr_model_sha_rec: str    # pinned rec model sha256 ('' = unpinned)
    ocr_frame_width: int      # OCR reads native res (1728 = mac capture cap, no resample)
    ocr_min_conf: float       # drop boxes below this confidence
    ocr_min_chars: int        # drop lines shorter than this
    ocr_dedup_ratio: float    # drop an event >= this similar to the prev kept (chunk-local)
    ocr_floor_s: float        # floor-grid cadence: one guaranteed read every this many seconds
    ocr_max_events: int       # rank-free even-spaced cap on OCR reads per chunk (A-16 cost dial)
    ocr_max_tokens: int       # decode cap for the vlm OCR arm
    ocr_model: str            # vlm-OCR-arm served-model name ('' = none) — OUTPUT_AFFECTING like vlm_model
    ocr_stamp: str            # rel (relative +Ns offsets, default) | utc (A-6, forks the dialect)
    privacy_filter: bool      # deterministic secret redaction (D-07 step 4) — an access control

    # =====================================================================
    # CLIP path — prompt selection (D-13). OUTPUT_AFFECTING.
    # =====================================================================
    scenario: str             # screen-mac (default) | screen-browser | screen-generic | camera
    prompt_dir_fingerprint: str  # '' unless VIDEO_PROMPT_DIR overrides the packaged pack

    # =====================================================================
    # OPERATIONAL — wire/host/thread knobs that CANNOT change a clip record's bytes
    # (moving an endpoint with the same served model is a no-op; forking on a DNS
    # change would be a self-inflicted double-count — D-13). OPERATIONAL_ONLY.
    # =====================================================================
    ocr_url: str              # loopback OCR sidecar base URL
    ocr_api_key: str          # vlm-OCR-arm bearer token ('' = none) — a credential, like vlm_api_key
    ocr_timeout: float        # per-request OCR sidecar timeout (seconds)
    ocr_threads: int          # PP-OCR CPU thread count
    structured_mode: str      # guided-decoding: auto (probe) | on | off
    max_prompt_images_check: int  # assert K <= this (the server --limit-mm-per-prompt bound)


# --- prompt-source fingerprint: computed ONCE at import, never re-stat'd --------------
# D-13 TOCTOU note: packs load once per process and are never re-stat'd. A VIDEO_PROMPT_DIR
# override is a dev/experiment escape hatch (production bakes prompts into the image); when
# set we fingerprint the dir ONCE at import so the override is visible in the clip dialect
# (cfg_tag) without re-reading the filesystem per request. Unset (the default and prod) -> "",
# so a packaged-prompt edit forks ONLY the vlm dialect via prompt_tag, never the mock corpus.
def prompt_dir_fingerprint(directory: str | None) -> str:
    """sha8 over the sorted (relpath, bytes) of a prompt override dir's ``*.md`` /
    ``*.json`` files, or ``""`` when unset / not a directory. Pure — exposed for tests;
    ``get_vision_settings`` uses the import-time snapshot below."""
    d = (directory or "").strip()
    if not d:
        return ""
    p = Path(d)
    if not p.is_dir():
        return ""
    h = hashlib.sha256()
    for f in sorted(p.rglob("*")):
        if f.is_file() and f.suffix in (".md", ".json"):
            h.update(f.relative_to(p).as_posix().encode("utf-8"))
            h.update(b"\0")
            h.update(f.read_bytes())
            h.update(b"\0")
    return h.hexdigest()[:8]


_PROMPT_DIR_FINGERPRINT = prompt_dir_fingerprint(os.getenv("VIDEO_PROMPT_DIR"))


def get_vision_settings() -> VisionSettings:
    return VisionSettings(
        pipeline=resolve_pipeline(),
        # --- captioner backend (shared) ---
        backend=_norm("VIDEO_BACKEND", "mock"),
        vlm_model=os.getenv("VIDEO_VLM_MODEL", "Qwen/Qwen3-VL-32B-Instruct"),
        vlm_url=os.getenv("VIDEO_VLM_URL", "http://127.0.0.1:8000").rstrip("/"),
        vlm_api_key=os.getenv("VIDEO_VLM_API_KEY", ""),
        vlm_timeout=_float("VIDEO_VLM_TIMEOUT", "120"),
        # --- legacy keyframe path ---
        scene_threshold=_float("VIDEO_SCENE_THRESHOLD", "0.30"),
        keyframe_interval_s=_float("VIDEO_KEYFRAME_INTERVAL_S", "3.0"),
        max_keyframes=_int("VIDEO_MAX_KEYFRAMES", "8"),
        min_keyframes=_int("VIDEO_MIN_KEYFRAMES", "1"),
        sample_fps=_float("VIDEO_SAMPLE_FPS", "2.0"),
        frame_max_width=_int("VIDEO_FRAME_MAX_WIDTH", "768"),
        vlm_max_tokens=_int("VIDEO_VLM_MAX_TOKENS", "256"),
        ocr_records=_as_bool(os.getenv("VIDEO_OCR_RECORDS", "0")),
        # --- clip captioner sampling & budget ---
        clip_seconds_per_frame=_float("VIDEO_CLIP_SECONDS_PER_FRAME", "2.5"),
        clip_max_frames=_int("VIDEO_CLIP_MAX_FRAMES", "12"),
        clip_min_frames=_int("VIDEO_CLIP_MIN_FRAMES", "2"),
        clip_frame_width=_clamp_int("VIDEO_CLIP_FRAME_WIDTH", "768", lo=16, hi=1024, warn_over=1024),
        clip_max_tokens=_int("VIDEO_CLIP_MAX_TOKENS", "512"),
        chars_per_second=_int("VIDEO_CHARS_PER_SECOND", "22"),
        caption_chars_share=_int("VIDEO_CAPTION_CHARS_SHARE", "16"),
        # --- clip analysis / delta gate ---
        analysis_period_s=_float("VIDEO_ANALYSIS_PERIOD_S", "2.0"),
        pixel_threshold=_int("VIDEO_PIXEL_THRESHOLD", "24"),
        grid=_int("VIDEO_DELTA_GRID", "32"),
        idle_peak=_int("VIDEO_OCR_IDLE_PEAK", "8"),
        layout_peak=_int("VIDEO_OCR_LAYOUT_PEAK", "40"),
        layout_spread=_int("VIDEO_OCR_LAYOUT_SPREAD", "32"),
        # --- clip OCR specialist ---
        ocr_backend=_norm("VIDEO_OCR_BACKEND", "mock"),
        ocr_ep=_norm("VIDEO_OCR_EP", "cpu"),
        ocr_model_sha_det=os.getenv("VIDEO_OCR_MODEL_SHA_DET", "").strip(),
        ocr_model_sha_rec=os.getenv("VIDEO_OCR_MODEL_SHA_REC", "").strip(),
        ocr_frame_width=_int("VIDEO_OCR_FRAME_WIDTH", "1728"),
        ocr_min_conf=_float("VIDEO_OCR_MIN_CONF", "0.60"),
        ocr_min_chars=_int("VIDEO_OCR_MIN_CHARS", "4"),
        ocr_dedup_ratio=_float("VIDEO_OCR_DEDUP_RATIO", "0.92"),
        ocr_floor_s=_float("VIDEO_OCR_FLOOR_S", "120"),
        ocr_max_events=_int("VIDEO_OCR_MAX_EVENTS", "3"),
        ocr_max_tokens=_int("VIDEO_OCR_MAX_TOKENS", "900"),
        ocr_model=os.getenv("VIDEO_OCR_MODEL", "").strip(),
        ocr_stamp=_norm("VIDEO_OCR_STAMP", "rel"),
        privacy_filter=_as_bool(os.getenv("VIDEO_PRIVACY_FILTER", "1")),
        # --- clip prompt selection ---
        scenario=_norm("VIDEO_SCENARIO", "screen-mac"),
        prompt_dir_fingerprint=_PROMPT_DIR_FINGERPRINT,
        # --- operational (never clip-output-affecting) ---
        ocr_url=os.getenv("VIDEO_OCR_URL", "http://127.0.0.1:8091").rstrip("/"),
        ocr_api_key=os.getenv("VIDEO_OCR_API_KEY", ""),
        ocr_timeout=_float("VIDEO_OCR_TIMEOUT", "15"),
        ocr_threads=_int("VIDEO_OCR_THREADS", "4"),
        structured_mode=_norm("VIDEO_VLM_STRUCTURED", "auto"),
        max_prompt_images_check=_int("VIDEO_MAX_PROMPT_IMAGES_CHECK", "16"),
    )
