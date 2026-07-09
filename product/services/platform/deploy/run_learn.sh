#!/usr/bin/env bash
#
# run_learn.sh — platform bring-up for the Nucleus LEARN-loop capture MVP (M0).
#
# Starts the three services of the capture skeleton, in dependency order,
# waiting on each /health before starting the next:
#
#     storage (8083) -> data-processing (8085, ASR_BACKEND=mock) -> recording (8084)
#
# The capture spine: recording carves a continuous audio source into chunks,
# PUTs each blob to storage /raw, and PUSHes a C1 envelope to data-processing,
# which pulls the blob, runs ASR, and writes a C2 record to storage /context.
#
# This is INFRA GLUE ONLY — no application logic lives here. It owns a single
# shared Python venv (separate from the serve loop's), installs each service's
# requirements into it, launches each service via its own run.sh, tracks PIDs so
# it can stop/report the fleet, and (--smoke) triggers one capture run.
#
# It is SEPARATE from the serve-loop bring-up (run_all.sh): different services,
# different env file (learn.env), different venv. Running one does not touch the
# other. (storage :8083 is common to both, so only one loop can be up at a time.)
#
# Usage:
#   bash run_learn.sh            # bring the capture loop up (default)
#   bash run_learn.sh --smoke    # bring up, then trigger one /capture/run + print record_ids
#   bash run_learn.sh --stop     # stop everything this script started (frees the ports)
#   bash run_learn.sh --status   # report per-service pid + /health
#   bash run_learn.sh --restart  # --stop then bring up
#   bash run_learn.sh --skip-install   # bring up without re-running pip install
#   bash run_learn.sh --help
#
# Config: deploy/learn.env (copy from learn.env.example). Any value not set there
# falls back to a built-in default, so the script also runs with no learn.env.
#
# Platform<->service contract (what each sibling run.sh MUST honour):
#   * read HOST and PORT from the environment and bind uvicorn to them;
#   * expose GET /health returning HTTP 200 when ready;
#   * use the active venv on PATH (do not create a private venv);
#   * data-processing additionally reads ASR_BACKEND + STORAGE_URL;
#   * recording additionally reads STORAGE_URL + DP_URL.
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
: "${RUN_DIR:=$DEPLOY_DIR/run-learn}"
: "${VENV_DIR:=$DEPLOY_DIR/.venv-learn}"
: "${ENV_FILE:=$DEPLOY_DIR/learn.env}"

mkdir -p "$LOG_DIR" "$RUN_DIR"

# ---------------------------------------------------------------------------
# Config: source learn.env if present, then apply defaults for anything unset.
# learn.env is the operator knob, so it wins over the ambient shell for these
# keys (source with `set -a` so its assignments export).
# ---------------------------------------------------------------------------
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

: "${HOST:=127.0.0.1}"
: "${ASR_BACKEND:=mock}"

: "${STORAGE_PORT:=8083}"
: "${DP_PORT:=8085}"
: "${RECORDING_PORT:=8084}"

: "${STORAGE_URL:=http://${HOST}:${STORAGE_PORT}}"
: "${DP_URL:=http://${HOST}:${DP_PORT}}"
: "${RECORDING_URL:=http://${HOST}:${RECORDING_PORT}}"

: "${HEALTH_TIMEOUT:=90}"          # seconds to wait per service /health
: "${SERVICES_ROOT:=$DEFAULT_SERVICES_ROOT}"   # override for self-test
: "${PYTHON_BIN:=}"                # explicit interpreter; auto-detected if empty
: "${PLATFORM_SKIP_INSTALL:=0}"    # 1 = skip pip install (also --skip-install)

# Smoke config (used by --smoke).
: "${SAMPLE_WAV:=$DEPLOY_DIR/sample/sample_audio.wav}"
: "${SAMPLE_SECONDS:=12}"
: "${CHUNK_SECONDS:=5}"

# Interpreter used by http_ok's no-curl fallback before/without a venv build;
# ensure_venv re-points this at the shared venv python.
PY="$(command -v python3 || command -v python || printf 'python3')"

# Export the wiring so every child (and its run.sh) inherits it.
export HOST ASR_BACKEND
export STORAGE_URL DP_URL RECORDING_URL
export STORAGE_PORT DP_PORT RECORDING_PORT

