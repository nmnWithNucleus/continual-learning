"""Shared test helpers.

Two of them, and the second one is the D18 cutover's whole shape in miniature.

`consolidate` — run one night from a fixed record list. `run_cycle` consumes a
day-log CLIENT rather than raw records: continuum fetches the day-log, it does not
build it. Tests that hold records in hand wrap them in a local day-log client with
`from_records`, which is exactly the seam `HttpDayLogClient` occupies against real
storage.

`make_window` — a `Window` as **storage would have minted it**. `window_for()` and
`closed_window_before()` are deleted from `app/`: the window is
`[last_trained_t, now−δ)` on storage's ingest clock, opened by
`POST /training/windows`, and `window_id` is an opaque `w<YYYYMMDD>T<HHMMSS>Z`
token derived from the window's END instant. Tests still need *some* window to
render a day-log against, so the boundary arithmetic lives HERE, in the harness
that stands in for storage — which is the point: it is no longer in the product.

Two properties of the real minter are reproduced deliberately, because tests
depend on them the way production does:

  * the id is derived from the window's END instant, so a later window always
    sorts after an earlier one as a plain STRING (fixed width, zero padded) — the
    comparison the journal, the reservoir's `before_window` filter and publish's
    alias-monotonicity guard all make;
  * it is path-safe (`app/ids.py`'s regex), because it becomes a directory name
    and an `rmtree` target. A raw RFC3339 instant would fail that regex on its
    colons, which is why the format is what it is.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.clients import LocalDayLogClient
from app.cycle import run_cycle
from app.window import Window


def mint_window_id(end_utc: datetime) -> str:
    """Storage's minter, reproduced for the harness: `w<YYYYMMDD>T<HHMMSS>Z` from
    the window's END instant, at SECOND granularity (a truncating id would collide
    two distinct windows — a manual catch-up, a re-drive, a test — and an id
    collision corrupts the journal, the reservoir and C5 lineage at once)."""
    return "w" + end_utc.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def make_window(user_id: str, day: int, tz: str = "UTC", *, year: int = 2026,
                month: int = 7, boundary: str = "04:00", state: str = "open",
                outcome: str | None = None) -> Window:
    """A one-local-day window for `day`, as storage would have returned it.

    The 04:00→04:00 local span is retained here (not in `app/`) so the day-log
    tests keep exercising real, timezone-shifted bounds — the anchor-line, block-gap
    and record-attribution behaviour they cover is unchanged by D18. What changed is
    who computes the bounds, and that is now the harness, standing in for storage.
    """
    hh, mm = boundary.split(":")
    zone = ZoneInfo(tz)
    start_local = datetime.combine(date(year, month, day), time(int(hh), int(mm)),
                                   tzinfo=zone)
    end_local = start_local + timedelta(days=1)
    end_utc = end_local.astimezone(timezone.utc)
    return Window(window_id=mint_window_id(end_utc), user_id=user_id, tz=tz,
                  start_utc=start_local.astimezone(timezone.utc), end_utc=end_utc,
                  state=state, outcome=outcome)


class StubWindowLedger:
    """An in-memory stand-in for storage's training-window ledger.

    Implements the `WindowLedger` protocol over windows the test already holds.
    Its `prior_windows` does exactly what the HTTP client's does — filter the
    CONSOLIDATED windows to those strictly before a given id, comparing the opaque
    ids as strings — so a cycle test exercises the enumeration path rather than
    reconstructing prior windows from a parsed date, which is the behaviour D18
    deleted.
    """

    def __init__(self, windows: list[Window] | None = None):
        self.rows: dict[str, Window] = {w.window_id: w for w in (windows or [])}
        self.closed: list[tuple[str, str]] = []

    def add(self, win: Window, *, state: str = "consolidated",
            outcome: str = "published") -> Window:
        row = Window(win.window_id, win.user_id, win.tz, win.start_utc, win.end_utc,
                     state=state, outcome=outcome)
        self.rows[win.window_id] = row
        return row

    def open(self, user_id: str, *, tz: str) -> Window:
        for row in sorted(self.rows.values(), key=lambda w: w.window_id):
            if row.user_id == user_id and row.state == "open":
                return row
        raise LookupError(f"no open window for {user_id!r} in the stub ledger")

    def enumerate(self, user_id: str, *, tz: str,
                  state: str | None = None) -> list[Window]:
        return sorted(
            (w for w in self.rows.values()
             if w.user_id == user_id and (state is None or w.state == state)),
            key=lambda w: w.window_id)

    def prior_windows(self, user_id: str, before_window: str, *,
                      tz: str) -> list[Window]:
        return [w for w in self.enumerate(user_id, tz=tz, state="consolidated")
                if w.window_id < before_window]

    def close(self, win: Window, outcome: str) -> Window:
        self.closed.append((win.window_id, outcome))
        return self.add(win, state="consolidated", outcome=outcome)


def consolidate(records, win, *, recipe, policy=None, force=False, windows=None):
    daylog_client = LocalDayLogClient.from_records(
        records, segment_seconds=recipe.segment_seconds,
        block_segments=recipe.block_segments)
    return run_cycle(win, daylog_client=daylog_client, recipe=recipe,
                     policy=policy, force=force, windows=windows)
