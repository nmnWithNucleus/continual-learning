#!/usr/bin/env bash
#
# run_selftest.sh — control-plane smoke for run_all.sh.
#
# Drives run_all.sh against stdlib-only FAKE services (selftest/fake/*) on a
# private port set, with pip install skipped. Verifies: ordered start + /health
# gating, --status reporting up, a streamed C9 turn through input /api/turn, and
# a clean --stop. No sibling code, no venv installs, no network needed.
#
set -u

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(dirname "$SELF_DIR")"
RUN_ALL="$DEPLOY_DIR/run_all.sh"
FAKE_ROOT="$SELF_DIR/fake"
WORK="$SELF_DIR/.run"          # isolated logs/pids/venv for the test
rm -rf "$WORK"; mkdir -p "$WORK"

# Isolated, non-conflicting config for run_all.sh.
export SERVICES_ROOT="$FAKE_ROOT"
export PLATFORM_SKIP_INSTALL=1
export LOG_DIR="$WORK/logs"
export RUN_DIR="$WORK/run"
export VENV_DIR="$WORK/venv"
export ENV_FILE="$WORK/nonexistent.env"   # force built-in defaults
export HEALTH_TIMEOUT=15
export HOST=127.0.0.1
export STORAGE_PORT=18083 INFERENCE_PORT=18010 OUTPUT_PORT=18082 INPUT_PORT=18081
export STORAGE_URL="http://127.0.0.1:18083"
export INFERENCE_URL="http://127.0.0.1:18010"
export OUTPUT_URL="http://127.0.0.1:18082"
export INPUT_URL="http://127.0.0.1:18081"

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

cleanup() { bash "$RUN_ALL" --stop >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "== platform self-test: bringing up fake serve-loop =="
if bash "$RUN_ALL" > "$WORK/up.log" 2>&1; then
  echo "  PASS  run_all.sh up exited 0"; pass=$((pass+1))
else
  echo "  FAIL  run_all.sh up exited non-zero"; fail=$((fail+1))
  sed 's/^/    | /' "$WORK/up.log"
fi

echo "== checking each service came up healthy =="
for p in 18083 18010 18082 18081; do
  check "/health up on :$p" curl -fsS -m 3 "http://127.0.0.1:$p/health"
done

echo "== checking ordered-start evidence in up.log =="
# storage must be reported healthy before input in the log.
if [ -f "$WORK/up.log" ]; then
  s_line="$(grep -n 'storage healthy' "$WORK/up.log" | head -1 | cut -d: -f1)"
  i_line="$(grep -n 'input healthy'   "$WORK/up.log" | head -1 | cut -d: -f1)"
  if [ -n "$s_line" ] && [ -n "$i_line" ] && [ "$s_line" -lt "$i_line" ]; then
    echo "  PASS  storage healthy before input (ordered start)"; pass=$((pass+1))
  else
    echo "  FAIL  ordered-start evidence missing (storage=$s_line input=$i_line)"; fail=$((fail+1))
  fi
fi

echo "== checking --status reports all up =="
bash "$RUN_ALL" --status > "$WORK/status.log" 2>&1
up_count="$(grep -c 'up' "$WORK/status.log" 2>/dev/null || echo 0)"
if [ "$up_count" -ge 4 ]; then echo "  PASS  --status shows >=4 up"; pass=$((pass+1));
else echo "  FAIL  --status showed $up_count up"; fail=$((fail+1)); sed 's/^/    | /' "$WORK/status.log"; fi

echo "== checking a streamed C9 turn through input /api/turn =="
curl -N -sS -m 5 -X POST "http://127.0.0.1:18081/api/turn" \
     -H 'Content-Type: application/json' \
     -d '{"text":"hi"}' > "$WORK/turn.bin" 2>/dev/null
grep_check "turn body has answer text" "Hello from the fake" "$WORK/turn.bin"
# Split on the U+001E separator and validate the end frame is JSON with C9 fields.
if python3 - "$WORK/turn.bin" <<'PY'
import sys, json
data = open(sys.argv[1], "rb").read()
sep = data.split(b"\x1e", 1)
assert len(sep) == 2, "no U+001E separator in stream"
answer, frame = sep
end = json.loads(frame.decode())
assert end["contract"] == "C9" and end["version"] == "0" and end["finished"] is True
assert end["adapter"] == "base"
print("ok")
PY
then echo "  PASS  C9 end frame parses (sep + contract/version/finished)"; pass=$((pass+1));
else echo "  FAIL  C9 end frame invalid"; fail=$((fail+1)); fi

echo "== checking --stop tears everything down =="
bash "$RUN_ALL" --stop > "$WORK/stop.log" 2>&1
sleep 1
down=0
for p in 18083 18010 18082 18081; do
  if curl -fsS -m 2 "http://127.0.0.1:$p/health" >/dev/null 2>&1; then
    echo "  FAIL  :$p still up after --stop"; fail=$((fail+1)); down=1
  fi
done
[ "$down" = "0" ] && { echo "  PASS  all ports down after --stop"; pass=$((pass+1)); }

echo ""
echo "== self-test summary: $pass passed, $fail failed =="
[ "$fail" = "0" ] || exit 1
