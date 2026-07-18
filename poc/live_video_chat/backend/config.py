"""Single source of truth for the shared config constants (Contract B + Contract A).

The backend owns these and exposes the UI-relevant subset at GET /api/config so the
frontend (WS3) fetches them on load and there is exactly one place to change them.

Keep this module dependency-free so it can be imported from anywhere.
"""

from __future__ import annotations

import os

# --- Shared constants (see HANDOFF.md → CONTRACTS → Shared config constants) ----------

# Max length of a recorded clip. WS3 auto-stops at this; WS2 rejects longer (best-effort).
MAX_CLIP_SECONDS: float = 30.0

# Frame sampling rate handed to vLLM via mm_processor_kwargs. Informational to WS2.
TARGET_FPS: float = 2.0

# Container/mime the UI records (MP4/H.264 on iOS Safari) and the backend accepts.
VIDEO_MIME: str = "video/mp4"

# Static greeting shown by the UI on load.
GREETING: str = "\U0001f44b Hey — show me something and ask."

# Cap on generated answer length for the model call (Contract A `max_tokens`).
MAX_NEW_TOKENS: int = 512


# --- Pre-recorded clips (sanity-test mode: reason over a long, KNOWN video) ------------
# A small registry of server-side, already-normalized clips the UI can load instead of a
# freshly recorded phone clip. Each is downscaled + fps-sampled ahead of time and written
# under a path vLLM can read via file:// (must live under the server's allowed-media root,
# i.e. /mnt/localssd). The UI renders a "Load pre-recorded clip" affordance per entry.
# When a clip is loaded, /api/turn uses it directly (no upload, no re-normalization) and
# the user's (typed or ASR'd) question is the only thing that changes turn to turn — which
# is exactly the shape vLLM prefix-caching + mm-processor-cache reward (the big video
# prefix is re-used; only the new question is prefilled).
PRERECORDED_CLIPS: list = [
    {
        "id": "ishowspeed_miami_30m",
        "label": "IShowSpeed — Miami (0–30 min)",
        "description": "IRL tour, Day 1 Part 1. 30 min @ 480×288, 1 fps (~128K video tokens).",
        "path": os.environ.get(
            "PREREC_ISHOWSPEED_MIAMI",
            "/mnt/localssd/poc/live_video_chat/prerecorded/"
            "ishowspeed_miami_day01_p1_00-00_30-00_480x288_fps1.mp4",
        ),
        "duration_s": 1800,
        "fps": 1,
        "longest_side": 480,
    },
]


def _prerecorded_public() -> list:
    """UI-facing view of PRERECORDED_CLIPS (server `path` withheld)."""
    keys = ("id", "label", "description", "duration_s", "fps", "longest_side")
    return [{k: c.get(k) for k in keys} for c in PRERECORDED_CLIPS]


def prerecorded_by_id(clip_id: str):
    """Return the registry entry for `clip_id`, or None if unknown."""
    for c in PRERECORDED_CLIPS:
        if c.get("id") == clip_id:
            return c
    return None


# --- Model/serving presets (UI-selectable video operating points) ---------------------
# Each preset relaunches vLLM with a specific HF video-processor config (fps / max_frames /
# size) so we can compare how much video the model reasons over. The server ENVELOPE
# (max_model_len / max_num_batched_tokens) is held at the max for all presets, so switching
# only changes the processor kwargs. We drive frame count via `fps` (NOT num_frames) so
# Qwen's per-frame timestamp generation stays valid (num_frames path 500s on 0.19.1).
#   - Qwen defaults: max_frames=768, size.longest_edge=24576 tokens (auto-downscales).
#   - Raise max_frames to sample >768 frames; raise size.longest_edge to stop downscaling.
# Frame counts below assume the 30-min (1800 s) pre-recorded clip: frames ≈ fps × 1800.
# vLLM 0.19.1's memory profiler builds a worst-case video dummy ≈ 3 × (longest_edge/2048)
# tokens. It must stay under BOTH the 256K position limit AND max_num_batched_tokens, or the
# engine crashes/hangs at startup. So longest_edge ≤ ~105M (→ dummy ≈ 154K < 163840). That
# in turn caps REAL video at ~51K tokens — i.e. full 1800f @ 480×288 (121K) is NOT servable
# on this vLLM (needs the upgrade). Within ~51K you trade frames vs per-frame resolution.
_ENVELOPE = {"num_frames": 2048, "max_model_len": 200000, "max_num_batched_tokens": 163840}
_LE = 105_000_000  # highest longest_edge whose profiling dummy stays safely under the caps