# Service table — START ORDER matters (dependencies first).
# recording depends on both storage (/raw) and data-processing (/ingest);
# data-processing depends on storage (/raw pull + /context write).
#   name:port
SERVICES=(
  "storage:${STORAGE_PORT}"
  "data-processing:${DP_PORT}"
  "recording:${RECORDING_PORT}"
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
logfile_for() { printf '%s/%s.log' "$LOG_DIR" "learn-$1"; }

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
    info "creating shared learn-loop venv at $VENV_DIR ($("$base_py" --version 2>&1))"
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

  info "installing service requirements into the shared learn-loop venv"
  "$PY" -m pip install --quiet --upgrade pip >>"$LOG_DIR/pip-learn.log" 2>&1 || \
    warn "pip self-upgrade failed (continuing)"

  local name req
  for entry in "${SERVICES[@]}"; do
    name="${entry%%:*}"
    req="$SERVICES_ROOT/$name/requirements.txt"
    if [ -f "$req" ]; then
      printf '  installing %s deps ... ' "$name"
      if "$PY" -m pip install --quiet -r "$req" >>"$LOG_DIR/pip-learn.log" 2>&1; then
        printf '%sok%s\n' "$c_green" "$c_reset"
      else
        printf '%sFAILED%s (see logs/pip-learn.log)\n' "$c_red" "$c_reset"
      fi
    else
      warn "$name has no requirements.txt yet at $req (sibling not built? — will fail to start)"
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
    return 0
  fi

  if [ ! -d "$svc_dir" ]; then
    err "$name: service dir not found ($svc_dir) — is the sibling built yet?"
    return 1
  fi

  # Prefer the service's own run.sh (the contract); fall back to a conventional
  # uvicorn app.main:app if no run.sh exists.
  local runner
  if [ -f "$svc_dir/run.sh" ]; then
    runner="bash run.sh"
  elif [ -d "$svc_dir/app" ]; then
    runner="\"$PY\" -m uvicorn app.main:app --host \"$HOST\" --port \"$port\""
    warn "$name: no run.sh — falling back to 'uvicorn app.main:app'"
  else
    err "$name: neither run.sh nor app/ found in $svc_dir (sibling not built yet?)"
    return 1
  fi

  info "starting $name on :$port  (log: ${log#$PLATFORM_DIR/})"

  # Launch in the service dir with PORT set for this service. Everything else
  # (HOST, ASR_BACKEND, *_URL) is already exported.
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
    sleep "$step"
    waited="$(awk -v w="$waited" -v s="$step" 'BEGIN{printf "%.1f", w+s}')"
    if awk -v w="$waited" -v t="$HEALTH_TIMEOUT" 'BEGIN{exit !(w>=t)}'; then
      err "$name did not pass /health within ${HEALTH_TIMEOUT}s — last log lines:"
      tail -n 20 "$log" 2>/dev/null | sed 's/^/      /' >&2
      return 1
    fi
  done
}

# ---------------------------------------------------------------------------
# Smoke: trigger one capture run and print the resulting record_ids.
# ---------------------------------------------------------------------------
ensure_sample_wav() {
  if [ -f "$SAMPLE_WAV" ]; then
    return 0
  fi
  mkdir -p "$(dirname "$SAMPLE_WAV")"
  info "generating sample WAV ($SAMPLE_SECONDS s) at ${SAMPLE_WAV#$PLATFORM_DIR/}"
  if ! "$PY" "$DEPLOY_DIR/make_sample_wav.py" "$SAMPLE_WAV" "$SAMPLE_SECONDS" \
        >>"$LOG_DIR/learn-smoke.log" 2>&1; then
    err "failed to generate sample WAV — see logs/learn-smoke.log"
    return 1
  fi
}

run_smoke() {
  ensure_sample_wav || return 1

  log ""
  info "smoke: triggering recording /capture/run"
  log "  source        : $SAMPLE_WAV"
  log "  chunk_seconds : $CHUNK_SECONDS"
  log "  dp_url        : $DP_URL"
  log "  storage_url   : $STORAGE_URL"
  log ""

  # POST {source, chunk_seconds, dp_url, storage_url} to recording /capture/run,
  # then extract every record_id in the response (nested-safe). Uses stdlib only
  # so it needs neither curl nor jq. Exit non-zero if the POST is not 2xx.
  "$PY" - "$RECORDING_URL/capture/run" "$SAMPLE_WAV" "$CHUNK_SECONDS" "$DP_URL" "$STORAGE_URL" <<'PY'
import json, sys, urllib.request

url, source, chunk_seconds, dp_url, storage_url = sys.argv[1:6]
body = json.dumps({
    "source": source,
    "chunk_seconds": int(chunk_seconds),
    "dp_url": dp_url,
    "storage_url": storage_url,
}).encode()

req = urllib.request.Request(
    url, data=body, method="POST",
    headers={"Content-Type": "application/json"},
)
try:
    resp = urllib.request.urlopen(req, timeout=120)
    status = resp.status
    raw = resp.read()
except urllib.error.HTTPError as e:
    status = e.code
    raw = e.read()
except Exception as e:
    print(f"  [fail] POST {url} errored: {e}")
    sys.exit(1)

text = raw.decode("utf-8", "replace")
print(f"  HTTP {status} from {url}")
print("  response body:")
for line in (text if text.strip() else "(empty)").splitlines() or ["(empty)"]:
    print(f"    {line}")

# Recursively collect record_id values wherever they appear in the response.
def find_record_ids(obj, out):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "record_id" and isinstance(v, str):
                out.append(v)
            elif k == "record_ids" and isinstance(v, list):
                out.extend(x for x in v if isinstance(x, str))
            else:
                find_record_ids(v, out)
    elif isinstance(obj, list):
        for v in obj:
            find_record_ids(v, out)

found = []
try:
    find_record_ids(json.loads(text), found)
except Exception:
    pass
# A response may carry each record_id in more than one place (e.g. both a
# chunks[].record_id and a convenience record_ids[]). Dedupe by value,
# preserving first-seen order — record_id is unique per record.
seen = set()
ids = []
for rid in found:
    if rid not in seen:
        seen.add(rid)
        ids.append(rid)

print("")
if ids:
    print(f"  record_ids ({len(ids)}):")
    for rid in ids:
        print(f"    record_id: {rid}")
else:
    print("  record_ids: (none found in response — inspect the body above)")

# The E2E assertion (blobs in /raw, C2 re-readable by record_id and by time)
# is the integrator's; this trigger just proves the capture run fires and
# surfaces what came back.
sys.exit(0 if 200 <= status < 300 else 1)
PY
}

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
cmd_up() {
  ensure_venv

  log ""
  info "bringing up the learn loop (capture skeleton, ASR_BACKEND=${c_bold}${ASR_BACKEND}${c_reset})"
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
    err "bring-up incomplete. Run 'bash run_learn.sh --status' to inspect, then '--stop' to clean up."
    return 1
  fi
}

