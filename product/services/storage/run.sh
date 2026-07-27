#!/usr/bin/env bash
# Storage service (:8083) — durable /sessions + model directory for the serve-loop MVP.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

# Dev SQLite DB + local /raw blob store live beside the app unless overridden.
export STORAGE_DB_PATH="${STORAGE_DB_PATH:-$HERE/app/dev.db}"
export STORAGE_RAW_DIR="${STORAGE_RAW_DIR:-$HERE/app/raw_store}"
# C14 reservoir (amplified corpora) — runtime output, gitignored like the blob store.
export STORAGE_RESERVOIR_DIR="${STORAGE_RESERVOIR_DIR:-$HERE/app/reservoir}"
# C13 registry artifacts — COMMITTED source, not runtime output, so they sit at the
# service root beside app/ exactly as continuum's do.
export STORAGE_RECIPES_DIR="${STORAGE_RECIPES_DIR:-$HERE/recipes}"
export STORAGE_POLICIES_DIR="${STORAGE_POLICIES_DIR:-$HERE/policies}"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8083}"

# Prefer the SERVICE venv's uvicorn over whatever PATH happens to resolve to. On a box
# where a conda env sits ahead on PATH this silently worked, because that env happened to
# carry fastapi+sqlite; on a machine where the service venv is the only place those deps
# live, `uvicorn` off PATH dies on import and takes the whole demo's step 1 with it.
if [ -x "$HERE/.venv/bin/uvicorn" ]; then
  exec "$HERE/.venv/bin/uvicorn" app.main:app --host "$HOST" --port "$PORT" "$@"
fi
exec uvicorn app.main:app --host "$HOST" --port "$PORT" "$@"
