"""WS2 — FastAPI hub for the live_video_chat V0 POC.

Implements Contract B (UI <-> backend):
  GET  /              -> serve the single-page UI (frontend/index.html + assets)
  GET  /api/config    -> shared constants the UI reads on load
  POST /api/transcribe-> multipart `audio` -> {"text": ...}  (via WS4's transcribe())
  POST /api/turn      -> multipart `video` + `text` -> STREAMED plain-text answer

It is the client of Contract A (vLLM) via model_client, and consumes WS4's ASR module.
Stateless / single-turn: every /api/turn is independent, no history.
"""

from __future__ import annotations

import os
import json
import time
import uuid
import asyncio
import subprocess
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, File, Form, UploadFile, Request
from fastapi.responses import JSONResponse, StreamingResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.concurrency import run_in_threadpool

import config
import model_client

# --- ASR (WS4): import the real module. asr.py now exists in backend/, so this resolves.
#     We still guard the import so a missing optional dep degrades to an empty-text stub
#     (the UI then falls back to the editable text box) instead of crashing startup.
try:
    import asr  # WS4's backend/asr.py: transcribe(bytes, mime)->str, warmup()
    from asr import transcribe
    _ASR_AVAILABLE = True
except Exception:  # pragma: no cover - only if faster-whisper/ctranslate2 are absent
    asr = None  # type: ignore
    _ASR_AVAILABLE = False

    def transcribe(audio_bytes, mime=None):  # type: ignore
        return ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm the ASR model at startup so the first real /api/transcribe doesn't pay the
    # one-time model-load (~2-10s) + VAD-prime cost. Best-effort, off the event loop.
    if _ASR_AVAILABLE and asr is not None:
        try:
            await run_in_threadpool(asr.warmup)
        except Exception:
            pass  # never let warmup block the server from coming up
    yield


app = FastAPI(title="live_video_chat backend (WS2)", lifespan=lifespan)

os.makedirs(config.TURNS_DIR, exist_ok=True)


# --- /api/config ----------------------------------------------------------------------
@app.get("/api/config")
async def api_config():
    return JSONResponse(config.public_config())


# --- /api/transcribe ------------------------------------------------------------------
@app.post("/api/transcribe")
async def api_transcribe(audio: UploadFile = File(...)):
    """Audio blob -> {"text": ..., "asr_ms": <int>} via WS4's transcribe().

    `asr_ms` is the wall-time around transcribe() (decode + inference). Empty text if
    ASR is unavailable or errors; the UI degrades to the editable text box.
    """
    audio_bytes = await audio.read()
    mime = audio.content_type or None
    t0 = time.monotonic()
    try:
        # transcribe() is sync + blocking (ffmpeg + faster-whisper); run it off the
        # event loop so it doesn't stall concurrent turns. It is thread-safe (WS4).
        text = await run_in_threadpool(transcribe, audio_bytes, mime)
    except Exception as exc:
        # Never 500 on ASR hiccups — the UI degrades to the editable text box.
        asr_ms = int((time.monotonic() - t0) * 1000)
        return JSONResponse({"text": "", "asr_ms": asr_ms, "error": f"{type(exc).__name__}: {exc}"})
    asr_ms = int((time.monotonic() - t0) * 1000)
    return JSONResponse({"text": text or "", "asr_ms": asr_ms})


# --- helpers for /api/turn ------------------------------------------------------------
def _probe_duration_seconds(path: str):
    """Best-effort clip duration via ffprobe. Returns float or None if unknown."""
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True, text=True, timeout=10,
        )
        val = out.stdout.strip()
        return float(val) if val else None
    except Exception:
        return None


def _normalize_clip(src: str, dst: str) -> bool:
    """Re-encode an uploaded clip before the model call: downscale the longest side to
    config.NORMALIZE_LONGEST_SIDE px, drop audio (video-only model), apply rotation, and
    write a clean CFR + faststart H.264 mp4. This bounds the Qwen3-VL video-token count so
    real phone clips (e.g. 640x480 -> ~9000 tokens) stay under vLLM's ~8192 encoder cache
    regardless of capture resolution/orientation. Returns True on success; False -> the
    caller falls back to the original clip.
    """
    side = config.NORMALIZE_LONGEST_SIDE
    try:
        res = subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error", "-i", src,
                "-vf", f"scale={side}:{side}:force_original_aspect_ratio=decrease:force_divisible_by=2",
                "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                "-r", str(config.NORMALIZE_FPS), "-an", "-movflags", "+faststart",
                dst,
            ],
            capture_output=True, text=True, timeout=60,
        )
        return res.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 0
    except Exception:
        return False


