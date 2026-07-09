#!/usr/bin/env bash
# Run the inference service (serve-loop MVP, :8010).
#
# Default backend is `mock` — no GPU needed, the whole loop runs on any box.
# For the real model, set MODEL_BACKEND=vllm and point VLLM_URL at a running
# vLLM server (see serve_vllm.sh, GPU node only).
set -euo pipefail
cd "$(dirname "$0")"

export MODEL_BACKEND="${MODEL_BACKEND:-mock}"
export STORAGE_URL="${STORAGE_URL:-http://localhost:8083}"
export VLLM_URL="${VLLM_URL:-http://localhost:8000}"
export MODEL_ID="${MODEL_ID:-Qwen/Qwen3-VL-32B-Instruct}"

# Honor the platform<->service contract: bind to HOST/PORT from the environment
# (run_all.sh sets PORT). Fall back to the pinned dev host/port when run alone.
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8010}"

echo "[inference] MODEL_BACKEND=${MODEL_BACKEND} STORAGE_URL=${STORAGE_URL} ${HOST}:${PORT}"
exec uvicorn app.main:app --host "${HOST}" --port "${PORT}" "$@"
