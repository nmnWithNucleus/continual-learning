"""Storage client seams — the boundary continuum consumes storage across.

The lean architecture (ws-morpheus-port §2, storage CHARTER § Scope note) puts
three data-shaped concerns in storage: the day-log (a derived view over C2), the
recipe registry (versioned config, pulled by continuum and inference), and the
reservoir (amplified-corpus custody). Continuum *consumes* all three.

This package is the CLIENT side of those three. Each is an interface with a LOCAL
implementation today and an HTTP-to-storage implementation later — the same
posture the scaffold already uses for the reservoir and model directory. Swapping
to real storage is a transport change behind these interfaces, never a redesign
of the cycle:

    day_log_client   fetch the rendered segment/block day-log for (user, window)
    recipe_registry  fetch the pinned training recipe and the gate policy by id
    reservoir_client write the amplified corpus; read prior day-logs for replay

The factories below pick the local backend from settings. When storage lands,
they gain an `http` branch and nothing above them changes.
"""
from __future__ import annotations

from .daylog_client import DayLogClient, LocalDayLogClient, RecordProvider
from .registry import LocalRecipeRegistry, RecipeRegistry
from .reservoir_client import LocalReservoirClient, ReservoirClient

__all__ = [
    "DayLogClient", "LocalDayLogClient", "RecordProvider",
    "RecipeRegistry", "LocalRecipeRegistry",
    "ReservoirClient", "LocalReservoirClient",
    "day_log_client", "recipe_registry", "reservoir_client",
]


def day_log_client(settings, recipe, *,
                   record_provider: RecordProvider | None = None) -> DayLogClient:
    """The day-log fetch client, segmented per the recipe's day-log format.

    Local default: materialize from the beta /context range read. A caller with
    records already in hand (a synthetic day, a test) passes its own
    `record_provider`. When storage owns materialization this factory returns the
    HTTP client and the provider is gone."""
    if record_provider is None:
        from ..context_reader import fetch_window_records

        def record_provider(win):  # noqa: E306 — default provider: the C10 read
            return fetch_window_records(settings.storage_url, win,
                                        timeout=settings.http_timeout)
    return LocalDayLogClient(record_provider,
                             segment_seconds=recipe.segment_seconds,
                             block_segments=recipe.block_segments)


def recipe_registry(settings) -> RecipeRegistry:
    """The recipe + gate-policy registry. Local: reads the versioned files under
    the service's recipes/ and policies/ dirs by id."""
    return LocalRecipeRegistry(recipes_dir=settings.recipes_dir,
                               policies_dir=settings.policies_dir)


def reservoir_client(settings, *, daylog_client: DayLogClient | None = None) -> ReservoirClient:
    """The training reservoir. Local: filesystem under var_dir. Replay from raw
    prior day-logs is served through `daylog_client` when the recipe selects it;
    the amplified store stays audit/provenance, off the replay hot path."""
    return LocalReservoirClient(settings.var_dir, daylog_client=daylog_client)
