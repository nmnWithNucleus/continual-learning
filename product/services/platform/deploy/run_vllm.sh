#!/usr/bin/env bash
#
# run_vllm.sh — platform bring-up for the DP captioner VLM.
#
# Serves Qwen/Qwen3-VL-32B-Instruct behind an OpenAI-compatible endpoint for
# data-processing's clipcap stage (POST /v1/chat/completions with K stills per
# clip, D-02). One vLLM process, tensor-parallel across two GPUs, loopback only.
#
# THE PINS ARE CODE, NOT KNOBS. Model name and weights revision are constants
# here for the same reason clipcap pins the model name and the servers/ manifest
# pins every fleet revision: an operator must not be able to change what model
# captions the corpus by editing an env file. What the environment may set is
# operational placement only (port, GPUs, log/run dirs).
#
# This endpoint is deliberately NOT under servers/manifest.json identity
# verification: it speaks the OpenAI wire, not the fleet's
# /infer envelope. Its identity check is DP's boot probe — create_app asserts
# GET /v1/models contains clipcap's pinned model name — plus this script's own
# health wait, which requires the same thing before reporting "up".
#
# Usage:
#   bash run_vllm.sh            # bring the VLM up (idempotent: adopts a healthy one)
#   bash run_vllm.sh --stop     # stop the VLM this script started (frees the port)
#   bash run_vllm.sh --status   # pid + /v1/models health
#   bash run_vllm.sh --restart  # --stop then bring up
#   bash run_vllm.sh --help
#
set -u

# ---------------------------------------------------------------------------
# Operational config (env-overridable, learn.env sourced like run_learn.sh)
# ---------------------------------------------------------------------------
DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${ENV_FILE:=$DEPLOY_DIR/learn.env}"
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

# ---------------------------------------------------------------------------
# Pins (code, reviewed — see header). Assigned AFTER the learn.env sourcing,
# deliberately: an env-file line must not be able to override what model or
# revision this launcher serves, because that would change every caption in the
# training corpus without moving `pipeline_version`. The guard is
# `test_run_vllm_pins.py`. Bumping the revision is a dialect-adjacent change that
# re-runs the caption smoke, not an env edit.
# ---------------------------------------------------------------------------
VLM_MODEL="Qwen/Qwen3-VL-32B-Instruct"
VLM_REVISION="0cfaf48183f594c314753d30a4c4974bc75f3ccb"

: "${VLLM_HOST:=127.0.0.1}"        # loopback only — never a bind-all default
: "${VLLM_PORT:=8161}"             # fleet port map: servers 8121-8152, VLM 8161
: "${VLLM_GPUS:=0,1}"              # tensor-parallel pair; fleet manifest owns 2-7
: "${VLLM_MAX_MODEL_LEN:=32768}"   # bounds KV-cache planning; clipcap sends K stills + text, well inside
: "${LOG_DIR:=$DEPLOY_DIR/logs}"
: "${RUN_DIR:=$DEPLOY_DIR/run-vllm}"
: "${VENV_DIR:=$DEPLOY_DIR/.venv-vllm}"
: "${VLLM_HEALTH_TIMEOUT:=900}"    # 63 GB of weights + TP warmup: minutes, not seconds

mkdir -p "$LOG_DIR" "$RUN_DIR"
LOG_FILE="$LOG_DIR/vllm.log"
PID_FILE="$RUN_DIR/vllm.pid"
BASE_URL="http://${VLLM_HOST}:${VLLM_PORT}"

c_reset=""; c_bold=""; c_green=""; c_red=""; c_yellow=""
if [ -t 1 ]; then
  c_reset="$(printf '\033[0m')"; c_bold="$(printf '\033[1m')"
  c_green="$(printf '\033[32m')"; c_red="$(printf '\033[31m')"
  c_yellow="$(printf '\033[33m')"
fi
info() { printf '%s->%s %s\n' "$c_bold" "$c_reset" "$*"; }
ok()   { printf '  %s[ ok ]%s %s\n' "$c_green" "$c_reset" "$*"; }
warn() { printf '  %s[warn]%s %s\n' "$c_yellow" "$c_reset" "$*"; }
err()  { printf '  %s[fail]%s %s\n' "$c_red" "$c_reset" "$*" >&2; }

