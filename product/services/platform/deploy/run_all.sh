#!/usr/bin/env bash
#
# run_all.sh — platform bring-up for the Nucleus serve-loop MVP (v0.0).
#
# Starts the four app services of the text-only walking skeleton, in dependency
# order, waiting on each /health before starting the next:
#
#     storage (8083) -> inference (8010, MODEL_BACKEND=mock) -> output (8082) -> input (8081)
#
# Then prints the surface URL (http://localhost:8081) and a bring-up checklist.
#
# This is INFRA GLUE ONLY — no application logic lives here. It owns a single
# shared Python venv, installs each service's requirements into it, launches each
# service via its own run.sh, and tracks PIDs so it can stop/report the fleet.
#
# Usage:
#   bash run_all.sh            # bring the loop up (default)
#   bash run_all.sh --stop     # stop everything this script started
#   bash run_all.sh --status   # report per-service pid + /health
#   bash run_all.sh --restart  # --stop then bring up
#   bash run_all.sh --skip-install   # bring up without re-running pip install
#   bash run_all.sh --help
#
# Config: deploy/.env (copy from .env.example). Any value not set there falls
# back to a built-in default, so the script also runs with no .env at all.
#
# Platform<->service contract (what each sibling run.sh MUST honour):
#   * read HOST and PORT from the environment and bind uvicorn to them;
#   * expose GET /health returning HTTP 200 when ready;
#   * use the active venv on PATH (do not create a private venv);
#   * inference additionally reads MODEL_BACKEND / MODEL_ID / VLLM_URL / STORAGE_URL;
#   * input/output read INFERENCE_URL / STORAGE_URL / OUTPUT_URL as needed.
#
set -u

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLATFORM_DIR="$(dirname "$DEPLOY_DIR")"
DEFAULT_SERVICES_ROOT="$(dirname "$PLATFORM_DIR")"   # product/services
# These are overridable via env (the self-test isolates its own logs/pids/venv).
: "${LOG_DIR:=$DEPLOY_DIR/logs}"
: "${RUN_DIR:=$DEPLOY_DIR/run}"
: "${VENV_DIR:=$DEPLOY_DIR/.venv}"
: "${ENV_FILE:=$DEPLOY_DIR/.env}"

mkdir -p "$LOG_DIR" "$RUN_DIR"

# ---------------------------------------------------------------------------
# Config: source .env if present, then apply defaults for anything unset.
# Pre-existing environment values win over .env (standard precedence: caller
# env > .env > built-in default) is NOT what we want here; .env is the operator
# knob, so .env wins over the ambient shell for these keys.
# ---------------------------------------------------------------------------
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

: "${HOST:=127.0.0.1}"
: "${MODEL_BACKEND:=mock}"
: "${MODEL_ID:=Qwen/Qwen3-VL-32B-Instruct}"

: "${STORAGE_PORT:=8083}"
: "${INFERENCE_PORT:=8010}"
: "${OUTPUT_PORT:=8082}"
: "${INPUT_PORT:=8081}"
: "${VLLM_PORT:=8000}"

: "${STORAGE_URL:=http://${HOST}:${STORAGE_PORT}}"
: "${INFERENCE_URL:=http://${HOST}:${INFERENCE_PORT}}"
: "${OUTPUT_URL:=http://${HOST}:${OUTPUT_PORT}}"
: "${INPUT_URL:=http://${HOST}:${INPUT_PORT}}"
: "${VLLM_URL:=http://${HOST}:${VLLM_PORT}}"

: "${HEALTH_TIMEOUT:=60}"          # seconds to wait per service /health
: "${SERVICES_ROOT:=$DEFAULT_SERVICES_ROOT}"   # override for self-test
: "${PYTHON_BIN:=}"                # explicit interpreter; auto-detected if empty
: "${PLATFORM_SKIP_INSTALL:=0}"    # 1 = skip pip install (also --skip-install)

# Interpreter used by http_ok's no-curl fallback before/without a venv build;
# ensure_venv re-points this at the shared venv python.
PY="$(command -v python3 || command -v python || printf 'python3')"

# Export the wiring so every child (and its run.sh) inherits it.
export HOST MODEL_BACKEND MODEL_ID
export STORAGE_URL INFERENCE_URL OUTPUT_URL INPUT_URL VLLM_URL
export STORAGE_PORT INFERENCE_PORT OUTPUT_PORT INPUT_PORT VLLM_PORT

