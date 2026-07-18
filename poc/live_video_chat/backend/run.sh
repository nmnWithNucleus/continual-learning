#!/usr/bin/env bash
# Start the WS2 FastAPI hub on port 8080 (WS5's cloudflared tunnel points here).
# Runs on the same node as WS1's vLLM so file:// clip paths and 127.0.0.1 just work.
#
# Env: conda `moe` — it has fastapi/uvicorn/httpx/python-multipart (WS2) AND
#      faster-whisper/ctranslate2/numpy + ffmpeg 7.1 (WS4 ASR). One env runs both.
set -euo pipefail

cd "$(dirname "$0")"

# Activate the conda `moe` env if available (deps live there). Harmless if already active.
if command -v conda >/dev/null 2>&1; then
  # shellcheck disable=SC1091
  source "$(conda info --base)/etc/profile.d/conda.sh" 2>/dev/null || true
  conda activate moe 2>/dev/null || true
fi

# Ensure the clip scratch dir exists (shared with WS1's vLLM via the node filesystem,
# and UNDER vLLM's --allowed-local-media-path=/mnt/localssd so file:// loads are allowed).
mkdir -p "${TURNS_DIR:-/mnt/localssd/poc/live_video_chat/turns}"

# Node-local HF cache (faster-whisper weights land here, off NFS), matching WS1/WS4.
export HF_HOME="${HF_HOME:-/mnt/localssd/.hf-home}"
# Pin ASR to the last GPU so it shares cleanly with vLLM's TP=8 (which uses all 8 too,
# but ASR is tiny / GPU-light). Override with ASR_DEVICE=cpu to keep GPUs fully for vLLM.
export ASR_DEVICE_INDEX="${ASR_DEVICE_INDEX:-7}"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8080}"

# Single worker: the app is stateless but we keep one process so the async httpx client
# streaming relay is simplest. --timeout-keep-alive is generous for long streamed turns.
exec uvicorn app:app \
  --host "$HOST" \
  --port "$PORT" \
  --timeout-keep-alive 75 \
  --no-access-log
