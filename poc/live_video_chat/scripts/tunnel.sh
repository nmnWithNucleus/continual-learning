#!/usr/bin/env bash
# =============================================================================
# WS5 — Cloudflare Tunnel / HTTPS exposure for live_video_chat V0
# =============================================================================
# Exposes the local BACKEND (WS2, default port 8080) to the public internet over
# HTTPS via a Cloudflare "quick tunnel" (https://<random>.trycloudflare.com).
#
# WHY HTTPS IS MANDATORY: iOS Safari getUserMedia is secure-context-only. Over
# plain HTTP, navigator.mediaDevices is `undefined` and the camera/mic never
# work. cloudflared auto-provisions a valid TLS cert, so the phone gets a real
# HTTPS origin with zero cert work.
#
# WHAT IT DOES
#   1. Verifies cloudflared is installed (installs hint if not).
#   2. Starts `cloudflared tunnel --url http://localhost:<PORT>` in the
#      background (logs to a file).
#   3. Parses the public https://<random>.trycloudflare.com URL from the log,
#      prints it prominently, and writes it to scripts/.tunnel_url so WS3/WS6
#      can read it programmatically.
#   4. Stays attached and tails the cloudflared log so you can see traffic /
#      errors. Ctrl-C tears the tunnel down cleanly.
#
# VALIDATED (2026-06-30, against a faithful uvicorn/Starlette stub == WS2 stack):
#   - Page loads over HTTPS/2 (server: cloudflare, valid TLS).
#   - 40MB and 90MB multipart POSTs pass through (no body-size cap surprises).
#   - Chunked text/plain streaming arrives INCREMENTALLY (~0.3s/token), NOT
#     buffered — first-token latency is preserved end-to-end.
#   NOTE: streaming only passes through if the origin emits PROPER HTTP chunked
#   framing (uvicorn/Starlette StreamingResponse does; a naive Python
#   http.server that relies on connection-close does NOT — cloudflared buffers
#   the latter until close). WS2's FastAPI/Starlette stack is correct here.
#
# USAGE
#   scripts/tunnel.sh                 # tunnel -> http://localhost:8080
#   PORT=9000 scripts/tunnel.sh       # tunnel -> http://localhost:9000
#   scripts/tunnel.sh 8080            # positional port also works
#
# RESTART
#   Just re-run this script. The quick-tunnel URL ROTATES every launch, so the
#   https://… changes each time — re-share the new link with the phone and
#   re-read scripts/.tunnel_url. (See "STABLE HOSTNAME" note at the bottom for
#   the named-tunnel option if the churn becomes annoying.)
#
# URL HAND-OFF TO WS3 / WS6
#   - Printed to stdout (the big banner).
#   - Written to:  scripts/.tunnel_url   (single line, no newline noise) — the
#     integrator (WS6) reads this file to know where the phone should point.
#   - Full cloudflared log at:  scripts/.tunnel.log
# =============================================================================

set -euo pipefail

# --- config -----------------------------------------------------------------
# Point ONLY at the backend (WS2). Never expose vLLM:8000 or the clip dir.
PORT="${PORT:-${1:-8080}}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="${SCRIPT_DIR}/.tunnel.log"
URL_FILE="${SCRIPT_DIR}/.tunnel_url"

# Locate cloudflared (PATH, or the user-local install used in V0).
CLOUDFLARED="$(command -v cloudflared || true)"
if [[ -z "${CLOUDFLARED}" && -x "${HOME}/.local/bin/cloudflared" ]]; then
  CLOUDFLARED="${HOME}/.local/bin/cloudflared"
fi
if [[ -z "${CLOUDFLARED}" ]]; then
  echo "ERROR: cloudflared not found." >&2
  echo "Install it (no root needed):" >&2
  echo "  mkdir -p ~/.local/bin" >&2
  echo "  curl -fL -o ~/.local/bin/cloudflared \\" >&2
  echo "    https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64" >&2
  echo "  chmod +x ~/.local/bin/cloudflared" >&2
  exit 1
fi

echo "cloudflared: ${CLOUDFLARED} ($(${CLOUDFLARED} --version 2>/dev/null | head -1))"

# --- warn if nothing is listening on the target port yet --------------------
# (Not fatal: the tunnel can come up before the backend; requests will just 502
#  until WS2 is listening. We surface it so you know.)
if command -v ss >/dev/null 2>&1; then
  if ! ss -ltn 2>/dev/null | grep -q ":${PORT} "; then
    echo "WARNING: nothing is listening on 127.0.0.1:${PORT} yet."
    echo "         Start the backend (WS2) on ${PORT}; until then the tunnel returns 502."
  fi
fi

# --- launch the quick tunnel in the background ------------------------------
echo "Starting Cloudflare quick tunnel -> http://localhost:${PORT} ..."
: > "${LOG_FILE}"
"${CLOUDFLARED}" tunnel --no-autoupdate --url "http://localhost:${PORT}" \
  >"${LOG_FILE}" 2>&1 &
CF_PID=$!

# Clean teardown on exit / Ctrl-C.
cleanup() {
  echo ""
  echo "Tearing down tunnel (pid ${CF_PID})..."
  kill "${CF_PID}" 2>/dev/null || true
  wait "${CF_PID}" 2>/dev/null || true
  rm -f "${URL_FILE}"
  echo "Tunnel stopped."
}
trap cleanup INT TERM EXIT

# --- wait for the public URL to appear in the log ---------------------------
PUBLIC_URL=""
for _ in $(seq 1 30); do
  if ! kill -0 "${CF_PID}" 2>/dev/null; then
    echo "ERROR: cloudflared exited early. Log:" >&2
    tail -20 "${LOG_FILE}" >&2
    exit 1
  fi
  PUBLIC_URL="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "${LOG_FILE}" | head -1 || true)"
  [[ -n "${PUBLIC_URL}" ]] && break
  sleep 1
done

if [[ -z "${PUBLIC_URL}" ]]; then
  echo "ERROR: timed out waiting for the trycloudflare URL. Log:" >&2
  tail -20 "${LOG_FILE}" >&2
  exit 1
fi

# Hand the URL off to WS3/WS6.
printf '%s' "${PUBLIC_URL}" > "${URL_FILE}"

cat <<BANNER

============================================================
  PUBLIC HTTPS URL (open this on the iPhone):

      ${PUBLIC_URL}

  -> backend on http://localhost:${PORT}
  -> URL also written to: ${URL_FILE}
  -> cloudflared log:     ${LOG_FILE}

  This is an EPHEMERAL quick tunnel: the URL changes on every
  restart. Re-run this script to get a fresh URL, then re-share.
  Ctrl-C to stop the tunnel.
============================================================

BANNER

# Stay attached so the tunnel keeps running and you can watch traffic/errors.
# (The trap above tears it down on Ctrl-C.)
tail -f "${LOG_FILE}"

# =============================================================================
# STABLE HOSTNAME (optional, NOT required for V0)
# -----------------------------------------------------------------------------
# Quick tunnels rotate the hostname each launch. If that churn gets annoying and
# you have a Cloudflare account + a domain on Cloudflare, use a NAMED tunnel for
# a fixed hostname:
#   cloudflared tunnel login
#   cloudflared tunnel create live-video-chat
#   cloudflared tunnel route dns live-video-chat vchat.example.com
#   cloudflared tunnel run --url http://localhost:8080 live-video-chat
# Then the phone always points at https://vchat.example.com. V0 deliberately
# skips this to avoid requiring an account.
# =============================================================================
