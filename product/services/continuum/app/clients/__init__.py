"""Storage client seams — the boundary continuum consumes storage across.

The lean architecture (ws-morpheus-port §2, storage CHARTER § Scope note) puts the
data-shaped concerns in storage: the day-log (a derived view over C2), the
training-window ledger + watermark, the recipe registry (versioned config, pulled
by continuum *and* inference), the reservoir (amplified-corpus custody), and the
per-user profile. Continuum *consumes* all of them.

This package is the CLIENT side. Each seam is an interface; three of them have a
LOCAL implementation and an HTTP-to-storage implementation, and which one runs is
a SETTINGS choice (`CONTINUUM_STORAGE_CLIENTS=local|http`) — never a redesign of
the cycle:

    day_log_client   fetch the rendered segment/block day-log for (user, window)
    recipe_registry  fetch the pinned training recipe and the gate policy by id
    reservoir_client write the amplified corpus; read the ledger; replay
    window_ledger    open/close windows, enumerate a user's consolidated ones
    profile_client   the C12 `home_tz` read

Two of them are HTTP-ONLY, deliberately:

  * **`window_ledger`** — a `window_id` is minted in exactly one place in the whole
    system, and the watermark is a fact about storage's own ingest clock. A local
    minter would be a second place, and the lexicographic-ordering invariant that
    the journal, the reservoir's `before_window` filter and publish's alias guard
    all depend on is only as strong as the discipline that mints ids.
  * **`profile_client`** — `home_tz` is declared by the user and stored once. A
    local fallback would be a default timezone, and D17 abolished those.

`http` is the DEFAULT, because it is the path `scripts/seam_check.py` drives against
the real service; a default that bypasses the seam ships a configuration nobody
exercises.

The LOCAL day-log / registry / reservoir backends are NOT dead code: the local
day-log path is the parity reference the storage-side differential diff is measured
against (storage CHARTER M9), and two independent renderers are the only thing that
can catch a renderer defect. But note the exact
shape of what survives — the local day-log client answers **"here are the records,
render them"** and nothing else. It cannot SOURCE a training day-log; see
`day_log_client` and `IngestWindowNotReadable` below.
"""
from __future__ import annotations

from .daylog_client import (SUPPORTED_DAYLOG_FORMAT_VERSIONS, DayLogClient,
                            DayLogDialectMismatch, DayLogUnavailable,
                            HttpDayLogClient, LocalDayLogClient, RecordProvider)
from .profile_client import HttpProfileClient, ProfileClient, UserNotSchedulable
from .registry import HttpRecipeRegistry, LocalRecipeRegistry, RecipeRegistry
from .reservoir_client import (HttpReservoirClient, LocalReservoirClient,
                               ReservoirClient)
from .window_client import HttpWindowLedger, WindowLedger, WindowNotOpenable

__all__ = [
    "DayLogClient", "LocalDayLogClient", "HttpDayLogClient", "DayLogUnavailable",
    "DayLogDialectMismatch", "SUPPORTED_DAYLOG_FORMAT_VERSIONS", "RecordProvider",
    "RecipeRegistry", "LocalRecipeRegistry", "HttpRecipeRegistry",
    "ReservoirClient", "LocalReservoirClient", "HttpReservoirClient",
    "WindowLedger", "HttpWindowLedger", "WindowNotOpenable",
    "ProfileClient", "HttpProfileClient", "UserNotSchedulable",
    "day_log_client", "recipe_registry", "reservoir_client", "window_ledger",
    "profile_client", "IngestWindowNotReadable",
]


class IngestWindowNotReadable(RuntimeError):
    """The local day-log backend was asked to SOURCE a training window's records.

    It cannot, and the failure it used to produce was the worst possible one: an
    empty day-log, hence `skipped_no_data`, hence a night that trained on nothing
    and reported a clean status.

    Why it cannot, stated once so nobody re-adds the query:

      * A training window is `[last_trained_t, now−δ)` on storage's **`updated_at`**
        axis (D18) — that is the completeness axis, the only one on which "everything
        I had" is a guarantee. `GET /context/records?from=&to=` filters `t_start`,
        which is **EVENT** time. Passing ingest bounds to an event-time filter is not
        an approximation; on a backlog it is disjoint, and it returned nothing.
      * Even given the right records, `build_daylog` would still be wrong here:
        `in_window(rec.t_start, win)` re-filters on event time, and `_bucket_index`
        indexes relative to the window START, which goes NEGATIVE for a record
        captured before the window opened and ingested inside it. D18 moved storage's
        grid to a global epoch for exactly that reason.

    So making the local path source its own records is not a missing query
    parameter — it is re-implementing storage's materializer inside continuum, which
    is the O(days²) duplication D18 rejected on the merits. Storage materializes;
    continuum fetches. The local client keeps its real job: rendering records the
    caller already holds (the M9 parity reference, a synthetic day, a Phase-3
    replay).
    """