pid_alive() { [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null; }

# Healthy means: /v1/models answers 200 AND lists the pinned model id. A vLLM
# serving the wrong weights on the right port must read as DOWN here, for the
# same reason DP's boot probe exists.
models_ok() {
  curl -fsS -m 5 "$BASE_URL/v1/models" 2>/dev/null | grep -q "\"$VLM_MODEL\""
}

cmd_up() {
  if models_ok; then
    warn "vLLM already serving $VLM_MODEL on :$VLLM_PORT — adopting, not restarting"
    return 0
  fi
  if [ ! -x "$VENV_DIR/bin/vllm" ]; then
    err "no vllm in $VENV_DIR — create it: python3.12 -m venv $VENV_DIR && $VENV_DIR/bin/pip install vllm==0.11.2"
    return 1
  fi
  info "starting vLLM $VLM_MODEL@$(printf '%.8s' "$VLM_REVISION") on :$VLLM_PORT (GPUs $VLLM_GPUS, TP2)  log: ${LOG_FILE#$DEPLOY_DIR/}"
  ( exec env CUDA_VISIBLE_DEVICES="$VLLM_GPUS" \
      "$VENV_DIR/bin/vllm" serve "$VLM_MODEL" \
      --revision "$VLM_REVISION" \
      --served-model-name "$VLM_MODEL" \
      --tensor-parallel-size 2 \
      --host "$VLLM_HOST" --port "$VLLM_PORT" \
      --max-model-len "$VLLM_MAX_MODEL_LEN" \
  ) >>"$LOG_FILE" 2>&1 &
  local pid=$!
  printf '%s' "$pid" > "$PID_FILE"

  local waited=0 step=5
  while :; do
    if models_ok; then
      ok "vLLM healthy (pid $pid): /v1/models lists $VLM_MODEL"
      return 0
    fi
    if ! pid_alive "$pid"; then
      err "vLLM exited before becoming healthy — last log lines:"
      tail -n 25 "$LOG_FILE" 2>/dev/null | sed 's/^/      /' >&2
      rm -f "$PID_FILE"
      return 1
    fi
    sleep "$step"
    waited=$((waited + step))
    if [ "$waited" -ge "$VLLM_HEALTH_TIMEOUT" ]; then
      err "vLLM not healthy within ${VLLM_HEALTH_TIMEOUT}s — still loading? see $LOG_FILE"
      return 1
    fi
  done
}

cmd_stop() {
  local p=""
  [ -f "$PID_FILE" ] && p="$(cat "$PID_FILE" 2>/dev/null)"
  if pid_alive "$p"; then
    info "stopping vLLM (pid $p)"
    pkill -TERM -P "$p" 2>/dev/null || true
    kill -TERM "$p" 2>/dev/null || true
    local waited=0
    while pid_alive "$p" && [ "$waited" -lt 30 ]; do sleep 1; waited=$((waited+1)); done
    if pid_alive "$p"; then
      warn "TERM did not land in ${waited}s — escalating to KILL"
      pkill -KILL -P "$p" 2>/dev/null || true
      kill -KILL "$p" 2>/dev/null || true
    fi
    ok "vLLM stopped"
  else
    warn "nothing was running (no live pidfile)"
  fi
  rm -f "$PID_FILE"
}

cmd_status() {
  local p=""; [ -f "$PID_FILE" ] && p="$(cat "$PID_FILE" 2>/dev/null)"
  printf '  pins: %s @ %.8s (code, not env)\n' "$VLM_MODEL" "$VLM_REVISION"
  printf '  %-8s %-10s %s\n' "PORT" "PID" "MODELS"
  if models_ok; then
    printf '  %-8s %-10s %b\n' "$VLLM_PORT" "${p:--}" "${c_green}up${c_reset} ($VLM_MODEL)"
  else
    printf '  %-8s %-10s %b\n' "$VLLM_PORT" "${p:--}" "${c_red}down${c_reset}"
  fi
}

usage() {
  sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

ACTION="up"
while [ $# -gt 0 ]; do
  case "$1" in
    --stop)      ACTION="stop" ;;
    --status|-s) ACTION="status" ;;
    --restart)   ACTION="restart" ;;
    -h|--help)   usage; exit 0 ;;
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
