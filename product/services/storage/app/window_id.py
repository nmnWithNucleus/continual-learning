"""The `window_id` minter + validator — the ONLY place a window_id is constructed.

D18 (ARCHITECTURE.md § "C10 evolved") makes the training-window id an **opaque,
path-safe, lexicographically-ordered** token derived from the window's **end
instant** in UTC: ``w<YYYYMMDD>T<HHMMSS>Z`` (e.g. ``w20260721T110000Z``).

Three properties are load-bearing and none of them is decorative:

1. **Path-safe.** Continuum uses the id as a filesystem path component and an
   ``rmtree`` target (``journal/``, ``cycles/``, ``reservoir/``, ``adapters/``)
   and gates it through ``ids.validate_id`` — a raw RFC3339 instant *fails*
   that gate because of its colons.
2. **Lexicographic order == chronological order.** Four continuum call sites
   compare window ids *as strings* and rely on nothing else: ``publish.py``'s
   ``active_before`` lineage and its alias-monotonicity guard, ``cycle.py``'s
   journal-debt + ``latest_window`` checks, and ``reservoir.py``'s
   ``before_window`` replay filter. Fixed width + zero padding is what
   guarantees it.
3. **Second granularity, not minute.** A truncating id can silently collide two
   distinct windows (a manual catch-up, a re-drive, a test), and an id collision
   corrupts the journal, the reservoir and C5 lineage at once.

**Meaning is minted, never parsed.** Consumers may rely on ``<`` / ``>=`` and on
nothing else — this module deliberately exposes no "parse an id back to a date"
helper, because that is precisely what D18 deleted (``Window.local_date``,
``ReservoirEntry.local_window_date``). Under the ``[last_trained_t, now-delta)``
watermark a window can span 23 h, 25 h or (after a missed night) 47 h, so there
is no local date the id could honestly name.

Collision safety is *by construction*, not by luck: the ledger refuses to open a
window whose end is not strictly greater than every prior window end for that
user (see ``Store.open_training_window``), so two windows can never share a
second.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

# The one regex. Fixed width, zero-padded, no separators beyond 'T'/'Z'.
WINDOW_ID_PATTERN = r"^w\d{8}T\d{6}Z$"
_WINDOW_ID_RE = re.compile(WINDOW_ID_PATTERN)

_MINT_FMT = "w%Y%m%dT%H%M%SZ"


def mint_window_id(end_utc: datetime) -> str:
    """Mint the window id for a window ENDING at ``end_utc``.

    Requires a timezone-aware datetime and normalizes to UTC — a naive datetime
    is rejected rather than assumed local or assumed UTC, because a silently
    wrong zone here mis-orders every downstream string comparison.

    Sub-second precision is truncated (the format carries seconds), so callers
    that persist ``t_end`` alongside the id MUST persist the same truncated
    instant or the two will disagree.
    """
    if end_utc.tzinfo is None or end_utc.tzinfo.utcoffset(end_utc) is None:
        raise ValueError(
            "mint_window_id requires a timezone-aware datetime; got a naive one"
        )
    return end_utc.astimezone(timezone.utc).strftime(_MINT_FMT)


def validate_window_id(window_id: str) -> bool:
    """True iff ``window_id`` was shaped by this minter.

    The one validator. Every surface that accepts a window_id from outside runs
    it through here before touching the ledger, so a malformed id is a 422 at
    the edge rather than a silent miss deeper in.
    """
    # fullmatch, not match: Python's `$` also matches just BEFORE a trailing newline, so
    # "w20260721T110000Z\n" would slip through a plain match() — and that string is a
    # different filesystem path from the id it looks like.
    return isinstance(window_id, str) and bool(_WINDOW_ID_RE.fullmatch(window_id))