print_checklist() {
  local line
  line="============================================================"
  log "$c_green$line$c_reset"
  log "${c_bold} Nucleus learn-loop capture MVP (M0) is up — capture skeleton${c_reset}"
  log "$c_green$line$c_reset"
  log ""
  log "  Services (start order):"
  log "    storage          http://${HOST}:${STORAGE_PORT}/health   (/raw blobs + /context C2)"
  log "    data-processing  http://${HOST}:${DP_PORT}/health   (POST /ingest; ASR_BACKEND=${ASR_BACKEND})"
  log "    recording        http://${HOST}:${RECORDING_PORT}/health   (capturer + POST /capture/run)"
  log ""
  log "  ${c_bold}Drive one capture run${c_reset} (carves the sample WAV into ${CHUNK_SECONDS}s chunks, C1->ASR->C2):"
  log "    bash run_learn.sh --smoke"
  log "  or by hand:"
  log "    curl -sS -X POST ${RECORDING_URL}/capture/run \\"
  log "         -H 'Content-Type: application/json' \\"
  log "         -d '{\"source\":\"${SAMPLE_WAV}\",\"chunk_seconds\":${CHUNK_SECONDS},\"dp_url\":\"${DP_URL}\",\"storage_url\":\"${STORAGE_URL}\"}'"
  log ""
  log "  Read a C2 back (integrator's E2E check):"
  log "    curl -s ${STORAGE_URL}/context/records/<record_id>"
  log "    curl -s '${STORAGE_URL}/context/records?user_id=<uid>&from=<t>&to=<t>'"
  log ""
  log "  Logs:   ${LOG_DIR}/learn-<svc>.log"
  log "  Status: bash run_learn.sh --status"
  log "  Stop:   bash run_learn.sh --stop"
  log ""
  if [ "$ASR_BACKEND" = "mock" ]; then
    log "  ${c_dim}Running the MOCK ASR backend (canned transcript, no GPU/torch). To go real:${c_reset}"
    log "  ${c_dim}  set ASR_BACKEND=faster_whisper in learn.env + restart (slow on CPU).${c_reset}"
  fi
}

cmd_stop() {
  info "stopping learn-loop services"
  local name port pf p stopped=0 i
  # Stop in reverse start order (recording first, storage last).
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
  info "learn-loop status"
  printf '  %-16s %-8s %-10s %s\n' "SERVICE" "PORT" "PID" "HEALTH"
  printf '  %-16s %-8s %-10s %s\n' "-------" "----" "---" "------"
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
    printf '  %-16s %-8s %-10s %b\n' "$name" "$port" "$pstate" "$hstate"
  done
}

usage() {
  cat <<'EOF'
run_learn.sh — platform bring-up for the Nucleus LEARN-loop capture MVP (M0).

Starts, health-gated in dependency order:
  storage (8083) -> data-processing (8085, ASR_BACKEND=mock) -> recording (8084)

Usage:
  bash run_learn.sh            # bring the capture loop up (default)
  bash run_learn.sh --smoke    # bring up, then trigger one /capture/run + print record_ids
  bash run_learn.sh --stop     # stop everything this script started (frees the ports)
  bash run_learn.sh --status   # report per-service pid + /health
  bash run_learn.sh --restart  # --stop then bring up
  bash run_learn.sh --skip-install   # bring up without re-running pip install
  bash run_learn.sh --help

Config: deploy/learn.env (copy from learn.env.example); built-in defaults cover
anything unset, so it also runs with no learn.env.
EOF
}

# ---------------------------------------------------------------------------
# Arg parse
# ---------------------------------------------------------------------------
ACTION="up"
while [ $# -gt 0 ]; do
  case "$1" in
    --smoke)        ACTION="smoke" ;;
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
  smoke)   cmd_up && run_smoke ;;
  stop)    cmd_stop ;;
  status)  cmd_status ;;
  restart) cmd_stop; cmd_up ;;
esac