# Service table — START ORDER matters (dependencies first).
#   name  port-var
SERVICES=(
  "storage:${STORAGE_PORT}"
  "inference:${INFERENCE_PORT}"
  "output:${OUTPUT_PORT}"
  "input:${INPUT_PORT}"
)

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
c_reset=""; c_bold=""; c_green=""; c_red=""; c_yellow=""; c_dim=""
if [ -t 1 ]; then
  c_reset="$(printf '\033[0m')"; c_bold="$(printf '\033[1m')"
  c_green="$(printf '\033[32m')"; c_red="$(printf '\033[31m')"
  c_yellow="$(printf '\033[33m')"; c_dim="$(printf '\033[2m')"
fi

log()  { printf '%s\n' "$*"; }
info() { printf '%s->%s %s\n' "$c_bold" "$c_reset" "$*"; }
ok()   { printf '  %s[ ok ]%s %s\n' "$c_green" "$c_reset" "$*"; }
warn() { printf '  %s[warn]%s %s\n' "$c_yellow" "$c_reset" "$*"; }
err()  { printf '  %s[fail]%s %s\n' "$c_red" "$c_reset" "$*" >&2; }

pidfile_for() { printf '%s/%s.pid' "$RUN_DIR" "$1"; }
logfile_for() { printf '%s/%s.log' "$LOG_DIR" "$1"; }

# HTTP 200 check on a URL. Uses curl if present, else python stdlib.
http_ok() {
  local url="$1"
  if command -v curl >/dev/null 2>&1; then
    curl -fsS -m 2 -o /dev/null "$url" 2>/dev/null
    return $?
  fi
  "$PY" - "$url" <<'PY' 2>/dev/null
import sys, urllib.request
try:
    r = urllib.request.urlopen(sys.argv[1], timeout=2)
    sys.exit(0 if 200 <= r.status < 300 else 1)
except Exception:
    sys.exit(1)
PY
}

