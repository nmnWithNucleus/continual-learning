"""The training-window ledger — storage's, consumed here.

D18 put the per-user watermark and the window rows in storage, for a reason
continuum cannot satisfy on its own: the watermark is a fact about *storage's*
ingest clock. Three operations, and each one exists because deleting continuum's
local arithmetic left a hole that only storage can fill:

  open()            `POST /training/windows {user_id}` — an IDEMPOTENT
                    get-or-create of the user's currently-open window. This is
                    what preserves the cycle's crash-safe journal replay: a retry
                    gets the SAME `window_id` and the SAME bounds, where
                    recomputing `now` would mint a fresh id, a fresh journal, a
                    full re-train, a second C5 entry and a second reservoir
                    admission.
  enumerate()       `GET /training/windows?user_id=&state=` — "which windows has
                    this user consolidated?". LOAD-BEARING, not a convenience:
                    it is where prior windows come from now that
                    `ReservoirEntry.local_window_date()` no longer rebuilds them
                    from a parsed id under tonight's timezone.
  close()           `POST /training/windows/{window_id}/close {outcome}` —
                    advances the watermark **iff the outcome is `published`**.
                    Gate failure, freeze, crash, no data and too-little data all
                    leave `last_trained_t` alone, so the next window is a strict
                    superset of the failed one: the design-of-record's failed-day
                    merge, obtained structurally.

`close()` is TERMINAL, and that is the thing to hold on to: `open()` is idempotent
only while the window is still OPEN, so closing a window is what decides that the
NEXT `open()` mints a new id, a new journal and a new seed. A night that crashed
mid-cycle therefore must NOT be closed — `nightly.py` leaves it open so the retry
resumes it. `crashed` remains a legal outcome for the case it was always right for:
an OPERATOR deliberately abandoning a window that must never be retried.

There is deliberately NO local implementation. A window id is minted in exactly
one place in the whole system; a continuum-side minter would be a second one, and
the ordering invariant that the journal, the reservoir filter and publish's alias
guard all rely on is only as strong as the discipline that mints ids.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Protocol

import httpx

from ..window import Window
from ._http import request_json

# The closed set storage accepts. `published` is the ONLY one that advances the
# watermark; the rest are recorded outcomes that leave it where it is.
WINDOW_OUTCOMES = ("published", "gate_failed", "frozen", "skipped_no_data", "crashed")


def _parse_ts(raw: str) -> datetime:
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class WindowNotOpenable(RuntimeError):
    """Storage declined to open a window for this user (409), with a reason.

    Not a crash and not a bug: it is storage saying that this user cannot be
    consolidated *yet*, and the two reasons it says it for are both ordinary.

      * **no ingest history** — the user has never been trained AND has no
        `/context` records, so there is no instant to start the window at. A brand
        new user, or a demo run against an empty store, is exactly this.
      * **window end is not strictly greater than the floor** — everything this user
        has was ingested within the last `delta` seconds (storage's in-flight-write
        guard), so `[t_start, now−delta)` is empty or inverted. It becomes openable
        by itself, with no operator action, once the data is `delta` old.

    It is given a type so the caller can say that in words instead of dying in
    `raise_for_status()` with a bare `409 Conflict` URL — which is what it used to
    do, and which taught every reader that the demo was broken rather than that the
    user was not ready. `detail` is storage's own JSON body, passed through
    unedited: it names the reason, and for the second case the bounds that missed.
    """

    def __init__(self, message: str, detail: Any = None) -> None:
        super().__init__(message)
        self.detail = detail


class WindowLedger(Protocol):
    def open(self, user_id: str, *, tz: str) -> Window:
        """The user's currently-open window (get-or-create, idempotent)."""

    def enumerate(self, user_id: str, *, tz: str,
                  state: str | None = None) -> list[Window]:
        """Every window for this user, optionally filtered by ledger state."""

    def prior_windows(self, user_id: str, before_window: str, *,
                      tz: str) -> list[Window]:
        """**Published** windows strictly before `before_window` — replay's input.
        Published, not merely consolidated: replay is anti-forgetting, so a night
        that never entered the adapter has nothing to be forgotten."""

    def close(self, win: Window, outcome: str) -> Window:
        """Record the night's outcome; advances the watermark iff `published`."""


