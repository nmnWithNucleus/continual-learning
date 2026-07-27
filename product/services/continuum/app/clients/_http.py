"""One HTTP call shape for every storage client.

Four seams (day-log, window ledger, recipe registry, reservoir) plus the C12
profile read all talk to the same service over the same transport, so they share
one request helper rather than four subtly different ones. Two properties matter
and both are failure-policy, not tidiness:

  * **Errors raise.** A nightly run must fail loudly and retry — never train on a
    silently truncated or absent input. `raise_for_status()` on everything except
    the statuses a caller explicitly asked to handle (a 404 the caller turns into
    its own domain error, e.g. "this user is not schedulable").
  * **The transport is injectable.** Tests drive these clients through
    `httpx.MockTransport`, which is how the seam is exercised without standing up
    storage — the same posture `context_reader` already used.
"""
from __future__ import annotations

from typing import Any, Iterable

import httpx


def request_json(method: str, url: str, *, timeout: float = 60.0,
                 params: dict[str, Any] | None = None,
                 json_body: Any | None = None,
                 transport: httpx.BaseTransport | None = None,
                 allow_status: Iterable[int] = ()) -> tuple[int, Any]:
    """Perform one request and return `(status_code, decoded_json | None)`.

    Statuses in `allow_status` are returned to the caller instead of raising, so
    a 404 can become a domain error with an operator-readable message. Every
    other error status raises `httpx.HTTPStatusError`.
    """
    allowed = set(allow_status)
    with httpx.Client(timeout=timeout, transport=transport) as client:
        resp = client.request(method, url, params=params, json=json_body)
    if resp.status_code in allowed:
        return resp.status_code, None
    resp.raise_for_status()
    return resp.status_code, resp.json()
