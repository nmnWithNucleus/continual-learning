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
differs. Local backend today, HTTP-to-storage later, same interface.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..renderer import blocks_text
from ..reservoir import Reservoir, ReservoirEntry, sample_pooled
from ..window import Window
from .daylog_client import DayLogClient


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
