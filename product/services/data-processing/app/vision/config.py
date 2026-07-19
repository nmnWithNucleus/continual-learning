"""VIDEO_* runtime configuration, read fresh from the environment.

The video pipeline lives entirely behind the modality seam (a disjoint plugin +
this new namespace), so its knobs are read here via ``os.getenv`` rather than
added to the shared ``app.config.Settings`` — keeping the shared-core config file
untouched (no cross-session merge surface) while still reading env per call, the
same discipline ``app.config`` uses.

Defaults keep the loop headless: ``VIDEO_BACKEND=mock`` (no GPU, no network),
scene-change keyframe selection with sane caps, OCR woven into the caption (D8)
but no extra ``ocr`` records unless explicitly asked for.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _as_bool(value: str) -> bool:
    return value.strip().lower() not in ("0", "false", "no", "off", "")


@dataclass(frozen=True)
class VisionSettings:
    backend: str              # "mock" (default, no GPU/net) | "vlm" (real captioner)

    # ---- keyframe selection (frame extraction, backend-independent) ----------
    # A duration-driven UNIFORM base grid (every ``keyframe_interval_s``) guarantees
    # coverage; SCENE-CHANGE cuts over ``scene_threshold`` refine it; the union is
    # deduped, floored to ``min_keyframes`` and capped at ``max_keyframes``. Base
    # grid keeps selection deterministic + content-robust (scene scores vary wildly
    # by encoding); scene cuts add keyframes exactly at visual transitions.
    scene_threshold: float    # ffmpeg scene-change score in (0,1] a cut must beat
    keyframe_interval_s: float  # uniform base-grid cadence (seconds/keyframe)
    max_keyframes: int        # hard cap on keyframes per chunk (cost/latency guard)
    min_keyframes: int        # absolute floor on keyframes per chunk
    sample_fps: float         # cadence the ffmpeg scene detector samples the blob at
    frame_max_width: int      # downscale extracted JPEGs to <= this width (VLM cost)

    # ---- vlm captioner backend (only read when backend == "vlm") -------------
    vlm_url: str              # OpenAI-compatible base URL (…/v1/chat/completions)
    vlm_model: str            # served-model-name to request
    vlm_api_key: str          # bearer token if the endpoint wants one ('' = none)
    vlm_timeout: float        # per-request httpx timeout (seconds)
    vlm_max_tokens: int       # caption length cap per keyframe

    # ---- OCR (D8): woven into the caption; optional separate 'ocr' records ----
    ocr_records: bool         # also emit a content.kind='ocr' unit per keyframe


def _backend() -> str:
    return os.getenv("VIDEO_BACKEND", "mock").strip().lower()


def get_vision_settings() -> VisionSettings:
    return VisionSettings(
        backend=_backend(),
        scene_threshold=float(os.getenv("VIDEO_SCENE_THRESHOLD", "0.30")),
        keyframe_interval_s=float(os.getenv("VIDEO_KEYFRAME_INTERVAL_S", "3.0")),
        max_keyframes=int(os.getenv("VIDEO_MAX_KEYFRAMES", "8")),
        min_keyframes=int(os.getenv("VIDEO_MIN_KEYFRAMES", "1")),
        sample_fps=float(os.getenv("VIDEO_SAMPLE_FPS", "2.0")),
        frame_max_width=int(os.getenv("VIDEO_FRAME_MAX_WIDTH", "768")),
        vlm_url=os.getenv("VIDEO_VLM_URL", "http://127.0.0.1:8000").rstrip("/"),
        vlm_model=os.getenv("VIDEO_VLM_MODEL", "Qwen/Qwen3-VL-32B-Instruct"),
        vlm_api_key=os.getenv("VIDEO_VLM_API_KEY", ""),
        vlm_timeout=float(os.getenv("VIDEO_VLM_TIMEOUT", "120")),
        vlm_max_tokens=int(os.getenv("VIDEO_VLM_MAX_TOKENS", "256")),
        ocr_records=_as_bool(os.getenv("VIDEO_OCR_RECORDS", "0")),
    )
