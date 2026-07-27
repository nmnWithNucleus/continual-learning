"""The C12 per-user profile read — where `home_tz` comes from now.

`nightly.py --tz` is RETIRED. The cycle stops being told a timezone by whoever
invoked it and reads the user's own policy row instead:
`GET /users/{user_id}/profile` → `{contract:"C12", version:"0", user_id, home_tz,
profile_version, updated_at}`.

Two rules here are absolute, and both are D17:

  * **There is no default timezone anywhere.** Not here, not in storage, not in a
    settings file. A silent "UTC" default mis-attributes a US-Pacific user's
    edge-of-night records by 7–8 h, forever, with no error and no metric. So this
    module has no fallback value to offer, by construction.
  * **404 means NOT SCHEDULABLE.** A user with no profile has no `home_tz`, and a
    user with no `home_tz` cannot have a nightly cycle scheduled for them. That is
    an operational alert with a human action attached, never a silent skip and
    never a guess.

`home_tz` is DECLARED, NOT INFERRED — the user sets it; a device's current zone
may be *suggested* in a UI but is never stored as though it were an answer. It
therefore does not move when the user travels: the consolidation boundary stays
put instead of jumping 9 h and producing a 15 h night followed by a 33 h one.
Where the user physically was is the per-record `device_tz` FACT (C2 `source`),
which is what the day-log renderer prefers; `home_tz` is only the fallback.
"""
from __future__ import annotations

from typing import Protocol

from ._http import request_json


class UserNotSchedulable(RuntimeError):
    """No C12 profile (or no `home_tz`) for this user — the cycle must not run.

    Carries an operator-readable message on purpose: this is the failure that gets
    read at 04:05 by whoever is on call, and "KeyError: home_tz" is not an
    instruction."""


class ProfileClient(Protocol):
    def home_tz(self, user_id: str) -> str:
        """The user's declared IANA home zone. Raises if there isn't one."""


class HttpProfileClient:
    """The only implementation: storage owns the profile (C12)."""

    def __init__(self, storage_url: str, *, timeout: float = 60.0, transport=None):
        self.storage_url = storage_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport

    def home_tz(self, user_id: str) -> str:
        status, body = request_json(
            "GET", f"{self.storage_url}/users/{user_id}/profile",
            timeout=self.timeout, transport=self.transport, allow_status=(404,))
        if status == 404:
            raise UserNotSchedulable(
                f"user {user_id!r} has NO C12 profile at {self.storage_url} — so no "
                "declared home_tz, so this user is NOT SCHEDULABLE. There is no "
                "default timezone anywhere in this system (D17): a guessed zone "
                "mis-attributes a night's records by hours, forever, with no error "
                "and no metric. OPERATOR ACTION: set the user's home_tz "
                f"(PUT {self.storage_url}/users/{user_id}/profile) and re-run. "
                "This night is NOT skipped — nothing ran.")
        if body.get("contract") != "C12" or str(body.get("version")) != "0":
            raise ValueError(
                f"expected a C12 v0 profile body, got contract={body.get('contract')!r} "
                f"version={body.get('version')!r}")
        home_tz = body.get("home_tz")
        if not home_tz:
            # Storage's schema makes home_tz REQUIRED, so this is a contract
            # violation rather than a missing-profile case — still not schedulable,
            # and still not somewhere to invent a default.
            raise UserNotSchedulable(
                f"user {user_id!r} has a C12 profile with no home_tz — not "
                "schedulable, and there is no default timezone to fall back to")
        return str(home_tz)
