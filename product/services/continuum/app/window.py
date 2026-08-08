"""The consolidation window — a value object storage RETURNS, never one we compute.

D18 (2026-07-26) moved the window out of continuum entirely. The window is
`[last_trained_t, now−δ)` on **storage's `updated_at` axis** (D27): storage owns the
per-user watermark, mints the window durably and idempotently
(`POST /training/windows` is a get-or-create of the user's open window), and
serves the day-log for it. Continuum fetches; it does not derive.

What that deleted from this module, and why each deletion is load-bearing:

  * `window_for(user, local_day, tz)` — there is no local day any more. Under a
    watermark window a night can span 23 h, 25 h, or (after a missed night) 47 h,
    so naming a window by a local date would mean *synthesising* one, which both
    reintroduces the timezone dependency the watermark query does not need and
    makes the id lie about the window's extent.
  * `closed_window_before(user, now, tz)` — "the most recent fully closed window"
    was continuum recomputing `now`. That is exactly what breaks crash-safe
    idempotency: a retry would compute a *fresh* window, a fresh journal, and
    therefore a full re-train, a second C5 entry and a second reservoir
    admission. Storage's get-or-create returns the SAME window on a retry.
  * `Window.local_date` — `window_id` is **opaque**: `w<YYYYMMDD>T<HHMMSS>Z`,
    derived from the window's END instant, minted in exactly one place (storage)
    and **parsed by nobody**. Every consumer may rely on `<` / `>=` — the
    property the journal, the reservoir's `before_window` filter and publish's
    alias-monotonicity guard all compare on — **and on nothing else**.

`tz` survives, with a narrowed job. It is the user's **C12 `home_tz`** and it is
now a RENDER FALLBACK only: the zone a day-log block is written in when no
contributing record carried a `device_tz` (D17's FACT/POLICY split — the record's
own zone wins, because it is a fact about where the user physically was). It is
no longer boundary arithmetic, because there is no boundary arithmetic left here.
C10 v1 carries the same value on the day-log body for the same reason: it is what
makes a wrong-timezone adapter falsifiable after the fact.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Window:
    """One row of storage's training-window ledger, as continuum holds it.

    Mirrors what `POST /training/windows` / `GET /training/windows` return, plus
    the C12 `home_tz` stamped on beside them (storage's ledger row does not carry
    a zone — the window needs none, which is the whole point of a storage-clock
    watermark; the renderer's fallback does).
    """

    window_id: str        # OPAQUE token minted by storage. Compare with < / >=, never parse.
    user_id: str          # window_id is a PER-USER token, so it never travels alone
    tz: str               # the user's C12 home_tz — RENDER FALLBACK ONLY
    start_utc: datetime   # `updated_at` lower bound, inclusive
    end_utc: datetime     # `updated_at` upper bound, exclusive (half-open)
    state: str = "open"   # "open" | "consolidated"
    outcome: str | None = None   # the cycle outcome recorded at close, if any


def in_window(t_start_utc: datetime, win: Window) -> bool:
    """Half-open membership test, `[start, end)`.

    Used by the LOCAL day-log build (the parity reference), which buckets records
    it was handed by a provider. Storage's own materialization applies the same
    half-open rule on its `updated_at` axis; on that axis late data cannot exist,
    because `updated_at` is assigned at write and can never land below an
    already-closed boundary.
    """
    return win.start_utc <= t_start_utc < win.end_utc
