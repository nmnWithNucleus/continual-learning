"""Wall-clock helpers for C1's time-spine (t_start / t_end).

C1 timestamps are device wall-clock, RFC3339 UTC. Recording stamps each chunk with
the wall-clock span it covers. A ``base_wallclock`` can be pinned per capture session
(the moment the stream's frame 0 was captured) so tests are deterministic; absent one,
we use the real UTC clock at capture time.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def parse_wallclock(value: str | None) -> datetime:
    """Parse an RFC3339 timestamp into an aware UTC datetime; None -> now (UTC).

    Accepts a trailing 'Z' or an explicit offset. Naive inputs are assumed UTC.
    """
    if value is None:
        return datetime.now(timezone.utc)
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def rfc3339(dt: datetime) -> str:
    """Render an aware datetime as RFC3339 UTC with a trailing 'Z'.

    Whole seconds render without a fractional part; sub-second spans (a short final
    chunk whose boundary isn't on a second) keep microsecond precision.
    """
    dt = dt.astimezone(timezone.utc)
    if dt.microsecond:
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def offset(base: datetime, seconds: float) -> str:
    """RFC3339 string for ``base + seconds`` (seconds may be fractional)."""
    return rfc3339(base + timedelta(seconds=seconds))
