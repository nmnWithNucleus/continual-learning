#!/usr/bin/env bash
# =============================================================================
# WS6 — run_all.sh : bring up the live_video_chat V0 stack on ONE a3mega node.
#
# Order (the seams that must hold for file:// + 127.0.0.1 to work):
#   1. vLLM (WS1)        — Qwen3-VL-32B-Instruct, TP=8, :8000  (ASSERTED up; not
#                          started here — model load is ~3-4 min, see server/serve.sh)
#   2. backend (WS2/WS4) — FastAPI hub + ASR, :8080            (started detached)
#   3. cloudflared (WS5) — public HTTPS tunnel -> :8080        (started detached)
#
# Everything is started DETACHED (nohup + pidfile) so the stack survives this
# script exiting — the phone keeps working after you walk away.
#
# Usage:
#   scripts/run_all.sh            # bring the stack up, print the phone URL + checklist
#   scripts/run_all.sh --status   # show what's running
#   scripts/run_all.sh --stop     # stop backend + tunnel (leaves vLLM up)
#   scripts/run_all.sh --restart  # stop then up
#
# NOTE on vLLM: this script does NOT (re)start vLLM — it asserts it's up and bails
# with instructions if not, because the 32B model load takes minutes. Start it
# separately with:  server/serve.sh --bg   (then poll server/serve.sh --status).
# =============================================================================
set -euo pipefail

# --- paths -------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

VLLM_PORT="8000"
BACKEND_PORT="8080"

STATE_DIR="/mnt/localssd/.hf-home"          # node-local, same place WS1 logs
mkdir -p "${STATE_DIR}"
BACKEND_LOG="${STATE_DIR}/backend_uvicorn.log"
BACKEND_PIDFILE="${STATE_DIR}/backend_uvicorn.pid"

TUNNEL_LOG="${SCRIPT_DIR}/.tunnel.log"
TUNNEL_URL_FILE="${SCRIPT_DIR}/.tunnel_url"
TUNNEL_PIDFILE="${SCRIPT_DIR}/.tunnel.pid"

# --- helpers -----------------------------------------------------------------
is_up() { curl -fsS -m 4 "$1" >/dev/null 2>&1; }

pid_alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }

print_checklist() {
  local url="$1"
  cat <<BANNER

============================================================
  PHONE URL (open in iOS Safari):

      ${url}

  ON-DEVICE CHECKLIST (the only leg not yet auto-validated):
   1. Open the link in Safari on the iPhone.
   2. Tap the screen to start the camera -> allow Camera access.
   3. The greeting "Hey — show me something and ask." shows.
   4. Tap the mic, speak a question -> allow Microphone -> text
      fills the (editable) box.  (Or just type a question.)
   5. Tap Record -> point at something -> it auto-stops at 30s
      (or tap stop). Send becomes enabled.
   6. Tap Send -> spinner -> the answer STREAMS in token-by-token,
      grounded in what the camera saw.
   7. Start another turn -> the previous answer is gone (length-1).

  Stack:  vLLM :${VLLM_PORT}  |  backend :${BACKEND_PORT}  |  tunnel -> public HTTPS
  Stop:   scripts/run_all.sh --stop      (leaves vLLM running)
  Logs:   backend ${BACKEND_LOG}
          tunnel  ${TUNNEL_LOG}
============================================================

BANNER
}

# --- subcommands -------------------------------------------------------------
case "${1:-}" in
  --status)
    echo "vLLM   (:${VLLM_PORT}): $(is_up "http://127.0.0.1:${VLLM_PORT}/health" && echo UP || echo DOWN)"
    echo "backend(:${BACKEND_PORT}): $(is_up "http://127.0.0.1:${BACKEND_PORT}/healthz" && echo UP || echo DOWN)"
    if pid_alive "${TUNNEL_PIDFILE}"; then
      echo "tunnel : UP (pid $(cat "${TUNNEL_PIDFILE}")) -> $(cat "${TUNNEL_URL_FILE}" 2>/dev/null || echo '?')"
    else
      echo "tunnel : DOWN"
    fi
    exit 0
    ;;
  --stop)
    echo "Stopping tunnel + backend (vLLM left running)..."
    if pid_alive "${TUNNEL_PIDFILE}"; then kill "$(cat "${TUNNEL_PIDFILE}")" 2>/dev/null || true; fi
    rm -f "${TUNNEL_PIDFILE}" "${TUNNEL_URL_FILE}"
    if pid_alive "${BACKEND_PIDFILE}"; then kill "$(cat "${BACKEND_PIDFILE}")" 2>/dev/null || true; fi
    rm -f "${BACKEND_PIDFILE}"
    echo "Stopped."
    exit 0
    ;;
  --restart)
    "${BASH_SOURCE[0]}" --stop || true
    sleep 1
    ;;
  "" ) : ;;  # default: bring up
  * ) echo "usage: run_all.sh [--status|--stop|--restart]" >&2; exit 2 ;;
