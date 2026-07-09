#!/usr/bin/env bash
# Input service (serve-loop MVP v0.0) — computer text chat surface + QueryBuilder.
# Runs on :8081. Relays the C9 stream from inference (${INFERENCE_URL}).
set -euo pipefail

# cd to this service dir so `app.main:app` (package import) resolves.
cd "$(dirname "$0")"

export INFERENCE_URL="${INFERENCE_URL:-http://localhost:8010}"
PORT="${PORT:-8081}"

exec uvicorn app.main:app --host "${HOST:-0.0.0.0}" --port "${PORT}" "$@"
