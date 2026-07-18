#!/usr/bin/env bash
# =============================================================================
# WS1 — Inference server launch (live_video_chat V0)
# Qwen3-VL-32B-Instruct on vLLM, TP=8 (full a3mega node), video-enabled,
# OpenAI-compatible at http://127.0.0.1:8000/v1  (Contract A).
#
# Validated build: vLLM 0.19.1  (post-fix: includes PR #36136 / issue #35909,
#   the Qwen3-VL num_frames/fps timestamp AssertionError fix; merged 2026-03-11).
#   We use 0.19.1 rather than 0.24.0 because 0.20.0+ wheels link libcudart.so.13
#   (CUDA 13, needs driver >=580) and this node's driver is 570 / CUDA 12.8.
#   0.19.1 is the highest CUDA-12 (libcudart.so.12) release. torch 2.10.0+cu128.
#
# Usage:
#   ./serve.sh            # start in foreground (Ctrl-C to stop)
#   ./serve.sh --bg       # start backgrounded; logs to $LOG, pid to $PIDFILE
#   ./serve.sh --stop     # stop a backgrounded server
#   ./serve.sh --status   # show status
# =============================================================================
set -euo pipefail

# ---- Config (single source of truth for the launch) -------------------------
# LONG PRE-RECORDED CLIP PROFILE: sized to reason over a ~30-min / ~128K-token clip
# (480x288 @ 1 fps -> 900 temporal patches x 135 tok ~= 128K). Every value is env-
# overridable so the smaller live-recording profile is a one-line override.
ENV_PY="/home/ubuntu/miniconda3/envs/vllm-vlm/bin"     # vLLM 0.19.1 env
MODEL="Qwen/Qwen3-VL-32B-Instruct"
HOST="0.0.0.0"
PORT="8000"
TP="8"                                                  # all 8x H100 (heads divide by 8)
MAX_MODEL_LEN="${MAX_MODEL_LEN:-200000}"                # ~128K video + Q/A + multi-turn headroom (<256K native, no YaRN)
MAX_NUM_BATCHED_TOKENS="${MAX_NUM_BATCHED_TOKENS:-163840}"  # a whole video must fit ONE prefill (ViT is not chunked) -> >= video tokens
GPU_MEM_UTIL="${GPU_MEM_UTIL:-0.90}"
MAX_NUM_SEQS="${MAX_NUM_SEQS:-4}"                       # single-user POC; small batch
NUM_FRAMES="${NUM_FRAMES:-2048}"                        # media-io loader frame cap (>= processor frames)
# Full HF video-processor kwargs as ONE JSON blob (single source of truth for fps /
# max_frames / size). This is the knob the backend rewrites per preset on reconfigure.
#   - Qwen defaults cap at max_frames=768 and size.longest_edge=24576 tokens (the "sweet
#     spot"). Raise max_frames to sample >768 frames; raise size.longest_edge to stop the
#     per-frame downscaling. Keep `fps` (NOT num_frames) so timestamp generation stays valid.
# Default = dense_1800: full 1800 frames @ 1fps, reduced res (~50K tokens). longest_edge is
# capped at 105M so vLLM 0.19.1's profiling dummy (~3x longest_edge/2048 ≈ 154K) stays under
# the 163840 batched-token / 256K position ceilings. Higher (full-res 1800f=121K) crashes
# startup on this vLLM version — see backend/config.py MODEL_PRESETS.
# NB: assign with an explicit if, NOT `${MM_PROCESSOR_KWARGS:-<json>}` — a `${VAR:-default}`
# whose default contains literal `}` makes bash close the expansion at the first `}` and
# append the trailing braces to the value (corrupts the JSON to `...}}}}`).
if [ -z "${MM_PROCESSOR_KWARGS:-}" ]; then
  MM_PROCESSOR_KWARGS='{"fps":1.0,"max_frames":1800,"size":{"longest_edge":105000000,"shortest_edge":4096}}'
fi
# Contract A: vLLM only reads file:// media under this root. WS2 MUST write its
# turn_<id>.mp4 clips under $ALLOWED_MEDIA (e.g. /mnt/localssd/live_video_chat/clips/).
ALLOWED_MEDIA="/mnt/localssd"