esac

# =============================================================================
# 1. ASSERT vLLM (WS1) is up — do NOT start it here (slow model load).
# =============================================================================
echo "[1/3] Checking vLLM (WS1) on :${VLLM_PORT} ..."
if ! is_up "http://127.0.0.1:${VLLM_PORT}/health"; then
  cat >&2 <<EOF
ERROR: vLLM is not up at http://127.0.0.1:${VLLM_PORT}.
  Start it first (model load ~3-4 min):
      ${ROOT_DIR}/server/serve.sh --bg
      ${ROOT_DIR}/server/serve.sh --status   # poll until UP
  Must run on the same node (nucla3m-a3meganodeset-7) so file:// clips + 127.0.0.1 resolve.
EOF
  exit 1
fi
echo "      vLLM UP."

# =============================================================================
# 2. backend (WS2 + WS4 ASR) — start detached on :${BACKEND_PORT}.
# =============================================================================
echo "[2/3] Starting backend (WS2/WS4) on :${BACKEND_PORT} ..."
if is_up "http://127.0.0.1:${BACKEND_PORT}/healthz"; then
  echo "      backend already UP — leaving it."
else
  nohup bash "${ROOT_DIR}/backend/run.sh" >"${BACKEND_LOG}" 2>&1 &
  echo $! > "${BACKEND_PIDFILE}"
  # Wait for it to answer /healthz (ASR warmup at startup can add a few seconds).
  for _ in $(seq 1 40); do
    is_up "http://127.0.0.1:${BACKEND_PORT}/healthz" && break
    sleep 1
  done
  if ! is_up "http://127.0.0.1:${BACKEND_PORT}/healthz"; then
    echo "ERROR: backend did not come up. Log tail:" >&2
    tail -30 "${BACKEND_LOG}" >&2
    exit 1
  fi
  echo "      backend UP (pid $(cat "${BACKEND_PIDFILE}"))."
fi

# =============================================================================
# 3. cloudflared (WS5) — public HTTPS tunnel -> :${BACKEND_PORT}, detached.
# =============================================================================
echo "[3/3] Starting cloudflared tunnel (WS5) -> :${BACKEND_PORT} ..."
CLOUDFLARED="$(command -v cloudflared || true)"
if [ -z "${CLOUDFLARED}" ] && [ -x "${HOME}/.local/bin/cloudflared" ]; then
  CLOUDFLARED="${HOME}/.local/bin/cloudflared"
fi
if [ -z "${CLOUDFLARED}" ]; then
  echo "ERROR: cloudflared not found (see scripts/tunnel.sh for install)." >&2
  exit 1
fi

# Restart any stale tunnel so we get one fresh URL.
if pid_alive "${TUNNEL_PIDFILE}"; then kill "$(cat "${TUNNEL_PIDFILE}")" 2>/dev/null || true; fi
: > "${TUNNEL_LOG}"
nohup "${CLOUDFLARED}" tunnel --no-autoupdate --url "http://localhost:${BACKEND_PORT}" \
  >"${TUNNEL_LOG}" 2>&1 &
echo $! > "${TUNNEL_PIDFILE}"

PUBLIC_URL=""
for _ in $(seq 1 30); do
  pid_alive "${TUNNEL_PIDFILE}" || { echo "ERROR: cloudflared exited:" >&2; tail -20 "${TUNNEL_LOG}" >&2; exit 1; }
  PUBLIC_URL="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "${TUNNEL_LOG}" | head -1 || true)"
  [ -n "${PUBLIC_URL}" ] && break
  sleep 1
done
if [ -z "${PUBLIC_URL}" ]; then
  echo "ERROR: timed out getting the trycloudflare URL. Log:" >&2
  tail -20 "${TUNNEL_LOG}" >&2
  exit 1
fi
printf '%s' "${PUBLIC_URL}" > "${TUNNEL_URL_FILE}"
echo "      tunnel UP -> ${PUBLIC_URL}"

print_checklist "${PUBLIC_URL}"
