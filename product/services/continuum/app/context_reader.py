"""The raw `/context` range read — thin client over `GET /context/records`.

Half-open `[from, to)` on EVENT time, ordered by t_start. This is **not** the
C10-evolved training read any more: D18 made C10 a day-log fetch over storage's
storage-clock watermark (`app/clients/daylog_client.py`), and this range read was
explicitly **not** retired by that decision — it stays first-class as a different
question on a different axis (a retraction, a debug, D12's beta training feed).

**It has NO job in the nightly path, and wiring it into one was a defect.** It was
the local day-log client's default record provider, which meant the default nightly
handed a training window's `updated_at` bounds to an EVENT-time filter and trained on
whatever fell out — in the measured case, nothing at all. A training window is
`[last_trained_t, now−δ)` on `updated_at`; this read answers a question about a
lived period. Same units, different axis, no error. `clients.day_log_client` now
refuses that wiring outright (`IngestWindowNotReadable`).

Its real consumers hold an EVENT-time window on purpose and say so: the retraction
and debugging paths, D12's beta training feed, and `scripts/phase3_daylog.py`, which
re-renders archived local calendar days and builds its own event-time window for
each. The M9 parity reference does not use it either — it hands
`LocalDayLogClient.from_records` a fixed record list.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from .window import Window


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def fetch_window_records(storage_url: str, win: Window, *,
                         timeout: float = 60.0,
                         transport: httpx.BaseTransport | None = None) -> list[dict[str, Any]]:
    """All C2 records for the window, ordered by t_start. Raises on HTTP errors —
    a nightly run must fail loudly and retry, never train on a silently
    truncated window."""
    with httpx.Client(timeout=timeout, transport=transport) as client:
        resp = client.get(
            f"{storage_url}/context/records",
            params={"user_id": win.user_id,
                    "from": _iso(win.start_utc),
                    "to": _iso(win.end_utc)},
        )
        resp.raise_for_status()
        body = resp.json()
    records = body["records"] if isinstance(body, dict) and "records" in body else body
    if not isinstance(records, list):
        raise ValueError(f"unexpected /context/records payload shape: {type(records)}")
    return records
