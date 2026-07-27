"""Nightly entrypoint: consolidate one user's currently-open training window.

    python -m app.nightly --user u1                  # the real path (storage-backed)
    python -m app.nightly --user u1 --synthetic      # synthetic day-log, real window

There is no `--tz` and no `--date`. Both were continuum computing things it does
not own, and D18 moved them:

  * **`--tz` is RETIRED.** The cycle stops being told a timezone by its caller and
    reads the user's own **C12** profile (`GET /users/{user_id}/profile`). A 404
    means the user has no declared `home_tz`, which means they are **NOT
    SCHEDULABLE** — this exits loudly with an operator action rather than guessing.
    There is no default timezone anywhere in this system, and `home_tz`'s only jobs
    are the scheduler's fire time and the day-log renderer's FALLBACK zone.
  * **`--date` is gone with `window_for`.** The window is `[last_trained_t, now−δ)`
    on storage's ingest clock; it is **opened, not computed**
    (`POST /training/windows`, an idempotent get-or-create). A retry gets the same
    `window_id`, the same bounds, and therefore the same journal — which is exactly
    what a recomputed `now` would have destroyed.

The night ends by CLOSING the window with its outcome. The watermark advances if
and only if the outcome is `published`; gate failure, freeze, no data and
too-little data all leave it where it is, so the next window is a strict superset
of the failed one — the failed-day merge, obtained structurally.

**A CRASH IS NOT AN OUTCOME AND MUST NOT CLOSE THE WINDOW.** `close()` is
TERMINAL: it moves the ledger row to `consolidated`, and storage's get-or-create
can only hand back the same window WHILE IT IS STILL OPEN. So closing a crashed
window makes the retry find none open and mint a FRESH id — a fresh
`journal/{user}/{window_id}.json`, a fresh `cycles/` dir, a fresh seed, a full
re-train, a second C5 entry and a second reservoir admission. That is precisely
the outcome the idempotent open exists to prevent, reached by a different route.
An interrupted attempt is not a verdict on the window; the verdict is whatever the
retry reaches. So the crash path leaves the window OPEN, says so on stderr, and
re-raises. (The watermark is unaffected either way — only `published` advances it
— so what closing costs is journal/lineage/compute churn, not data.)

`crashed` stays in storage's outcome vocabulary, and it is not orphaned: it is how
an OPERATOR abandons a window that must never be retried (poison input, a
half-migrated store). Abandoning a night is a deliberate act with a human behind
it, not something an `except` clause decides on the way past.

EXIT CODES, because a scheduler reads them and a human reads them differently:

  0  the night ran (`published`, or `skipped_no_data` — nothing to consolidate)
  1  the night ran and did not publish (gate failure, freeze, too little data)
  2  it never started: an OPERATOR CONDITION, named on stderr in one line and
     never as a stack trace. Three of them today — the user has no `home_tz`
     (`NOT SCHEDULABLE`), storage will not open a window yet (`NO WINDOW TO
     CONSOLIDATE`: no ingest history, or everything they have is younger than
     storage's `delta`), and the day-log came back under a recipe or format this
     night does not train under (`MISCONFIGURED`). All three leave the ledger
     exactly as they found it.

Scheduling (cron per user shortly after their local `boundary_local_time` in
`home_tz`, fleet packing, min-data thresholds) is charter M4; this CLI is the unit
it will loop.
"""
from __future__ import annotations

import argparse
import json
import sys

from .clients import (day_log_client, profile_client, recipe_registry,
                      window_ledger)
from .clients.daylog_client import DayLogDialectMismatch
from .clients.profile_client import UserNotSchedulable
from .clients.window_client import WindowNotOpenable
from .config import get_settings
from .cycle import run_cycle
from .synth import synth_records

# CycleResult.status is already storage's outcome vocabulary, minus `crashed`
# (which no result object can report, because there isn't one).
_OK_STATUSES = ("published", "skipped_no_data")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="continuum nightly consolidation")
    ap.add_argument("--user", required=True)
    ap.add_argument("--synthetic", action="store_true",
                    help="build the day-log from a synthetic day instead of fetching "
                         "it from storage (headless demo). The WINDOW is still "
                         "storage's — nothing here mints one.")
    ap.add_argument("--force", action="store_true",
                    help="run even if the user is frozen after consecutive gate failures")
    args = ap.parse_args(argv)

    settings = get_settings()
    registry = recipe_registry(settings)
    recipe = registry.fetch_recipe(settings.recipe_id)

    # ---- who is this user, and can they be scheduled at all? --------------------
    try:
        home_tz = profile_client(settings).home_tz(args.user)
    except UserNotSchedulable as exc:
        print(f"NOT SCHEDULABLE: {exc}", file=sys.stderr)
        return 2

    # ---- the window is opened, not computed ------------------------------------
    ledger = window_ledger(settings)
    try:
        win = ledger.open(args.user, tz=home_tz)
    except WindowNotOpenable as exc:
        # An operator condition, not a crash: this user has nothing to consolidate
        # yet. Same posture as NOT SCHEDULABLE — say it in one line and exit 2,
        # rather than dying inside raise_for_status with a bare `409 Conflict` URL.
        print(f"NO WINDOW TO CONSOLIDATE: {exc}", file=sys.stderr)
        return 2

    daylog = None
    if args.synthetic:
        daylog = day_log_client(settings, recipe,
                                record_provider=lambda w: synth_records(w))

    try:
        result = run_cycle(win, daylog_client=daylog, registry=registry, recipe=recipe,
                           windows=ledger, force=args.force)
    except DayLogDialectMismatch as exc:
        # A half-finished re-pin (STORAGE_DAYLOG_RECIPE_ID vs CONTINUUM_RECIPE_ID, or
        # a day-log format bump). LEAVE THE WINDOW OPEN for exactly the reason the
        # crash path does — the retry after the config is fixed must resume THIS
        # window — but say what to change instead of printing a stack trace at an
        # operator whose only fault was re-pinning one service.
        print(f"MISCONFIGURED — REFUSING TO TRAIN: {exc}\n"
              f"Window {win.window_id} is LEFT OPEN; fix the pin and re-run.",
              file=sys.stderr)
        return 2
    except Exception:
        # LEAVE THE WINDOW OPEN. Closing it here would be the one action that
        # destroys the property the idempotent open exists to provide: storage's
        # get-or-create returns the same window only while it is still open, so a
        # closed crash means the retry mints a fresh window_id, a fresh journal and
        # a full re-train (see the module docstring). Announce it and re-raise; the
        # window's outcome is whatever the retry reaches.
        print(f"CRASHED mid-cycle on window {win.window_id} (user {args.user}). "
              "The window is LEFT OPEN on purpose: a retry re-opens THIS window and "
              "the journal resumes from the last completed stage. To abandon it "
              "instead, close it deliberately with outcome 'crashed'.", file=sys.stderr)
        raise

    ledger.close(win, result.status)
    print(json.dumps({
        "status": result.status, "user": result.user_id, "window": result.window_id,
        "home_tz": home_tz,
        "adapter_version": result.adapter_version,
        "gate": ({"passed": result.gate.passed, "reasons": result.gate.reasons,
                  "skipped_checks": list(result.gate.skipped)} if result.gate else None),
        "stages_run": result.stages_run, "stages_skipped": result.stages_skipped,
        "backend": settings.trainer_backend,
        "storage_clients": settings.storage_clients,
    }, indent=1))
    return 0 if result.status in _OK_STATUSES else 1


if __name__ == "__main__":
    sys.exit(main())
