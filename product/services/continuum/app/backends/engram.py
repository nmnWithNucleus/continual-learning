"""The real trainer backend — the engram research core, PORTED (not pinned).

Port policy (founder decision, 2026-07-22 session): files from
nucleus-research@continuum-research are ported into this service, adapted
in place (paths, prompts, envs), and evolve here; the source snapshot and a
divergence log live in handoff/ws-engram-port.md. Until that workstream lands,
this backend fails loudly — TRAINER_BACKEND=engram must never silently mock.
"""
from __future__ import annotations

from ..daylog import Block
from ..recipe import Recipe
from .base import AmplifyResult, EvalScores, TrainResult

_MSG = ("engram backend not ported yet — the port plan (file manifest, source "
        "commit pin, adaptation notes) is product/services/continuum/handoff/"
        "ws-engram-port.md; run with TRAINER_BACKEND=mock meanwhile")


class EngramBackend:
    name = "engram"

    def amplify(self, blocks: list[Block], recipe: Recipe, *, seed: int) -> AmplifyResult:
        raise NotImplementedError(_MSG)

    def train(self, corpus_path: str, recipe: Recipe, *, out_dir: str,
              resume_adapter: str | None) -> TrainResult:
        raise NotImplementedError(_MSG)

    def evaluate(self, adapter_dir: str, blocks: list[Block], recipe: Recipe) -> EvalScores:
        raise NotImplementedError(_MSG)
