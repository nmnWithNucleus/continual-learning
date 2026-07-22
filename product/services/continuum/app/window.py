"""Consolidation-window semantics: a "day" is 04:00→04:00 user-local, not a calendar day.

Adopted from the research design of record (DESIGN_PROD §2a "Day-boundary
semantics"): the window runs from local 04:00 to the next local 04:00 at the
wearer's timezone; records are attributed by t_start; camera-off gaps stay
inside the window; all bookkeeping keys on the consolidation-window id, never
on calendar dates. Storage keeps UTC + the user tz, so windows are computed
client-side here from those two facts.

v0 simplification (recorded in ws-nightly-scaffold): one tz per window. The
design's travel rule (boundary follows the device's local clock at the boundary
moment) needs per-boundary tz lookups — wired when real multi-tz users exist.
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