class HttpWindowLedger:
    """The only implementation: storage's ledger over HTTP."""

    def __init__(self, storage_url: str, *, timeout: float = 60.0, transport=None):
        self.storage_url = storage_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport

    def _window(self, row: dict[str, Any], tz: str) -> Window:
        return Window(
            window_id=row["window_id"], user_id=row["user_id"], tz=tz,
            start_utc=_parse_ts(row["t_start"]), end_utc=_parse_ts(row["t_end"]),
            state=row.get("state", "open"), outcome=row.get("outcome"))

    def open(self, user_id: str, *, tz: str) -> Window:
        try:
            _, body = request_json(
                "POST", f"{self.storage_url}/training/windows",
                json_body={"user_id": user_id}, timeout=self.timeout,
                transport=self.transport)
        except httpx.HTTPStatusError as exc:
            # 409 is the ONE status here that is a statement about the user rather
            # than about us, so it is the one that gets a domain error. Everything
            # else (a 5xx, a refused connection) is still an exception nobody should
            # dress up as an expected condition.
            if exc.response.status_code != 409:
                raise
            try:
                detail = exc.response.json().get("detail")
            except (ValueError, AttributeError):  # non-JSON error body
                detail = exc.response.text
            reason = detail.get("reason") if isinstance(detail, dict) else detail
            error = detail.get("error") if isinstance(detail, dict) else None
            raise WindowNotOpenable(
                f"storage will not open a training window for user {user_id!r}: "
                f"{error or 'refused'} — {reason or json.dumps(detail)}",
                detail) from exc
        return self._window(body, tz)

    def enumerate(self, user_id: str, *, tz: str,
                  state: str | None = None) -> list[Window]:
        params: dict[str, Any] = {"user_id": user_id}
        if state is not None:
            params["state"] = state
        _, body = request_json(
            "GET", f"{self.storage_url}/training/windows", params=params,
            timeout=self.timeout, transport=self.transport)
        return [self._window(row, tz) for row in body]

    def prior_windows(self, user_id: str, before_window: str, *,
                      tz: str) -> list[Window]:
        """The windows replay may re-read: **PUBLISHED**, strictly before this one.

        The `outcome == "published"` filter is load-bearing, not defensive. Replay
        is ANTI-FORGETTING: it re-teaches material the adapter already learned, so
        it can only ever mean *published* nights. A gate-failed or crashed window
        was never trained into the adapter, so it has nothing to be forgotten —
        rehearsing it is definitionally wrong for the mechanism, and because the
        budget is fixed it also DISPLACES genuinely-old material one-for-one.

        It compounds with the watermark's superset rule: a failed window does not
        advance `last_trained_t`, so its records are re-trained as tonight's FRESH
        corpus. Without this filter its day-log would be a rehearsal source at the
        same time — measured at 50% of the replay budget spent re-teaching text
        already in tonight's corpus.

        This was the pre-cutover behaviour and it was lost by accident: the pool
        used to come from `reservoir.entries()`, and `reservoir.admit()` is only
        reached inside the gate-PASSED branch, so "published only" was implicit in
        the source. Substituting the ledger made the filter explicit work.

        `<` on the opaque id is the ONLY operation performed on it — the same
        comparison the reservoir's `before_window` filter and publish's alias
        guard make, and the reason the id is fixed-width and zero-padded."""
        windows = [w for w in self.enumerate(user_id, tz=tz, state="consolidated")
                   if w.window_id < before_window and w.outcome == "published"]
        return sorted(windows, key=lambda w: w.window_id)

    def close(self, win: Window, outcome: str) -> Window:
        if outcome not in WINDOW_OUTCOMES:
            raise ValueError(
                f"unknown window outcome {outcome!r} — storage's enum is "
                f"{list(WINDOW_OUTCOMES)}; an unrecognised outcome must fail here "
                "rather than become a silent non-advance (or a silent advance)")
        _, body = request_json(
            "POST", f"{self.storage_url}/training/windows/{win.window_id}/close",
            json_body={"user_id": win.user_id, "outcome": outcome},
            timeout=self.timeout, transport=self.transport)
        return self._window(body, win.tz)