def _mmk(fps, max_frames, longest_edge=None):
    if longest_edge is None:
        return '{"fps":%s,"max_frames":%d}' % (fps, max_frames)
    return ('{"fps":%s,"max_frames":%d,"size":{"longest_edge":%d,"shortest_edge":4096}}'
            % (fps, max_frames, longest_edge))

MODEL_PRESETS: list = [
    {
        "id": "dense_1800", "label": "1800f · 1fps · ~320×176",
        "description": "TRUE 1 fps over the full 30 min (1800 frames), reduced resolution. ~50K video tokens — the most temporal coverage vLLM 0.19.1 allows (full-res 1800f/121K needs the upgrade).",
        "est_video_tokens": 50000,
        "mm_processor_kwargs": _mmk("1.0", 1800, _LE),
        **_ENVELOPE,
    },
    {
        "id": "sharp_768", "label": "768f · 480×288 (sharp)",
        "description": "768 frames (~0.43 fps) at full 480×288 resolution. ~52K video tokens — same budget as dense, spent on resolution instead of frames. Good for reading signs/text.",
        "est_video_tokens": 52000,
        "mm_processor_kwargs": _mmk("1.0", 768, _LE),
        **_ENVELOPE,
    },
    {
        "id": "sweet_768", "label": "768f · 128×224 (Qwen default)",
        "description": "Qwen's default sweet spot: 768 frames auto-downscaled to the ~24.5K-token budget. ~15K video tokens.",
        "est_video_tokens": 15000,
        "mm_processor_kwargs": '{"fps":1.0}',
        **_ENVELOPE,
    },
]
DEFAULT_PRESET_ID: str = "dense_1800"

# Path to the vLLM launch script the reconfigure endpoint drives, and the file that records
# the currently-active preset id (so status survives a backend restart).
SERVE_SH: str = os.environ.get(
    "SERVE_SH",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server", "serve.sh"),
)
ACTIVE_PRESET_FILE: str = os.environ.get(
    "ACTIVE_PRESET_FILE", "/mnt/localssd/poc/live_video_chat/active_preset"
)


def _presets_public() -> list:
    keys = ("id", "label", "description", "est_video_tokens")
    return [{k: p.get(k) for k in keys} for p in MODEL_PRESETS]


def preset_by_id(pid: str):
    for p in MODEL_PRESETS:
        if p.get("id") == pid:
            return p
    return None


# --- Human feedback (per-turn thumbs up/down; saved for later fine-tuning) -------------
# Each rated turn appends one JSON line to FEEDBACK_FILE: {ts, turn_id, rating, clip_id,
# question, answer, model}. Per-turn only for now; a richer scheme comes with V1 multiturn.
FEEDBACK_DIR: str = os.environ.get(
    "FEEDBACK_DIR", "/mnt/localssd/poc/live_video_chat/feedback"
)
FEEDBACK_FILE: str = os.path.join(FEEDBACK_DIR, "turn_feedback.jsonl")


def public_config() -> dict:
    """The subset served at GET /api/config (what the UI needs)."""
    return {
        "max_clip_seconds": MAX_CLIP_SECONDS,
        "target_fps": TARGET_FPS,
        "video_mime": VIDEO_MIME,
        "greeting": GREETING,
        "max_new_tokens": MAX_NEW_TOKENS,
        # The model id (settings modal #7) and the px resolution actually fed to the
        # model (= the longest side the clip is normalized to before the video call).
        "model_id": MODEL_ID,
        "video_longest_side": NORMALIZE_LONGEST_SIDE,
        # Pre-recorded clips the UI can load (sanity-test mode).
        "prerecorded_clips": _prerecorded_public(),
        # Model/serving presets the UI can switch between (triggers a vLLM reload).
        "model_presets": _presets_public(),
        "default_preset_id": DEFAULT_PRESET_ID,
    }


