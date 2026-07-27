#!/usr/bin/env bash
# Headless demo of the nightly loop: venv bootstrap -> tests -> one real night on
# the mock backend (no GPU).
#
# The night is no longer fully headless, and that is the D18 cutover rather than a
# regression: the training WINDOW is opened by storage (`POST /training/windows`,
# an idempotent get-or-create over the ingest-time watermark) and the timezone
# comes from the user's C12 profile. Continuum mints neither, and there is no
# default timezone anywhere. So the demo night needs a storage service; the test
# suite does not, and still runs everywhere.
#
# WHAT THIS SCRIPT USED TO PRINT, AND WHY IT COULD NOT WORK (measured, 2026-07-27).
# It told you to write a profile and then run `--synthetic`, and that sequence ends
# in `httpx.HTTPStatusError: Client error '409 Conflict' for url .../training/windows`
# — because a training window starts at the user's `last_trained_t` or, for a
# never-trained user, their EARLIEST INGEST TIME, and a user with no `/context`
# records has neither. `--synthetic` does not substitute for that: it replaces the
# DAY-LOG, not the WINDOW. So the demo needs THREE preconditions, not one, and this
# script now establishes all three instead of naming one of them:
#
#   (1) a C12 profile      — home_tz is declared, never inferred (D17)
#   (2) an ingest history  — at least one /context record, or there is no instant
#                            to start the window at
#   (3) enough elapsed time — `t_end` is `now - delta` (storage's in-flight-write
#                            guard, 60 s by default), so a window over records
#                            written seconds ago is empty and storage refuses it
#
# It also runs the REAL path rather than `--synthetic`: the seeded records are in
# storage, so storage materializes the day-log and the demo exercises the C10 fetch
# that the cutover is actually about.
set -euo pipefail
cd "$(dirname "$0")"

STORAGE_URL="${STORAGE_URL:-http://localhost:8083}"
DEMO_USER="${DEMO_USER:-demo}"
DEMO_TZ="${DEMO_TZ:-America/Los_Angeles}"
DEMO_WAIT="${DEMO_WAIT:-150}"      # seconds to wait out storage's window delta

if [ ! -d .venv ]; then
  python3 -m venv .venv
  ./.venv/bin/pip install -q -r requirements.txt
fi

./.venv/bin/python -m pytest -q
echo

if ! curl -fsS --max-time 2 "${STORAGE_URL}/health" >/dev/null 2>&1; then
  echo "=== night SKIPPED: no storage at ${STORAGE_URL} ==="
  echo "The window is opened by storage and home_tz comes from the C12 profile"
  echo "(D18) -- there is no local minter and no default timezone. To run it by hand:"
  echo "  1. start storage:   (cd ../storage && ./run.sh)"
  echo "  2. declare the zone (there is no default; a user with no home_tz is not"
  echo "     schedulable, which is an alert, never a silent skip):"
  echo "       curl -X PUT ${STORAGE_URL}/users/${DEMO_USER}/profile \\"
  echo "            -H 'content-type: application/json' \\"
  echo "            -d '{\"home_tz\": \"${DEMO_TZ}\"}'"
  echo "  3. give ${DEMO_USER} an INGEST HISTORY -- POST at least one C2 record to"
  echo "     ${STORAGE_URL}/context/records. Without it there is no instant to start"
  echo "     the window at and POST /training/windows answers 409 'no ingest"
  echo "     history'. --synthetic does NOT stand in for this: it replaces the"
  echo "     day-log, not the window."
  echo "  4. wait until those records are older than storage's window delta"
  echo "     (STORAGE_WINDOW_DELTA_SECONDS, 60 s by default) -- until then the"
  echo "     window [first_ingest, now-delta) is empty and storage refuses it."
  echo "  5. ./.venv/bin/python -m app.nightly --user ${DEMO_USER}"
  echo "Or just start storage and re-run this script: it does 2-5 for you."
  exit 0
fi

echo "=== demo prerequisites against ${STORAGE_URL} ==="
STORAGE_URL="$STORAGE_URL" ./.venv/bin/python - \
    "$STORAGE_URL" "$DEMO_USER" "$DEMO_TZ" <<'PY'
"""Establish (1) the C12 profile and (2) an ingest history for the demo user.

Seeds a synthetic day through the SAME generator `--synthetic` uses, but writes it
to storage rather than rendering it in-process — which is the point of the cutover:
storage materializes the day-log, continuum fetches it.
"""
import sys
from datetime import datetime, timedelta, timezone

import httpx

from app.synth import synth_records
from app.window import Window

base, user, tz = sys.argv[1], sys.argv[2], sys.argv[3]

resp = httpx.put(f"{base}/users/{user}/profile", json={"home_tz": tz}, timeout=30)
resp.raise_for_status()
print(f"  profile   home_tz={resp.json()['home_tz']} (declared, never inferred)")

have = httpx.get(f"{base}/context/records", params={"user_id": user},
                 timeout=30).json()
if have:
    print(f"  records   {len(have)} already present -- seeding nothing")
else:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    # A throwaway 24 h EVENT-time span, used only to give the generated records
    # plausible t_start values. It is not a training window and nothing mints one
    # here: membership in the real window is by storage-assigned ingest_time.
    span = Window(window_id="w20260101T000000Z", user_id=user, tz=tz,
                  start_utc=now - timedelta(hours=24), end_utc=now)
    records = [r for r in synth_records(span, seed=7, events=40)
               if "OUT-OF-WINDOW" not in r["content"]["text"]]
    for record in records:
        written = httpx.post(f"{base}/context/records", json=record, timeout=30)
        written.raise_for_status()
    print(f"  records   seeded {len(records)} synthetic C2 records")
PY

# (3) The window becomes openable once the seeded records are older than storage's
# delta. This is the same IDEMPOTENT get-or-create the nightly makes, so the window
# it opens IS the window the night runs on -- polling here costs nothing and mints
# nothing extra.
deadline=$((SECONDS + DEMO_WAIT))
until curl -fsS --max-time 5 -X POST "${STORAGE_URL}/training/windows" \
        -H 'content-type: application/json' \
        -d "{\"user_id\": \"${DEMO_USER}\"}" >/dev/null 2>&1; do
  if [ "$SECONDS" -ge "$deadline" ]; then
    echo "  window    still refused after ${DEMO_WAIT}s. Storage says:"
    curl -sS -X POST "${STORAGE_URL}/training/windows" \
      -H 'content-type: application/json' \
      -d "{\"user_id\": \"${DEMO_USER}\"}" | sed 's/^/            /'
    echo
    exit 1
  fi
  echo "  window    not openable yet -- the seeded records must age past storage's"
  echo "            window delta (t_end is now-delta). Waiting 10s."
  sleep 10
done
echo "  window    open"
echo

echo "=== the night (mock backend; storage's window, storage's day-log) ==="
STORAGE_URL="$STORAGE_URL" ./.venv/bin/python -m app.nightly --user "$DEMO_USER"