def _cleanup_old_clips(keep: int) -> None:
    """Opportunistically delete all but the `keep` most recent clips in TURNS_DIR."""
    try:
        entries = [
            os.path.join(config.TURNS_DIR, f)
            for f in os.listdir(config.TURNS_DIR)
            if f.startswith("turn_") and f.endswith(".mp4")
        ]
        entries.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        for stale in entries[keep:]:
            try:
                os.remove(stale)
            except OSError:
                pass
    except Exception:
        pass


# RECORD SEPARATOR (U+001E): never appears in normal answer text, so the frontend splits
# the streamed body on it — everything before is the answer, everything after is the
# compact per-turn metrics JSON. Body shape: `<answer text>\x1e<json>`.
METRICS_SEP = "\x1e"


# --- /api/turn ------------------------------------------------------------------------
@app.post("/api/turn")
async def api_turn(
    request: Request,
    video: Optional[UploadFile] = File(None),
    text: str = Form(""),
    clip_id: str = Form(""),
):
    """Stream the model's answer back (text/plain), then a `\\x1e<metrics-json>` tail.

    Video source precedence (video is OPTIONAL):
      - `clip_id` set + known -> PRE-RECORDED clip mode: use the server-side, already
        normalized clip at that id; any uploaded `video` is ignored. The user's text is
        the question. (Same big video prefix every turn -> vLLM prefix/mm caches reward it.)
      - else `video` present   -> save -> normalize -> video+text request.
      - else text only         -> a TEXT-ONLY chat request (no video part).
      - else (nothing)         -> a streamed `[error] ...`.

    Validation errors are returned as a streamed `[error] ...` body (status 200) so the
    UI's single getReader() path handles success and failure uniformly. On an `[error]`
    we skip the metrics tail.
    """
    def err_stream(msg: str):
        async def gen():
            yield f"[error] {msg}"
        return StreamingResponse(gen(), media_type="text/plain; charset=utf-8")

    turn_id = uuid.uuid4().hex             # stable id for metrics + feedback join
    clip_ref = (clip_id or "").strip()     # pre-recorded clip id (Contract B addition)
    prerec = config.prerecorded_by_id(clip_ref) if clip_ref else None
    has_video = video is not None and bool(getattr(video, "filename", None))
    text_clean = (text or "").strip()

    send_path: Optional[str] = None        # None -> text-only turn
    normalize_ms = 0
    clip_meta = ""                         # video source label (for metrics + feedback)

    if prerec is not None:
        # --- Pre-recorded clip mode ------------------------------------------------
        path = prerec.get("path") or ""
        if not path or not os.path.exists(path):
            return err_stream(f"pre-recorded clip missing on server: {prerec.get('id')}")
        send_path = path
        clip_meta = prerec.get("id") or clip_ref
        # NB: any uploaded `video` is intentionally ignored here (pre-recorded wins).

    elif has_video:
        # --- Recorded-clip mode ----------------------------------------------------
        clip_path = os.path.join(config.TURNS_DIR, f"turn_{turn_id}.mp4")
        size = 0
        try:
            with open(clip_path, "wb") as f:
                while True:
                    chunk = await video.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    f.write(chunk)
        except Exception as exc:
            return err_stream(f"failed to save clip: {type(exc).__name__}: {exc}")

        if size == 0:
            try:
                os.remove(clip_path)
            except OSError:
                pass
            # An empty file upload but real text -> treat as text-only rather than error.
            if not text_clean:
                return err_stream("empty video upload")
            has_video = False

        if has_video:
            # Best-effort length check: reject clips longer than max_clip_seconds.
            duration = _probe_duration_seconds(clip_path)
            if duration is not None and duration > config.MAX_CLIP_SECONDS + 0.5:
                try:
                    os.remove(clip_path)
                except OSError:
                    pass
                return err_stream(
                    f"clip too long: {duration:.1f}s > {config.MAX_CLIP_SECONDS:.0f}s max"
                )

            # Normalize (downscale longest side, drop audio, fix rotation/CFR/faststart)
            # so the token count fits the encoder cache for ANY phone resolution. Fall
            # back to the raw clip if ffmpeg fails.
            send_path = clip_path
            clip_meta = "recorded"
            if config.NORMALIZE_ENABLED:
                norm_path = os.path.join(config.TURNS_DIR, f"turn_{turn_id}_norm.mp4")
                _n0 = time.monotonic()
                ok = await run_in_threadpool(_normalize_clip, clip_path, norm_path)
                normalize_ms = int((time.monotonic() - _n0) * 1000)
                if ok:
                    send_path = norm_path

            _cleanup_old_clips(config.MAX_KEPT_CLIPS)

    # Nothing to send at all (no clip resolved AND no text).
    if send_path is None and not text_clean:
        return err_stream("nothing to send (record/load a clip or type/speak a question)")

    # Determine the prompt: prefer the user's text; else a sensible default (video turns
    # may legitimately have no text — describe-what-you-see).
    prompt = text_clean or config.DEFAULT_PROMPT

    # Relay the upstream token stream straight to the client, then append the metrics tail.
    usage_sink: dict = {}

    async def answer_gen():
        errored = False
        async for piece in model_client.stream_answer(send_path, prompt, usage_sink):
            if piece.lstrip().startswith("[error]") or "\n[error]" in piece:
                errored = True
            yield piece
        # On an [error], skip the metrics frame (Contract: tail is optional on error).
        if errored:
            return
        # Best-effort metrics frame. Any sub-step failure -> still emit a frame.
        try:
            metrics = await _build_metrics(
                prompt, send_path, normalize_ms, usage_sink, turn_id, clip_meta
            )
            yield METRICS_SEP + json.dumps(metrics, separators=(",", ":"))
        except Exception:
            # Last resort: emit a minimal valid frame so the split never yields garbage.
            try:
                yield METRICS_SEP + json.dumps(
                    {"tokens": {}, "timing_ms": {}, "model": config.MODEL_ID,
                     "turn_id": turn_id, "clip_id": clip_meta},
                    separators=(",", ":"),
                )
            except Exception:
                pass

    headers = {
        # Defeat proxy/browser buffering so chunks arrive incrementally.
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(
        answer_gen(),
        media_type="text/plain; charset=utf-8",
        headers=headers,
    )


async def _build_metrics(
    prompt: str,
    send_path: Optional[str],
    normalize_ms: int,
    usage_sink: dict,
    turn_id: str = "",
    clip_meta: str = "",
) -> dict:
    """Assemble the per-turn metrics object (usage metrics #8).

    Combines the streamed usage (prompt_total/output, ttft/inference timings) with a
    /tokenize-derived token breakdown (system/text/video). Also carries `turn_id` and
    `clip_id` so the UI can attach thumbs up/down feedback to this exact turn. Robust: any
    missing piece is 0 / best-effort; never raises out (caller falls back to a minimal frame).
    """
    prompt_total = usage_sink.get("prompt_total")
    output = int(usage_sink.get("output") or 0)
    breakdown = await model_client.token_breakdown(prompt, prompt_total)
    # video tokens are only meaningful when a clip was actually sent.
    video_tokens = int(breakdown.get("video") or 0) if send_path else 0
    return {
        "tokens": {
            "system": int(breakdown.get("system") or 0),
            "text": int(breakdown.get("text") or 0),
            "video": video_tokens,
            "prompt_total": int(prompt_total or 0),
            "output": output,
        },
        "timing_ms": {
            "normalize": int(normalize_ms),
            "ttft": int(usage_sink.get("ttft_ms") or 0),
            "inference_total": int(usage_sink.get("inference_ms") or 0),
        },
        "model": config.MODEL_ID,
        "turn_id": turn_id,
        "clip_id": clip_meta,
        # Which serving preset produced this answer (for cross-config comparison).
        "preset": _model_state.get("preset_id"),
    }


# --- /api/feedback --------------------------------------------------------------------
def _append_jsonl(path: str, obj: dict) -> None:
    """Append one JSON object as a line to `path` (creating the dir/file if needed)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


@app.post("/api/feedback")
async def api_feedback(request: Request):
    """Persist a per-turn thumbs up/down rating for later fine-tuning/eval.

    Body (JSON): {turn_id, rating: "up"|"down", clip_id?, question?, answer?}. Appends one
    line to FEEDBACK_FILE with a server timestamp. Best-effort store; validation errors 400.
    """
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid JSON body"}, status_code=400)

    rating = str((data or {}).get("rating") or "").strip().lower()
    if rating not in ("up", "down"):
        return JSONResponse(
            {"ok": False, "error": "rating must be 'up' or 'down'"}, status_code=400
        )

    rec = {
        "ts": time.time(),
        "turn_id": str((data or {}).get("turn_id") or ""),
        "rating": rating,
        "clip_id": str((data or {}).get("clip_id") or ""),
        # Bound the stored strings so a runaway client can't write unbounded lines.
        "question": str((data or {}).get("question") or "")[:4000],
        "answer": str((data or {}).get("answer") or "")[:20000],
        "model": config.MODEL_ID,
        # Authoritative server-side active preset (what actually served the answer).
        "preset": _model_state.get("preset_id") or _read_active_preset(),
    }
    try:
        await run_in_threadpool(_append_jsonl, config.FEEDBACK_FILE, rec)
    except Exception as exc:
        return JSONResponse(
            {"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status_code=500
        )
    return JSONResponse({"ok": True})


# --- Model preset selection (/api/model/*) --------------------------------------------
# A UI-driven knob to switch the vLLM video-processing config (fps/max_frames/resolution).
# Because these are LAUNCH-time kwargs on vLLM 0.19.1 (per-request kwargs crash the engine),
# switching relaunches vLLM (~3-4 min model reload). We track the state so the UI can show
# a spinner while loading and a green check when the requested preset is ready to serve.
_model_state: dict = {"state": "unknown", "preset_id": None, "error": None, "since": 0.0}


def _read_active_preset() -> Optional[str]:
    try:
        with open(config.ACTIVE_PRESET_FILE, "r", encoding="utf-8") as f:
            return f.read().strip() or None
    except Exception:
        return None


def _write_active_preset(pid: str) -> None:
    try:
        os.makedirs(os.path.dirname(config.ACTIVE_PRESET_FILE), exist_ok=True)
        with open(config.ACTIVE_PRESET_FILE, "w", encoding="utf-8") as f:
            f.write(pid)
    except Exception:
        pass


async def _vllm_up() -> bool:
    return (await model_client.health_check()) is not None


async def _do_reconfigure(preset: dict) -> None:
    """Stop vLLM, relaunch it with the preset's env, and poll until healthy."""
    env = {
        **os.environ,
        "MM_PROCESSOR_KWARGS": preset["mm_processor_kwargs"],
        "NUM_FRAMES": str(preset["num_frames"]),
        "MAX_MODEL_LEN": str(preset["max_model_len"]),
        "MAX_NUM_BATCHED_TOKENS": str(preset["max_num_batched_tokens"]),
    }

    def _run(args):
        return subprocess.run(args, env=env, capture_output=True, text=True, timeout=90)

    try:
        await run_in_threadpool(_run, ["bash", config.SERVE_SH, "--stop"])
        await asyncio.sleep(3)
        await run_in_threadpool(_run, ["bash", config.SERVE_SH, "--bg"])
        # Poll for health: model reload is ~3-4 min; allow up to ~8 min.
        for _ in range(96):
            await asyncio.sleep(5)
            if await _vllm_up():
                _model_state.update(state="ready", preset_id=preset["id"], error=None,
                                    since=time.time())
                await run_in_threadpool(_write_active_preset, preset["id"])
                return
        _model_state.update(state="error", error="vLLM did not become healthy within ~8 min")
    except Exception as exc:
        _model_state.update(state="error", error=f"{type(exc).__name__}: {exc}")


@app.get("/api/model/status")
async def api_model_status():
    """Current serving state so the UI can render spinner / green check.

    state: 'loading' (reconfigure in flight) | 'ready' | 'down' | 'error' | 'unknown'.
    """
    up = await _vllm_up()
    st = dict(_model_state)
    if st["state"] in (None, "unknown"):
        # Not tracked yet this process: infer from the active-preset file + health.
        st["preset_id"] = _read_active_preset()
        st["state"] = "ready" if up else "down"
    elif st["state"] == "ready" and not up:
        # We thought ready but vLLM went away (crash/manual stop).
        st["state"] = "down"
    p = config.preset_by_id(st.get("preset_id")) if st.get("preset_id") else None
    st["label"] = p["label"] if p else None
    st["vllm_up"] = up
    return JSONResponse(st)


@app.post("/api/model/reconfigure")
async def api_model_reconfigure(request: Request):
    """Switch the active preset (relaunches vLLM). Returns immediately with state=loading;
    the UI then polls /api/model/status until state=ready."""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid JSON body"}, status_code=400)
    pid = str((data or {}).get("preset_id") or "").strip()
    preset = config.preset_by_id(pid)
    if not preset:
        return JSONResponse({"ok": False, "error": f"unknown preset_id: {pid}"}, status_code=400)
    if _model_state.get("state") == "loading":
        return JSONResponse(
            {"ok": False, "error": "a reconfigure is already in progress",
             "state": "loading", "preset_id": _model_state.get("preset_id")},
            status_code=409,
        )
    _model_state.update(state="loading", preset_id=pid, error=None, since=time.time())
    # Fire-and-forget: the relaunch + health poll runs in the background.
    asyncio.create_task(_do_reconfigure(preset))
    return JSONResponse({"ok": True, "state": "loading", "preset_id": pid, "label": preset["label"]})


# --- Static UI mount (must be LAST so it doesn't shadow /api/*) ------------------------
# WS3 builds frontend/; the dir may be empty while we build — that's fine. We create it
# if missing so StaticFiles can mount, and serve index.html at / via html=True.
os.makedirs(config.FRONTEND_DIR, exist_ok=True)


@app.get("/healthz", response_class=PlainTextResponse)
async def healthz():
    model = await model_client.health_check()
    return "ok" if model is None else f"ok (vLLM: {model})"


# html=True makes "/" serve index.html when present; if absent, StaticFiles 404s "/"
# which is fine until WS3 lands. Mounted last so /api/* and /healthz win.
app.mount("/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="frontend")
