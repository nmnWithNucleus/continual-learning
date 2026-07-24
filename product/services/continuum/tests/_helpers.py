"""Shared test helper: run one night from a fixed record list.

Post-2c, `run_cycle` consumes a day-log CLIENT rather than raw records — continuum
fetches the day-log, it does not build it. Tests that hold records in hand wrap
them in a local day-log client with `from_records`, which is exactly the seam the
storage HTTP client will occupy later."""
from __future__ import annotations

from app.clients import LocalDayLogClient
from app.cycle import run_cycle


def consolidate(records, win, *, recipe, policy=None, force=False):
    daylog_client = LocalDayLogClient.from_records(
        records, segment_seconds=recipe.segment_seconds,
        block_segments=recipe.block_segments)
    return run_cycle(win, daylog_client=daylog_client, recipe=recipe,
                     policy=policy, force=force)
