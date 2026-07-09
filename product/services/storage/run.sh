#!/usr/bin/env bash
# Storage service (:8083) — durable /sessions + model directory for the serve-loop MVP.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

# Dev SQLite DB lives beside the app unless overridden.
export STORAGE_DB_PATH="${STORAGE_DB_PATH:-$HERE/app/dev.db}"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8083}"

exec uvicorn app.main:app --host "$HOST" --port "$PORT" "$@"
