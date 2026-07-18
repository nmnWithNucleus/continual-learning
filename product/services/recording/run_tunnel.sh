#!/usr/bin/env bash
#
# run_tunnel.sh — expose the recording server (phone web client + ingest) over HTTPS.
#
# iOS getUserMedia is secure-context-only, so the phone client MUST be reached over
# HTTPS: a cloudflared quick tunnel is the beta-grade answer (platform owns real
# ingress later). The ephemeral URL ROTATES on every restart — it is written to
# var/tunnel_url.txt (the POC's .tunnel_url pattern) and printed here; hand THAT
# URL to the tester (the client lives at <url>/client/).
#
# Usage:
#   bash run_tunnel.sh            # foreground (Ctrl-C stops it)
#   bash run_tunnel.sh --bg       # detach; log + pid under var/
#   bash run_tunnel.sh --stop     # stop a --bg tunnel
#   bash run_tunnel.sh --url      # print the current URL (from var/tunnel_url.txt)
#
set -u

SERVICE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VAR_DIR="${RECORDING_VAR_DIR:-$SERVICE_DIR/var}"
URL_FILE="$VAR_DIR/tunnel_url.txt"
LOG_FILE="$VAR_DIR/tunnel.log"
PID_FILE="$VAR_DIR/tunnel.pid"
TARGET="${TUNNEL_TARGET:-http://127.0.0.1:${PORT:-8084}}"

mkdir -p "$VAR_DIR"

extract_url() {
  # cloudflared prints the assigned https://*.trycloudflare.com URL on stderr.
  grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$1" | head -1
}

case "${1:-}" in
  --stop)
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      kill "$(cat "$PID_FILE")" && echo "tunnel stopped (pid $(cat "$PID_FILE"))"
    else
      echo "no running tunnel (pid file: $PID_FILE)"
    fi
    rm -f "$PID_FILE" "$URL_FILE"
    ;;
  --url)
    [ -f "$URL_FILE" ] && cat "$URL_FILE" || { echo "no tunnel URL recorded" >&2; exit 1; }
    ;;
  --bg)
    nohup cloudflared tunnel --url "$TARGET" >"$LOG_FILE" 2>&1 &
    echo $! >"$PID_FILE"
    echo "waiting for tunnel URL..."
    for _ in $(seq 1 30); do
      URL="$(extract_url "$LOG_FILE" || true)"
      [ -n "$URL" ] && break
      sleep 1
    done
    if [ -n "${URL:-}" ]; then
      echo "$URL" >"$URL_FILE"
      echo "tunnel up: $URL  (phone client: $URL/client/)"
      echo "URL recorded in $URL_FILE — rotates on every restart."
    else
      echo "tunnel did not report a URL in 30s — see $LOG_FILE" >&2
      exit 1
    fi
    ;;
  ""|--fg)
    echo "target: $TARGET — URL will be printed by cloudflared and saved to $URL_FILE"
    cloudflared tunnel --url "$TARGET" 2> >(tee "$LOG_FILE" >&2) &
    CF_PID=$!
    trap 'kill $CF_PID 2>/dev/null' INT TERM
    for _ in $(seq 1 30); do
      URL="$(extract_url "$LOG_FILE" || true)"
      [ -n "$URL" ] && { echo "$URL" >"$URL_FILE"; echo "tunnel up: $URL/client/"; break; }
      sleep 1
    done
    wait $CF_PID
    ;;
  *)
    echo "usage: run_tunnel.sh [--bg | --stop | --url | --fg]" >&2
    exit 2
    ;;
esac
