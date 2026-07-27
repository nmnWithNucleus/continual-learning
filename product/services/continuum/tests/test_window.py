"""The window after D18: a value object storage returns, with an OPAQUE id.

`window_for()` and `closed_window_before()` are deleted, so the tests that pinned
their local-date arithmetic are gone with them — that arithmetic is storage's now,
and asserting it here would be asserting a behaviour continuum no longer has.
What is tested instead is what continuum still owns and still depends on:

  * the half-open attribution rule the local day-log build applies;
  * the three properties the opaque `window_id` must have for the journal, the
    reservoir's `before_window` filter and publish's alias-monotonicity guard to
    stay correct — path-safety, string-order == chronological order, and second
    granularity;
  * that nothing in continuum can parse a window id back into a date.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.ids import validate_id
from app.window import Window, in_window
from tests._helpers import make_window, mint_window_id


def test_window_is_half_open():
    win = make_window("u1", 20, "UTC")
    assert in_window(win.start_utc, win)
    assert not in_window(win.end_utc, win)  # boundary record -> NEXT window


def test_overnight_wear_attributes_past_boundary_to_next_window():
    win = make_window("u1", 20, "UTC")
    just_before = datetime(2026, 7, 21, 3, 59, 59, tzinfo=timezone.utc)
    just_after = datetime(2026, 7, 21, 4, 0, 1, tzinfo=timezone.utc)
    assert in_window(just_before, win)
    assert not in_window(just_after, win)


def test_window_id_is_path_safe():
    """It is a filesystem path component and an rmtree target (journal/, cycles/,
    reservoir/, adapters/). A raw RFC3339 instant fails this regex on its colons —
    which is exactly why the minted format is what it is."""
    win = make_window("u1", 20, "America/Los_Angeles")
    assert validate_id(win.window_id, "window_id") == win.window_id
    with pytest.raises(ValueError):
        validate_id("2026-07-21T11:00:00Z", "window_id")


def test_window_ids_sort_chronologically_as_plain_strings():
    """Four places compare window ids AS STRINGS — publish's active_before and the
    alias-monotonicity guard, the cycle's journal debt + latest_window, and the
    reservoir's before_window filter. Fixed width + zero padding is what makes that
    equal chronological order."""
    ids = [make_window("u1", day, "America/Los_Angeles").window_id
           for day in (18, 19, 20)]
    assert ids == sorted(ids)
    assert len(set(len(i) for i in ids)) == 1          # fixed width
    assert ids[0] < ids[1] < ids[2]


def test_window_id_has_second_granularity():
    """A truncating id silently collides two distinct windows (a manual catch-up, a
    re-drive, a test), and an id collision corrupts the journal, the reservoir and
    C5 lineage at once."""
    t = datetime(2026, 7, 21, 11, 0, 0, tzinfo=timezone.utc)
    assert mint_window_id(t) != mint_window_id(t + timedelta(seconds=1))
    assert mint_window_id(t) == "w20260721T110000Z"


def test_window_carries_no_way_to_parse_its_id():
    """`Window.local_date` is deleted: the id is derived from the window's END
    instant, and under a watermark window there is no local date to parse back out
    (a window can span 23 h, 25 h or 47 h)."""
    win = make_window("u1", 20, "UTC")
    assert not hasattr(win, "local_date")
    assert not hasattr(Window, "local_date")


def test_window_tz_is_the_render_fallback_not_a_boundary():
    """`tz` survives with a narrowed job: the C12 home_tz the day-log renderer falls
    back to when a record carried no device_tz. It no longer decides any bounds —
    those arrive from storage already computed."""
    win = make_window("u1", 20, "Asia/Kolkata")   # UTC+5:30
    assert win.tz == "Asia/Kolkata"
    assert win.start_utc == datetime(2026, 7, 19, 22, 30, tzinfo=timezone.utc)
    # Bounds are just data on the value object — nothing recomputes them.
    handed_in = Window(window_id="w20260721T110000Z", user_id="u1", tz="UTC",
                       start_utc=datetime(2026, 7, 20, 11, tzinfo=timezone.utc),
                       end_utc=datetime(2026, 7, 21, 11, tzinfo=timezone.utc))
    assert handed_in.end_utc - handed_in.start_utc == timedelta(hours=24)


def test_window_bounds_may_span_more_than_a_day():
    """The watermark window is not a calendar day: after a missed night it is a
    strict superset of the failed one. Nothing in the value object objects."""
    win = Window(window_id="w20260723T110000Z", user_id="u1", tz="UTC",
                 start_utc=datetime(2026, 7, 21, 11, tzinfo=timezone.utc),
                 end_utc=datetime(2026, 7, 23, 11, tzinfo=timezone.utc))
    assert win.end_utc - win.start_utc == timedelta(hours=48)
    assert in_window(datetime(2026, 7, 22, 9, tzinfo=timezone.utc), win)