export HF_HOME="/mnt/localssd/.hf-home"                 # keep model off NFS
export VLLM_HOST_IP="127.0.0.1"
# HF_TOKEN comes from ~/.bashrc; export it if present (model is already cached anyway).
if [ -z "${HF_TOKEN:-}" ]; then
  HF_TOKEN="$(grep -oP 'HF_TOKEN=\K\S+' "$HOME/.bashrc" 2>/dev/null | head -1 || true)"
  export HF_TOKEN
fi

LOG="/mnt/localssd/.hf-home/vllm_serve.log"
PIDFILE="/mnt/localssd/.hf-home/vllm_serve.pid"

# Export everything launch() reads so the backgrounded child (--bg) inherits it DIRECTLY
# as raw env strings. This avoids re-quoting the MM_PROCESSOR_KWARGS JSON through a nested
# `bash -c "export VAR='...'"`, which corrupted the braces (added trailing `}}`).
export ENV_PY MODEL HOST PORT TP MAX_MODEL_LEN MAX_NUM_BATCHED_TOKENS GPU_MEM_UTIL \
       MAX_NUM_SEQS NUM_FRAMES MM_PROCESSOR_KWARGS ALLOWED_MEDIA

launch() {
  # --mm-processor-kwargs sets default fps + per-frame pixel bounds; we pass fps WITH
  # num_frames (--media-io-kwargs) to avoid the timestamp-length assertion. --max-num-
  # batched-tokens must be >= a single clip's video-token count because the ViT output
  # for one video is NOT chunked across prefill steps. --enable-prefix-caching (default
  # on in the V1 engine; explicit here) lets turn 2+ on the SAME clip re-use the big
  # video prefix (KV) so only the new question is prefilled — cheap repeats + cheap multiturn.
  exec "$ENV_PY/vllm" serve "$MODEL" \
    --tensor-parallel-size "$TP" \
    --max-model-len "$MAX_MODEL_LEN" \
    --max-num-batched-tokens "$MAX_NUM_BATCHED_TOKENS" \
    --max-num-seqs "$MAX_NUM_SEQS" \
    --enable-prefix-caching \
    --allowed-local-media-path "$ALLOWED_MEDIA" \
    --limit-mm-per-prompt '{"video":1,"image":0}' \
    --mm-processor-kwargs "$MM_PROCESSOR_KWARGS" \
    --media-io-kwargs '{"video":{"num_frames":'"$NUM_FRAMES"'}}' \
    --gpu-memory-utilization "$GPU_MEM_UTIL" \
    --served-model-name "$MODEL" \
    --host "$HOST" --port "$PORT"
}

case "${1:-}" in
  --stop)
    if [ -f "$PIDFILE" ]; then
      PID="$(cat "$PIDFILE")"
      echo "Stopping vLLM (pid $PID) and children..."
      pkill -TERM -P "$PID" 2>/dev/null || true
      kill -TERM "$PID" 2>/dev/null || true
      sleep 3
      pkill -9 -f "vllm serve $MODEL" 2>/dev/null || true
      rm -f "$PIDFILE"
      echo "Stopped."
    else
      echo "No pidfile; trying pkill..."
      pkill -9 -f "vllm serve $MODEL" 2>/dev/null || echo "nothing running."
    fi
    ;;
  --status)
    if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
      echo "UP — http://127.0.0.1:$PORT/v1 (model: $MODEL)"
    else
      echo "DOWN (or still loading). Tail: $LOG"
    fi
    ;;
  --bg)
    echo "Launching vLLM in background -> $LOG"
    # Config vars (incl. MM_PROCESSOR_KWARGS, HF_*) are exported above, so this child
    # inherits them verbatim — no fragile per-var re-quoting.
    nohup bash -c "$(declare -f launch); launch" > "$LOG" 2>&1 < /dev/null &
    echo $! > "$PIDFILE"
    echo "pid $(cat "$PIDFILE")  (model load takes ~2-4 min; poll: ./serve.sh --status)"
    ;;
  *)
    echo "Launching vLLM (foreground). vLLM 0.19.1 | $MODEL | TP=$TP | port $PORT"
    launch
    ;;
esac
