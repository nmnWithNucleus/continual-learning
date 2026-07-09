#!/usr/bin/env bash
# Recording service (:8084) — continuous-stream capture -> /raw blob + C1 push.
#
# Talks to storage /raw (STORAGE_URL) and data-processing /ingest (DP_URL); those
# URLs can also be passed per-request in the POST /capture/run body.
set -euo pipefail
cd "$(dirname "$0")"

export STORAGE_URL="${STORAGE_URL:-http://localhost:8083}"
export DP_URL="${DP_URL:-http://localhost:8085}"

# Honor the platform<->service contract: bind HOST/PORT from the environment
# (run_all.sh sets PORT). Fall back to the pinned dev host/port when run alone.
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8084}"

echo "[recording] STORAGE_URL=${STORAGE_URL} DP_URL=${DP_URL} ${HOST}:${PORT}"
exec uvicorn app.main:app --host "${HOST}" --port "${PORT}" "$@"
