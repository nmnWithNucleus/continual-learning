"""Reservoir client — write the amplified corpus, read prior day-logs for replay.

Two operations, matching the architecture's split (ws-morpheus-port §1, storage
CHARTER):

  admit(amplified corpus)   the WRITE side. The amplified corpus is kept forever
                            as audit/provenance — the one invariant is that
                            synthetic text never lands in /context.
  sample_replay(...)        the READ side. What rescues sequential consolidation
                            from collapse.

The locked decision is that replay re-reads prior DAY-LOGS (raw) — the amplified
reservoir is audit/provenance, off the replay hot path (raw is a measured tie
with amplified, and simpler). Recipe v1.0 pins `amp` because the Phase-1 goldens
were produced that way and parity is diffed against them; this client serves both,
so flipping a future recipe to `rawlog` is a recipe change, not a code change:

  source="amp"     pool the amplified corpora in the reservoir (recipe v1.0, the
                   parity path — byte-identical to pre-2c).
  source="rawlog"  pool the RAW prior day-logs, fetched through the day-log client
                   (the locked architecture; the amplified store is not touched).

Both sample through the identical pooled-uniform sampler; only the pooled text
differs. Two backends, one interface: the local filesystem reservoir, and C14 over
HTTP (`POST /reservoir/{user_id}/{window_id}`, `GET /reservoir/{user_id}`).

One asymmetry between them is a CONTRACT fact, not an omission: **C14 serves the
ledger, never the corpora.** The bodies are deliberately not on that read — the
ledger's consumer wants provenance (which windows ran, under which recipe, with
which content hash), and shipping megabytes of amplified text to answer that would
be a different question badly answered. So `source="amp"` — which pools the
amplified corpus BODIES — has no HTTP implementation, and the HTTP client says so
loudly rather than silently replaying nothing. That is consistent with the locked
architecture (`rawlog`, prior day-logs via C10) and with C14's own framing as
audit/provenance rather than the replay hot path.
"""
from __future__ import annotations

import logging

import httpx

from pathlib import Path
from typing import Protocol

from ..renderer import blocks_text
from ..reservoir import Reservoir, ReservoirEntry, sample_pooled
from ..window import Window
from ._http import request_json
from .daylog_client import DayLogClient


log = logging.getLogger(__name__)

class ReservoirClient(Protocol):
    def admit(self, user_id: str, window_id: str, recipe_id: str,
              corpus_text: str) -> ReservoirEntry: ...

    def entries(self, user_id: str, *, before_window: str | None = None) -> list[ReservoirEntry]: ...

    def sample_replay(self, user_id: str, *, target_chars: int, frac: float, seed: int,
                      before_window: str | None = None, source: str = "amp",
                      prior_windows: list[Window] | None = None) -> str: ...


class LocalReservoirClient:
    """Local backend: the filesystem reservoir, plus raw-day-log replay through
    the day-log client. The amplified write path is exactly the scaffold's."""

    def __init__(self, var_dir: str | Path, *, daylog_client: DayLogClient | None = None):
        self._reservoir = Reservoir(var_dir)
        self._daylog = daylog_client

    def admit(self, user_id: str, window_id: str, recipe_id: str,
              corpus_text: str) -> ReservoirEntry:
        return self._reservoir.admit(user_id, window_id, recipe_id, corpus_text)

    def entries(self, user_id: str, *, before_window: str | None = None) -> list[ReservoirEntry]:
        return self._reservoir.entries(user_id, before_window=before_window)

    def sample_replay(self, user_id: str, *, target_chars: int, frac: float, seed: int,
                      before_window: str | None = None, source: str = "amp",
                      prior_windows: list[Window] | None = None) -> str:
        if source == "amp":
            # Unchanged from the scaffold — the parity path.
            return self._reservoir.sample_replay(
                user_id, target_chars=target_chars, frac=frac, seed=seed,
                before_window=before_window)
        if source == "rawlog":
            # The locked architecture: pool the RAW prior day-logs, re-read through
            # the day-log client, never a separate amplified store. `prior_windows`
            # are the windows already consolidated for this user (the cycle knows
            # them); each is fetched and its block text pooled.
            if self._daylog is None:
                raise ValueError("rawlog replay needs a day-log client — "
                                 "construct the reservoir client with one")
            if not prior_windows:
                return ""
            sources = [blocks_text(self._daylog.fetch_daylog(win).blocks)
                       for win in prior_windows]
            return sample_pooled(sources, target_chars=target_chars, frac=frac, seed=seed)
        raise ValueError(f"unknown replay source {source!r} (expected 'amp' or 'rawlog')")


