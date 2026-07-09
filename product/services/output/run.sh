#!/usr/bin/env bash
# Nucleus Output Service (:8082) — C9 delivery relay + static browser client.
# Serve-loop MVP v0.0. No GPU, no model backend — output only moves C9 streams.
set -euo pipefail

cd "$(dirname "$0")"

PORT="${PORT:-8082}"
HOST="${HOST:-0.0.0.0}"

# Prefer python3.11 per the stack convention; fall back to python3.
PY="python3"
command -v python3.11 >/dev/null 2>&1 && PY="python3.11"

exec "$PY" -m uvicorn app.main:app --host "$HOST" --port "$PORT" "$@"
