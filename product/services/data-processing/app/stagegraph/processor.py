"""GraphProcessor — the thin adapter that plugs a stage graph into the EXISTING seam.

Subclasses set ``modality`` (+ ``content_kind`` documentation) and register via the same
``@register`` decorator the monolithic processors used, so the registry, the HTTP core,
and every seam test keep working unchanged. The graph itself comes from the stage
registry (``app/stages/<modality>/``) at call time — dropping a stage file in or out
changes the pipeline without touching this class.

Two entry points:
  * ``process_async`` — the real path. ``ingest_core.process_chunk`` awaits it directly
    on the event loop; stages do their own offloading (``run_sync`` → worker thread) or
    native awaiting (``run_async``), so intra-chunk concurrency is real.
  * ``process`` — the sync Processor-protocol shim (``asyncio.run``). Works anywhere no
    event loop is running (e.g. legacy direct calls in a thread); a caller INSIDE a
    running loop must use ``process_async`` (asyncio.run would raise — deliberately).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from ..config import Settings
from ..processing.base import ProcessedUnit, Processor
from .executor import resolve, run_graph
from .stage import StageContext, stages_for


class GraphProcessor(Processor):
    modality: str = ""
    content_kind: str = ""

    def _resolved(self, settings: Settings):
        return resolve(self.modality, stages_for(self.modality), settings)

    def pipeline_version(self, settings: Settings) -> str:
        return self._resolved(settings).pipeline_version

    async def process_async(
        self,
        c1: dict[str, Any],
        blob: bytes,
        settings: Settings,
        span_seconds: float,
        resources: Any = None,
    ) -> list[ProcessedUnit]:
        resolved = self._resolved(settings)
        ctx = StageContext(
            c1=c1, blob=blob, settings=settings, span_seconds=span_seconds,
            resources=resources or SimpleNamespace(metrics=None, vlm_pool=None),
        )
        return await run_graph(resolved, ctx)

    def process(
        self,
        c1: dict[str, Any],
        blob: bytes,
        settings: Settings,
        span_seconds: float,
    ) -> list[ProcessedUnit]:
        return asyncio.run(self.process_async(c1, blob, settings, span_seconds))
