"""RFC3339 wall-clock helpers — the time spine every C2 record rides on."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def parse_rfc3339(value: str) -> datetime:
    """Parse an RFC3339/ISO-8601 timestamp to an aware UTC datetime.

    Accepts a trailing 'Z' (Python < 3.11 fromisoformat rejects it) and treats a
    naive stamp as UTC.
    """
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def abs_time(base: datetime, offset_seconds: float) -> str:
    """Absolute RFC3339 UTC stamp = base + offset_seconds."""
    return (base + timedelta(seconds=offset_seconds)).astimezone(timezone.utc).isoformat()


def now_iso() -> str:
    """Current time as an RFC3339 UTC stamp."""
    return datetime.now(timezone.utc).isoformat()
