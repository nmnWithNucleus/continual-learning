#!/usr/bin/env bash
# Headless demo of the nightly loop: venv bootstrap -> tests -> one synthetic
# night on the mock backend (no GPU, no storage service needed).
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  python3 -m venv .venv
  ./.venv/bin/pip install -q -r requirements.txt
fi

./.venv/bin/python -m pytest -q
echo
echo "=== synthetic night (mock backend) ==="
./.venv/bin/python -m app.nightly --user demo --tz America/Los_Angeles \
  --date "$(date -d yesterday +%F)" --synthetic