# --- Contract A (vLLM) client settings ------------------------------------------------

# OpenAI-compatible endpoint of WS1's vLLM server (same node → 127.0.0.1).
VLLM_BASE_URL: str = os.environ.get("VLLM_BASE_URL", "http://127.0.0.1:8000")
VLLM_CHAT_COMPLETIONS_URL: str = VLLM_BASE_URL.rstrip("/") + "/v1/chat/completions"
# vLLM tokenize endpoint — used for the per-turn token breakdown (usage metrics #8).
VLLM_TOKENIZE_URL: str = VLLM_BASE_URL.rstrip("/") + "/tokenize"

# Model id served by WS1.
MODEL_ID: str = os.environ.get("MODEL_ID", "Qwen/Qwen3-VL-32B-Instruct")

# System prompt for the (stateless, length-1) turn.
SYSTEM_PROMPT: str = (
    "You are a helpful assistant. Answer in plain English about what you see."
)

# Default prompt when the user sent an empty text (voice-only / no transcript).
DEFAULT_PROMPT: str = "Describe what you see and answer any implicit question."

# fps/frame-count are set at vLLM launch (--mm-processor-kwargs fps=2.0 +
# --media-io-kwargs num_frames=60). We deliberately do NOT send per-request
# mm_processor_kwargs: vLLM ignores `extra_body` over the raw HTTP API, and its video
# path does NOT honor max_pixels anyway — so clip resolution is bounded by the ffmpeg
# normalization below instead. (Kept for reference / possible future use.)
MM_MAX_PIXELS: int = int(os.environ.get("MM_MAX_PIXELS", 262144))
MM_MIN_PIXELS: int = int(os.environ.get("MM_MIN_PIXELS", 131072))

# --- Clip normalization (ffmpeg, applied to every uploaded clip before the model call) -
# vLLM does NOT apply max_pixels to VIDEO, so a raw phone clip (e.g. 640x480 @ 60 frames)
# is ~9000 video tokens and exceeds the ~8192 encoder-cache budget -> HTTP 400. We
# re-encode each clip: downscale the longest side to NORMALIZE_LONGEST_SIDE px, drop audio
# (the model is video-only), and write a clean CFR + faststart H.264 mp4. 512px longest
# side -> ~190 tok/merged-unit -> ~6k tokens for a 30s clip — comfortably under 8192, and
# deterministic across any phone resolution/orientation. Also fixes the iOS rotation
# metadata and the MediaRecorder non-monotonic-DTS warning. ~0.3s for a 10s clip.
NORMALIZE_ENABLED: bool = os.environ.get("NORMALIZE_ENABLED", "1") not in ("0", "false", "False")
NORMALIZE_LONGEST_SIDE: int = int(os.environ.get("NORMALIZE_LONGEST_SIDE", 512))
NORMALIZE_FPS: int = int(os.environ.get("NORMALIZE_FPS", 15))

# Upstream request timeouts (seconds). Generous connect/read so first token can be slow,
# but bounded so a dead server doesn't hang the phone forever.
VLLM_CONNECT_TIMEOUT: float = float(os.environ.get("VLLM_CONNECT_TIMEOUT", 10.0))
# Read timeout must exceed the FIRST-turn prefill of a large clip: a 30-min / ~128K-token
# pre-recorded clip does a full ViT encode + prefill before the first token arrives (can be
# tens of seconds). 300s gives ample headroom; prefix-cached later turns are near-instant.
VLLM_READ_TIMEOUT: float = float(os.environ.get("VLLM_READ_TIMEOUT", 300.0))


# --- Backend file/runtime settings ----------------------------------------------------

# Where uploaded clips land. vLLM (WS1) reads this same path via file:// (shared node FS).
TURNS_DIR: str = os.environ.get(
    "TURNS_DIR", "/mnt/localssd/poc/live_video_chat/turns"
)

# Directory the UI is served from (WS3 builds it; may be empty while we build).
FRONTEND_DIR: str = os.environ.get(
    "FRONTEND_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend"),
)

# Opportunistic cleanup: keep at most this many recent clips in TURNS_DIR.
MAX_KEPT_CLIPS: int = int(os.environ.get("MAX_KEPT_CLIPS", 20))