def day_log_client(settings, recipe, *,
                   record_provider: RecordProvider | None = None) -> DayLogClient:
    """The day-log fetch client.

    `http` (the default): storage materialized it; we GET it.

    An explicit `record_provider` always selects the local backend: it means the
    caller has records in hand (a synthetic day, a test, a Phase-3 replay), which is
    a question storage cannot be asked — so it wins over the setting.

    `local` with NO provider is the one combination that has no answer, and it
    REFUSES. Every window that reaches here without records in hand came from the
    ledger, and the ledger is HTTP-only with exactly one minter, so such a window is
    ingest-axis by construction — which is precisely what the local backend cannot
    read. It used to answer it with an empty day-log; silence is the only option
    that is not allowed.

    `recipe` is handed to BOTH backends and for the same reason in mirror image: the
    local one RENDERS under its `corpus` knobs, and the HTTP one CHECKS the fetched
    body's `recipe_id` against its id. The expectation therefore tracks the recipe
    the cycle actually resolved, not whatever the environment says at fetch time.
    """
    if record_provider is None:
        if settings.storage_clients != "http":
            raise IngestWindowNotReadable(
                f"CONTINUUM_STORAGE_CLIENTS={settings.storage_clients!r} selects the "
                "LOCAL day-log backend, but no records were supplied and the local "
                "backend cannot source a training window's records: the window is on "
                "storage's `updated_at` axis and `GET /context/records` filters EVENT "
                "time (t_start). This used to yield an EMPTY day-log and a "
                "'skipped_no_data' night that had trained on nothing. Use "
                "CONTINUUM_STORAGE_CLIENTS=http (storage materializes the day-log, "
                "C10 v2), or pass an explicit record_provider if you genuinely have "
                "the records in hand.")
        return HttpDayLogClient(settings.storage_url, timeout=settings.http_timeout,
                                recipe_id=recipe.recipe_id)
    return LocalDayLogClient(record_provider,
                             segment_seconds=recipe.segment_seconds,
                             block_segments=recipe.block_segments)


def recipe_registry(settings) -> RecipeRegistry:
    """The recipe + gate-policy registry (C13). Local: the versioned files under the
    service's recipes/ and policies/ dirs, by id. HTTP: storage serves the same
    artifacts verbatim."""
    if settings.storage_clients == "http":
        return HttpRecipeRegistry(settings.storage_url, timeout=settings.http_timeout)
    return LocalRecipeRegistry(recipes_dir=settings.recipes_dir,
                               policies_dir=settings.policies_dir)


def reservoir_client(settings, *, daylog_client: DayLogClient | None = None) -> ReservoirClient:
    """The training reservoir (C14). Replay from raw prior day-logs is served
    through `daylog_client` when the recipe selects it; the amplified store stays
    audit/provenance, off the replay hot path."""
    if settings.storage_clients == "http":
        return HttpReservoirClient(settings.storage_url, timeout=settings.http_timeout,
                                   daylog_client=daylog_client)
    return LocalReservoirClient(settings.var_dir, daylog_client=daylog_client)


def window_ledger(settings) -> WindowLedger:
    """Storage's training-window ledger. HTTP-only by design — see the module
    docstring; there is exactly one minter of `window_id` and it is not here."""
    return HttpWindowLedger(settings.storage_url, timeout=settings.http_timeout)


def profile_client(settings) -> ProfileClient:
    """The C12 profile read. HTTP-only by design: a local fallback would be a
    default timezone, and there is no default timezone anywhere (D17)."""
    return HttpProfileClient(settings.storage_url, timeout=settings.http_timeout)