class HttpReservoirClient:
    """Storage backend: C14.

    `admit` is append-only and idempotent on identical content; `entries` reads the
    ledger (torn admissions are invisible there by construction — the corpus body
    lands atomically first and the meta record is the commit marker).

    `ReservoirEntry.path` is empty here and that is deliberate: a path is a fact
    about someone else's disk, and the ledger answers "what has been admitted", not
    "give me the text".
    """

    def __init__(self, storage_url: str, *, timeout: float = 60.0, transport=None,
                 daylog_client: DayLogClient | None = None):
        self.storage_url = storage_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport
        self._daylog = daylog_client

    @staticmethod
    def _entry(row: dict) -> ReservoirEntry:
        return ReservoirEntry(user_id=row["user_id"], window_id=row["window_id"],
                              recipe_id=row["recipe_id"], path="",
                              sha=row.get("sha", ""))

    def admit(self, user_id: str, window_id: str, recipe_id: str,
              corpus_text: str) -> ReservoirEntry:
        """Admit this night's corpus. A CONFLICT is not fatal — the existing entry
        stands and the night proceeds.

        C14 is append-only by charter, so re-admitting a window with a DIFFERENT
        corpus is a 409. That is the right answer for the reservoir and the wrong
        answer for the cycle: the publish tail reaches admission after the adapter
        is already published, so a hard failure here strands the night short of its
        terminal journal record — and since the conflict is permanent, every retry
        fails at the same point while appending more C5 rows. Measured before this
        fix: three `active` rows for one window and a night that could never
        complete, recoverable only by deleting a reservoir entry, which the charter
        defines as a deliberate privacy act rather than an operation.

        The corpus differing means the night was re-materialized (an operator
        correcting `home_tz` re-renders the day-log). Append-only says the FIRST
        admission stands; the audit trail is intact either way, and provenance must
        never be the thing that blocks consolidation."""
        try:
            _, body = request_json(
                "POST", f"{self.storage_url}/reservoir/{user_id}/{window_id}",
                json_body={"recipe_id": recipe_id, "corpus_text": corpus_text},
                timeout=self.timeout, transport=self.transport)
        except httpx.HTTPStatusError as exc:
            if exc.response is None or exc.response.status_code != 409:
                raise
            existing = [e for e in self.entries(user_id) if e.window_id == window_id]
            if not existing:
                raise
            log.warning(
                "reservoir already holds a corpus for (%s, %s) with different "
                "content — the first admission stands (C14 is append-only) and the "
                "night proceeds; this window was re-materialized", user_id, window_id)
            return existing[0]
        return self._entry(body)

    def entries(self, user_id: str, *,
                before_window: str | None = None) -> list[ReservoirEntry]:
        params: dict[str, str] = {}
        if before_window is not None:
            params["before_window"] = before_window
        _, body = request_json(
            "GET", f"{self.storage_url}/reservoir/{user_id}", params=params or None,
            timeout=self.timeout, transport=self.transport)
        if body.get("contract") != "C14" or str(body.get("version")) != "0":
            raise ValueError(
                f"expected a C14 v0 ledger body, got contract={body.get('contract')!r} "
                f"version={body.get('version')!r}")
        return [self._entry(row) for row in body["entries"]]

    def sample_replay(self, user_id: str, *, target_chars: int, frac: float, seed: int,
                      before_window: str | None = None, source: str = "amp",
                      prior_windows: list[Window] | None = None) -> str:
        if source == "rawlog":
            # The locked architecture, and the only replay source that has an HTTP
            # implementation: pool the RAW prior day-logs, re-read through the C10
            # day-log fetch. `prior_windows` come from the window-ledger ENUMERATION
            # read — never from parsing a window id.
            if self._daylog is None:
                raise ValueError("rawlog replay needs a day-log client — "
                                 "construct the reservoir client with one")
            if not prior_windows:
                return ""
            sources = [blocks_text(self._daylog.fetch_daylog(win).blocks)
                       for win in prior_windows]
            return sample_pooled(sources, target_chars=target_chars, frac=frac, seed=seed)
        if source == "amp":
            # Two cases where there is provably nothing to replay, and they behave
            # exactly as the local backend does — a night must not be blocked by the
            # absence of something it was never going to use:
            if frac <= 0:
                return ""                       # the SEQ arm: rehearsal is OFF
            if not self.entries(user_id, before_window=before_window):
                return ""                       # first night: empty reservoir
            # Past this point the night WOULD have rehearsed and cannot. That is NOT
            # a gap to fill later: C14 serves the LEDGER, not the corpora, and `amp`
            # replay needs the bodies. Failing here is the honest outcome — the
            # alternative is a night that silently trains with no rehearsal at all,
            # which is precisely the collapse replay exists to prevent.
            raise NotImplementedError(
                "replay_source='amp' has no HTTP implementation: C14 serves the "
                "reservoir LEDGER (provenance), not the amplified corpus bodies. "
                "The locked architecture replays raw prior day-logs via C10 — use a "
                "recipe with replay.source='rawlog' against storage, or keep the "
                "local reservoir backend (CONTINUUM_STORAGE_CLIENTS=local) for the "
                "amp-pinned parity recipes")
        raise ValueError(f"unknown replay source {source!r} (expected 'amp' or 'rawlog')")
