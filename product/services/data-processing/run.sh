#!/usr/bin/env bash
# data-processing service (:8085) — the learn-loop C1 -> stage graph -> C2 v1 spine.
#
# Slot Law v1: the pipeline dialect lives entirely in
# code — there is NO backend env knob (ASR_BACKEND is dead; mock dialects exist
# only as code-constructed stage sets in tests). Env here is operational-only:
# where storage is, where the journal lives, whether THIS process owns the
# model-server fleet (DP_SUPERVISOR=1 starts the supervisor over
# servers/manifest.json — model servers need their per-server venvs built).
set -euo pipefail
cd "$(dirname "$0")"

export STORAGE_URL="${STORAGE_URL:-http://localhost:8083}"

# Honor the platform<->service contract: bind to HOST/PORT from the environment
# (deploy scripts set PORT). Fall back to the pinned dev host/port when run alone.
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8085}"

echo "[data-processing] STORAGE_URL=${STORAGE_URL} DP_SUPERVISOR=${DP_SUPERVISOR:-0} ${HOST}:${PORT}"
exec uvicorn app.main:app --host "${HOST}" --port "${PORT}" "$@"
