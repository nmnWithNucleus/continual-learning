"""Consolidation-window semantics: a "day" is 04:00→04:00 user-local, not a calendar day.

Adopted from the research design of record (DESIGN_PROD §2a "Day-boundary
semantics"): the window runs from local 04:00 to the next local 04:00 at the
wearer's timezone; records are attributed by t_start; camera-off gaps stay
inside the window; all bookkeeping keys on the consolidation-window id, never
on calendar dates. Storage keeps timestamps in UTC; the tz used here is the
user's profile `home_tz` (D17), supplied by the caller until storage serves the
per-user profile — `nightly.py --tz`, which is REQUIRED, because there is no
default timezone anywhere in the system.

The tz used HERE is a scheduling/boundary value only. It is NOT what renders a
block's local anchor line: each record carries the capturing device's own
`device_tz` (C2 `source`), and `daylog._block_zone` prefers that, falling back
to this window's tz. That split is the whole of D17 — the boundary is a per-user
POLICY ("when is this user's night?"), while the rendered wall-clock is a
per-moment FACT that changes when the user travels.

One tz per window is therefore the DURABLE rule for the boundary, not a v0
shortcut: `window_id` is the local start date and keys the day-log, the cycle
journal, C5's `training_window`, and publish's active-alias monotonicity, so a
boundary chasing a per-moment device clock would produce 23 h/25 h days and, across
the dateline, one `window_id` for two windows. The cycle window is moving to the
watermark range `[last_trained_t, now)` (ARCHITECTURE C10 row), which drops
local-date arithmetic from the boundary entirely.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Window:
    window_id: str        # "w2026-07-21" — the LOCAL date the window STARTS on
    user_id: str
    tz: str
    start_utc: datetime   # inclusive
    end_utc: datetime     # exclusive (half-open [start, end), matching C10's range read)

    @property
    def local_date(self) -> date:
        return date.fromisoformat(self.window_id[1:])


def _parse_boundary(boundary_local_time: str) -> time:
    hh, mm = boundary_local_time.split(":")
    return time(int(hh), int(mm))


def window_for(user_id: str, local_day: date, tz: str,
               boundary_local_time: str = "04:00") -> Window:
    """The consolidation window that STARTS at `boundary` local time on `local_day`."""
    boundary = _parse_boundary(boundary_local_time)
    zone = ZoneInfo(tz)
    start_local = datetime.combine(local_day, boundary, tzinfo=zone)
    end_local = datetime.combine(local_day + timedelta(days=1), boundary, tzinfo=zone)
    return Window(
        window_id=f"w{local_day.isoformat()}",
        user_id=user_id,
        tz=tz,
        start_utc=start_local.astimezone(timezone.utc),
        end_utc=end_local.astimezone(timezone.utc),
    )


def closed_window_before(user_id: str, now_utc: datetime, tz: str,
                         boundary_local_time: str = "04:00") -> Window:
    """The most recent FULLY CLOSED window as of `now_utc` — what a nightly run
    that fires shortly after the boundary should consolidate."""
    zone = ZoneInfo(tz)
    boundary = _parse_boundary(boundary_local_time)
    now_local = now_utc.astimezone(zone)
    # The window starting on day D closes at boundary on D+1. Walk back to the
    # latest start whose end is <= now.
    candidate = now_local.date() - timedelta(days=1)
    while True:
        win = window_for(user_id, candidate, tz, boundary_local_time)
        if win.end_utc <= now_utc:
            return win
        candidate -= timedelta(days=1)


def in_window(t_start_utc: datetime, win: Window) -> bool:
    """Attribution rule: a record belongs to the window its t_start falls in
    (half-open). Overnight wear past the boundary attributes to the next window."""
    return win.start_utc <= t_start_utc < win.end_utc
