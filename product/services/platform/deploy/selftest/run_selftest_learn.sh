#!/usr/bin/env bash
#
# run_selftest_learn.sh — control-plane smoke for run_learn.sh.
#
# Drives run_learn.sh against stdlib-only FAKE services (selftest/fake_learn/*)
# on a private port set, with pip install skipped. Verifies: ordered start +
# /health gating (storage -> data-processing -> recording), --status reporting
# up, --smoke generating the sample WAV + triggering /capture/run + printing
# record_ids, and a clean --stop. No sibling code, no venv installs, no network.
#
# The real recording / data-processing / storage-/raw+/context services are
# built by parallel workstreams; this proves the bring-up GLUE independently of
# them, exactly as run_selftest.sh does for the serve loop.
#
set -u

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(dirname "$SELF_DIR")"
RUN_LEARN="$DEPLOY_DIR/run_learn.sh"
FAKE_ROOT="$SELF_DIR/fake_learn"
WORK="$SELF_DIR/.run-learn"        # isolated logs/pids/venv/sample for the test
rm -rf "$WORK"; mkdir -p "$WORK"

# Isolated, non-conflicting config for run_learn.sh.
export SERVICES_ROOT="$FAKE_ROOT"
export PLATFORM_SKIP_INSTALL=1
export LOG_DIR="$WORK/logs"
export RUN_DIR="$WORK/run"
export VENV_DIR="$WORK/venv"
export ENV_FILE="$WORK/nonexistent.env"   # force built-in defaults
export HEALTH_TIMEOUT=15
export HOST=127.0.0.1
export STORAGE_PORT=18083 DP_PORT=18085 RECORDING_PORT=18084
export STORAGE_URL="http://127.0.0.1:18083"
export DP_URL="http://127.0.0.1:18085"
export RECORDING_URL="http://127.0.0.1:18084"
export SAMPLE_WAV="$WORK/sample.wav"
export SAMPLE_SECONDS=12
export CHUNK_SECONDS=5

pass=0; fail=0
check() { # check "desc" <cmd...>  (passes if cmd exits 0)
  local desc="$1"; shift
  if "$@" >/dev/null 2>&1; then echo "  PASS  $desc"; pass=$((pass+1));
  else echo "  FAIL  $desc"; fail=$((fail+1)); fi
}
grep_check() { # grep_check "desc" pattern file
  local desc="$1" pat="$2" file="$3"
  if grep -q "$pat" "$file" 2>/dev/null; then echo "  PASS  $desc"; pass=$((pass+1));
  else echo "  FAIL  $desc"; fail=$((fail+1)); fi
}

cleanup() { bash "$RUN_LEARN" --stop >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "== learn-loop self-test: bringing up fake capture loop =="
if bash "$RUN_LEARN" > "$WORK/up.log" 2>&1; then
  echo "  PASS  run_learn.sh up exited 0"; pass=$((pass+1))
else
  echo "  FAIL  run_learn.sh up exited non-zero"; fail=$((fail+1))
  sed 's/^/    | /' "$WORK/up.log"
fi

echo "== checking each service came up healthy =="
for p in 18083 18085 18084; do
  check "/health up on :$p" curl -fsS -m 3 "http://127.0.0.1:$p/health"
done

echo "== checking data-processing /health advertises asr_backend=mock =="
if curl -fsS -m 3 "http://127.0.0.1:18085/health" 2>/dev/null | grep -q '"asr_backend": *"mock"'; then
  echo "  PASS  data-processing reports asr_backend=mock"; pass=$((pass+1))
else
  echo "  FAIL  data-processing did not report asr_backend=mock"; fail=$((fail+1))
fi

echo "== checking ordered-start evidence in up.log =="
# storage must be reported healthy before recording in the log.
if [ -f "$WORK/up.log" ]; then
  s_line="$(grep -n 'storage healthy'   "$WORK/up.log" | head -1 | cut -d: -f1)"
  r_line="$(grep -n 'recording healthy' "$WORK/up.log" | head -1 | cut -d: -f1)"
  if [ -n "$s_line" ] && [ -n "$r_line" ] && [ "$s_line" -lt "$r_line" ]; then
    echo "  PASS  storage healthy before recording (ordered start)"; pass=$((pass+1))
  else
    echo "  FAIL  ordered-start evidence missing (storage=$s_line recording=$r_line)"; fail=$((fail+1))
  fi
fi

echo "== checking --status reports all up =="
bash "$RUN_LEARN" --status > "$WORK/status.log" 2>&1
up_count="$(grep -c ' up' "$WORK/status.log" 2>/dev/null || echo 0)"
if [ "$up_count" -ge 3 ]; then echo "  PASS  --status shows >=3 up"; pass=$((pass+1));
else echo "  FAIL  --status showed $up_count up"; fail=$((fail+1)); sed 's/^/    | /' "$WORK/status.log"; fi

echo "== checking --smoke generates sample WAV, fires /capture/run, prints record_ids =="
bash "$RUN_LEARN" --smoke > "$WORK/smoke.log" 2>&1
check "sample WAV was generated" test -f "$SAMPLE_WAV"
grep_check "smoke got HTTP 200 from /capture/run" "HTTP 200" "$WORK/smoke.log"
grep_check "smoke printed at least one record_id" "record_id: rec-selftest-" "$WORK/smoke.log"
# 12s / 5s = 3 dense chunks -> 3 record_ids (seq 0,1,2).
rid_count="$(grep -c 'record_id: rec-selftest-' "$WORK/smoke.log" 2>/dev/null || echo 0)"
if [ "$rid_count" -eq 3 ]; then echo "  PASS  3 record_ids (12s/5s => chunks 0,1,2)"; pass=$((pass+1));
else echo "  FAIL  expected 3 record_ids, got $rid_count"; fail=$((fail+1)); sed 's/^/    | /' "$WORK/smoke.log"; fi

echo "== checking --stop tears everything down =="
bash "$RUN_LEARN" --stop > "$WORK/stop.log" 2>&1
sleep 1
down=0
for p in 18083 18085 18084; do
  if curl -fsS -m 2 "http://127.0.0.1:$p/health" >/dev/null 2>&1; then
    echo "  FAIL  :$p still up after --stop"; fail=$((fail+1)); down=1
  fi
done
[ "$down" = "0" ] && { echo "  PASS  all ports down after --stop"; pass=$((pass+1)); }

echo ""
echo "== learn-loop self-test summary: $pass passed, $fail failed =="
[ "$fail" = "0" ] || exit 1