pid_alive() { [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null; }

running_pid() {
  # echo the live pid for a service, or nothing.
  local name="$1" pf p
  pf="$(pidfile_for "$name")"
  [ -f "$pf" ] || return 1
  p="$(cat "$pf" 2>/dev/null)"
  if pid_alive "$p"; then printf '%s' "$p"; return 0; fi
  return 1
}

# Kill whatever is bound to a tcp port (belt-and-braces on stop).
kill_port() {
  local port="$1" sig="${2:-TERM}"
  if command -v fuser >/dev/null 2>&1; then
    fuser -k -"$sig" "${port}/tcp" >/dev/null 2>&1 || true
  elif command -v lsof >/dev/null 2>&1; then
    local pids; pids="$(lsof -ti tcp:"$port" 2>/dev/null || true)"
    [ -n "$pids" ] && kill -"$sig" $pids 2>/dev/null || true
  fi
}

# ---------------------------------------------------------------------------
# Python venv + dependency install
# ---------------------------------------------------------------------------
detect_python() {
  if [ -n "$PYTHON_BIN" ] && command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    printf '%s' "$PYTHON_BIN"; return 0
  fi
  local p
  for p in python3.11 python3.12 python3; do
    if command -v "$p" >/dev/null 2>&1; then printf '%s' "$p"; return 0; fi
  done
  return 1
}

ensure_venv() {
  local base_py
  base_py="$(detect_python)" || { err "no python3 found (need 3.11+); set PYTHON_BIN"; exit 1; }

  if [ ! -x "$VENV_DIR/bin/python" ]; then
    info "creating shared venv at $VENV_DIR ($("$base_py" --version 2>&1))"
    "$base_py" -m venv "$VENV_DIR" || { err "venv creation failed"; exit 1; }
  fi

  # Activate: prepend venv bin so python/uvicorn resolve to the shared venv for
  # every child run.sh we launch.
  # shellcheck disable=SC1091
  . "$VENV_DIR/bin/activate"
  PY="$VENV_DIR/bin/python"
  export VIRTUAL_ENV="$VENV_DIR"

  if [ "$PLATFORM_SKIP_INSTALL" = "1" ]; then
    warn "skipping pip install (--skip-install / PLATFORM_SKIP_INSTALL=1)"
    return 0
  fi

  info "installing service requirements into the shared venv"
  "$PY" -m pip install --quiet --upgrade pip >>"$LOG_DIR/pip.log" 2>&1 || \
    warn "pip self-upgrade failed (continuing)"

  local name req
  for entry in "${SERVICES[@]}"; do
    name="${entry%%:*}"
    req="$SERVICES_ROOT/$name/requirements.txt"
    if [ -f "$req" ]; then
      printf '  installing %s deps ... ' "$name"
      if "$PY" -m pip install --quiet -r "$req" >>"$LOG_DIR/pip.log" 2>&1; then
        printf '%sok%s\n' "$c_green" "$c_reset"
      else
        printf '%sFAILED%s (see logs/pip.log)\n' "$c_red" "$c_reset"
      fi
    else
      warn "$name has no requirements.txt yet at $req"
    fi
  done
}

# ---------------------------------------------------------------------------
# Launch + health-wait for one service
# ---------------------------------------------------------------------------
start_service() {
  local name="$1" port="$2"
  local svc_dir="$SERVICES_ROOT/$name"
  local log; log="$(logfile_for "$name")"
  local pf;  pf="$(pidfile_for "$name")"
  local health="http://${HOST}:${port}/health"

  # Already healthy? Adopt it.
  if http_ok "$health"; then
    warn "$name already responding on :$port — adopting, not restarting"
    running_pid "$name" >/dev/null 2>&1 || : # keep any existing pidfile
    return 0
  fi

  if [ ! -d "$svc_dir" ]; then
    err "$name: service dir not found ($svc_dir) — is the sibling built yet?"
    return 1
  fi

  # Choose the launch command. Prefer the service's own run.sh (the contract);
  # fall back to a conventional uvicorn app.main:app if no run.sh exists.
  local runner
  if [ -f "$svc_dir/run.sh" ]; then
    runner="bash run.sh"
  elif [ -d "$svc_dir/app" ]; then
    runner="\"$PY\" -m uvicorn app.main:app --host \"$HOST\" --port \"$port\""
    warn "$name: no run.sh — falling back to 'uvicorn app.main:app'"
  else
    err "$name: neither run.sh nor app/ found in $svc_dir"
    return 1
  fi

  info "starting $name on :$port  (log: ${log#$PLATFORM_DIR/})"

  # Launch in the service dir with PORT set for this service. Everything else
  # (HOST, MODEL_BACKEND, *_URL) is already exported.
  ( cd "$svc_dir" && exec env PORT="$port" bash -c "$runner" ) >"$log" 2>&1 &
  local pid=$!
  printf '%s' "$pid" > "$pf"

  # Wait for /health, bailing early if the process dies.
  local waited=0 step=0.5
  while :; do
    if http_ok "$health"; then
      ok "$name healthy (pid $pid)"
      return 0
    fi
    if ! pid_alive "$pid"; then
      err "$name process exited before becoming healthy — last log lines:"
      tail -n 20 "$log" 2>/dev/null | sed 's/^/      /' >&2
      rm -f "$pf"
      return 1
    fi
    # bash can't sleep fractions portably everywhere, but coreutils sleep can.
    sleep "$step"
    waited="$(awk -v w="$waited" -v s="$step" 'BEGIN{printf "%.1f", w+s}')"
    # timeout check
    if awk -v w="$waited" -v t="$HEALTH_TIMEOUT" 'BEGIN{exit !(w>=t)}'; then
      err "$name did not pass /health within ${HEALTH_TIMEOUT}s — last log lines:"
      tail -n 20 "$log" 2>/dev/null | sed 's/^/      /' >&2
      return 1
    fi
  done
}

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
cmd_up() {
  ensure_venv

  log ""
  info "bringing up the serve-loop (MODEL_BACKEND=${c_bold}${MODEL_BACKEND}${c_reset})"
  local failed=0 name port
  for entry in "${SERVICES[@]}"; do
    name="${entry%%:*}"; port="${entry##*:}"
    if ! start_service "$name" "$port"; then
      failed=1
      err "aborting bring-up: $name failed to start"
      break
    fi
  done

  log ""
  if [ "$failed" = "0" ]; then
    print_checklist
    return 0
  else
    err "bring-up incomplete. Run 'bash run_all.sh --status' to inspect, then '--stop' to clean up."
    return 1
  fi
}

print_checklist() {
  local line
  line="============================================================"
  log "$c_green$line$c_reset"
  log "${c_bold} Nucleus serve-loop MVP (v0.0) is up — text-only walking skeleton${c_reset}"
  log "$c_green$line$c_reset"
  log ""
  log "  ${c_bold}Open the chat surface:${c_reset}  ${c_bold}http://localhost:${INPUT_PORT}${c_reset}"
  log ""
  log "  Services (start order):"
  log "    storage    http://${HOST}:${STORAGE_PORT}/health   (/sessions + model directory)"
  log "    inference  http://${HOST}:${INFERENCE_PORT}/health   (MODEL_BACKEND=${MODEL_BACKEND})"
  log "    output     http://${HOST}:${OUTPUT_PORT}/health   (C9 relay + markdown render)"
  log "    input      http://${HOST}:${INPUT_PORT}/health   (chat surface + QueryBuilder)"
  log ""
  log "  ${c_bold}Send a test turn${c_reset} (streams a C9 body: text, U+001E, JSON end frame):"
  log "    curl -N -X POST http://localhost:${INPUT_PORT}/api/turn \\"
  log "         -H 'Content-Type: application/json' \\"
  log "         -d '{\"text\":\"hello, who are you?\"}'"
  log ""
  log "  Logs:   ${LOG_DIR}/<svc>.log"
  log "  Status: bash run_all.sh --status"
  log "  Stop:   bash run_all.sh --stop"
  log ""
  if [ "$MODEL_BACKEND" = "mock" ]; then
    log "  ${c_dim}Running the MOCK backend (canned stream, no GPU). To go real:${c_reset}"
    log "  ${c_dim}  set MODEL_BACKEND=vllm in .env + run services/inference/serve_vllm.sh on a3mega.${c_reset}"
  fi
}

cmd_stop() {
  info "stopping serve-loop services"
  local name port pf p stopped=0
  # Stop in reverse start order (input first, storage last).
  local i
  for (( i=${#SERVICES[@]}-1; i>=0; i-- )); do
    name="${SERVICES[$i]%%:*}"; port="${SERVICES[$i]##*:}"
    pf="$(pidfile_for "$name")"
    p=""
    [ -f "$pf" ] && p="$(cat "$pf" 2>/dev/null)"
    if pid_alive "$p"; then
      pkill -TERM -P "$p" 2>/dev/null || true   # children first
      kill -TERM "$p" 2>/dev/null || true
      stopped=1
    fi
    # Belt-and-braces: free the port regardless of how run.sh forked.
    kill_port "$port" TERM
  done

  # Grace, then escalate.
  sleep 1
  for (( i=${#SERVICES[@]}-1; i>=0; i-- )); do
    name="${SERVICES[$i]%%:*}"; port="${SERVICES[$i]##*:}"
    pf="$(pidfile_for "$name")"
    p=""
    [ -f "$pf" ] && p="$(cat "$pf" 2>/dev/null)"
    if pid_alive "$p"; then
      pkill -KILL -P "$p" 2>/dev/null || true
      kill -KILL "$p" 2>/dev/null || true
    fi
    kill_port "$port" KILL
    rm -f "$pf"
    ok "$name stopped"
  done
  [ "$stopped" = "0" ] && warn "nothing was running (no live pidfiles)"
  return 0
}

cmd_status() {
  info "serve-loop status"
  printf '  %-10s %-8s %-10s %s\n' "SERVICE" "PORT" "PID" "HEALTH"
  printf '  %-10s %-8s %-10s %s\n' "-------" "----" "---" "------"
  local name port pf p health hstate pstate
  for entry in "${SERVICES[@]}"; do
    name="${entry%%:*}"; port="${entry##*:}"
    pf="$(pidfile_for "$name")"
    p=""; [ -f "$pf" ] && p="$(cat "$pf" 2>/dev/null)"
    if pid_alive "$p"; then pstate="$p"; else pstate="-"; fi
    health="http://${HOST}:${port}/health"
    if http_ok "$health"; then
      hstate="${c_green}up${c_reset}"
    else
      hstate="${c_red}down${c_reset}"
    fi
    printf '  %-10s %-8s %-10s %b\n' "$name" "$port" "$pstate" "$hstate"
  done
}

usage() {
  sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^#\{0,1\} \{0,1\}//'
}

# ---------------------------------------------------------------------------
# Arg parse
# ---------------------------------------------------------------------------
ACTION="up"
while [ $# -gt 0 ]; do
  case "$1" in
    --stop)         ACTION="stop" ;;
    --status|-s)    ACTION="status" ;;
    --restart)      ACTION="restart" ;;
    --skip-install) PLATFORM_SKIP_INSTALL=1 ;;
    -h|--help)      usage; exit 0 ;;
    *) err "unknown argument: $1"; usage; exit 2 ;;
  esac
  shift
done

case "$ACTION" in
  up)      cmd_up ;;
  stop)    cmd_stop ;;
  status)  cmd_status ;;
  restart) cmd_stop; cmd_up ;;
esac
