"""C10 training-window read — thin client over storage's beta range read.

Shape deliberately matches what storage already serves (and what the C10 v0
freeze proposal extends): `GET /context/records?user_id=&from=&to=`, half-open
`[from, to)`, ordered by t_start. The freeze itself is a founders' act (D15);
this client codes to the beta shape and grows the frozen extras (cursor,
pipeline_version / modality filters) when they're ratified.
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
