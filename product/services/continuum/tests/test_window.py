from datetime import date, datetime, timezone

from app.window import closed_window_before, in_window, window_for


def test_local_0400_boundaries_map_to_utc():
    # America/Los_Angeles is UTC-7 in July: local 04:00 == 11:00Z.
    win = window_for("u1", date(2026, 7, 20), "America/Los_Angeles")
    assert win.start_utc == datetime(2026, 7, 20, 11, 0, tzinfo=timezone.utc)
    assert win.end_utc == datetime(2026, 7, 21, 11, 0, tzinfo=timezone.utc)
    assert win.window_id == "w2026-07-20"


def test_window_is_half_open():
    win = window_for("u1", date(2026, 7, 20), "UTC")
    assert in_window(win.start_utc, win)
    assert not in_window(win.end_utc, win)  # boundary record -> NEXT window


def test_overnight_wear_attributes_past_boundary_to_next_window():
    win = window_for("u1", date(2026, 7, 20), "UTC")
    just_before = datetime(2026, 7, 21, 3, 59, 59, tzinfo=timezone.utc)
    just_after = datetime(2026, 7, 21, 4, 0, 1, tzinfo=timezone.utc)
    assert in_window(just_before, win)
    assert not in_window(just_after, win)


def test_closed_window_before_picks_fully_closed_day():
    # 05:00 local on the 21st: the window started on the 20th just closed.
    now = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)  # 05:00 PDT
    win = closed_window_before("u1", now, "America/Los_Angeles")
    assert win.window_id == "w2026-07-20"
    # 03:00 local on the 21st: the 20th window is still OPEN -> the 19th.
    now = datetime(2026, 7, 21, 10, 0, tzinfo=timezone.utc)  # 03:00 PDT
    win = closed_window_before("u1", now, "America/Los_Angeles")
    assert win.window_id == "w2026-07-19"


def test_non_dst_timezone():
    win = window_for("u1", date(2026, 7, 20), "Asia/Kolkata")  # UTC+5:30
    assert win.start_utc == datetime(2026, 7, 19, 22, 30, tzinfo=timezone.utc)
