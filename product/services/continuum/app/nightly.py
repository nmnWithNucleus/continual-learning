"""Nightly entrypoint: consolidate one user's most recent closed window.

    python -m app.nightly --user u1 --tz America/Los_Angeles            # via C10
    python -m app.nightly --user u1 --date 2026-07-21 --synthetic       # headless demo

Scheduling (cron per user shortly after their local 04:00 boundary, fleet
packing, min-data thresholds) is charter M4; this CLI is the unit it will loop.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone

from .clients import day_log_client, recipe_registry
from .config import get_settings
from .context_reader import fetch_window_records
from .cycle import run_cycle
from .synth import synth_records
from .window import closed_window_before, window_for


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="continuum nightly consolidation")
    ap.add_argument("--user", required=True)
    ap.add_argument("--tz", default="UTC")
    ap.add_argument("--date", help="local window-start date YYYY-MM-DD "
                                   "(default: most recent closed window)")
    ap.add_argument("--synthetic", action="store_true",
                    help="use a synthetic day instead of reading C10 (headless demo)")
    ap.add_argument("--force", action="store_true",
                    help="run even if the user is frozen after consecutive gate failures")
    args = ap.parse_args(argv)

    settings = get_settings()
    # Fetch the pinned recipe from the registry (its window knob decides the
    # boundary), then build the day-log client with the matching record source.
    registry = recipe_registry(settings)
    recipe = registry.fetch_recipe(settings.recipe_id)
    boundary = recipe.boundary_local_time
    if args.date:
        win = window_for(args.user, date.fromisoformat(args.date), args.tz, boundary)
    else:
        win = closed_window_before(args.user, datetime.now(timezone.utc), args.tz,
                                   boundary)

    if args.synthetic:
        provider = lambda w: synth_records(w)  # noqa: E731 — headless demo day
    else:
        provider = lambda w: fetch_window_records(  # noqa: E731 — the C10 read
            settings.storage_url, w, timeout=settings.http_timeout)
    daylog = day_log_client(settings, recipe, record_provider=provider)

    result = run_cycle(win, daylog_client=daylog, registry=registry, recipe=recipe,
                       force=args.force)
    print(json.dumps({
        "status": result.status, "user": result.user_id, "window": result.window_id,
        "adapter_version": result.adapter_version,
        "gate": ({"passed": result.gate.passed, "reasons": result.gate.reasons,
                  "skipped_checks": list(result.gate.skipped)} if result.gate else None),
        "stages_run": result.stages_run, "stages_skipped": result.stages_skipped,
        "backend": settings.trainer_backend,
    }, indent=1))
    return 0 if result.status in ("published", "skipped_no_data") else 1


if __name__ == "__main__":
    sys.exit(main())
