"""GraphProcessor — the thin seam between the HTTP core and the stage graph.

One instance per modality. The graph itself comes from the stage registry
(``app/stages/<modality>/``) at call time — dropping a stage file in or out
changes the pipeline without touching this class — and ``pipeline_version`` is
resolved from code alone (L4: no settings, no env), BEFORE any stage runs.

This module also owns the modality routing the retired ``processing/`` package
used to: ``graph_processor(modality)`` raises ``KeyError`` for a modality with
no registered stages (the core maps that to a clean 501, not a crash).
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from .executor import GraphResult, resolve, run_graph
from .stage import registered_modalities, stages_for


class GraphProcessor:
    def __init__(self, modality: str) -> None:
        self.modality = modality

    def _resolved(self):
        return resolve(self.modality, stages_for(self.modality))

    def pipeline_version(self) -> str:
        """The attempted dialect — resolved from the registered stage set, in
 code, before any stage runs (L4)."""
        return self._resolved().pipeline_version

    async def process_async(
        self,
        c1: Mapping[str, Any],
        blob: bytes,
        span_seconds: float,
        *,
        clients: Optional[Mapping[str, Any]] = None,
        metrics: Any = None,
    ) -> GraphResult:
        """Run one chunk through this modality's graph: exactly one GraphResult
 (slots + statuses) or the leaf exception of a required failure."""
        return await run_graph(
            self._resolved(), c1=c1, blob=blob, span_seconds=span_seconds,
            clients=clients, metrics=metrics,
        )


def graph_processor(modality: str) -> GraphProcessor:
    """The modality router. Raises ``KeyError`` when no stage set exists for
 ``modality`` (C1-valid modality, no pipeline yet — the 501 path)."""
    if modality not in registered_modalities():
        raise KeyError(modality)
    return GraphProcessor(modality)
