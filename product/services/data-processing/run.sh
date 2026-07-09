#!/usr/bin/env bash
# data-processing service (:8085) — the learn-loop C1 -> ASR -> C2 capture skeleton.
#
# Default backend is `mock` — no GPU needed, the whole loop runs on any box. For
# real speech-to-text, set ASR_BACKEND=faster_whisper (installs torch/av on first
# import; see requirements.txt).
set -euo pipefail
cd "$(dirname "$0")"

export ASR_BACKEND="${ASR_BACKEND:-mock}"
export STORAGE_URL="${STORAGE_URL:-http://localhost:8083}"

# Honor the platform<->service contract: bind to HOST/PORT from the environment
# (deploy scripts set PORT). Fall back to the pinned dev host/port when run alone.
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8085}"

echo "[data-processing] ASR_BACKEND=${ASR_BACKEND} STORAGE_URL=${STORAGE_URL} ${HOST}:${PORT}"
exec uvicorn app.main:app --host "${HOST}" --port "${PORT}" "$@"
