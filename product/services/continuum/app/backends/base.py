"""The TRAINER_BACKEND seam — the boundary the ported research core plugs into.

The nightly ORCHESTRATION (window read, day-log build, replay mix, gate
decision, publish/rollback, journaling) is service-owned and identical across
backends. What a backend supplies is the three GPU/LLM-shaped steps:

    amplify()   day-log blocks -> amplified training text (styled retellings +
                deny-then-correct negatives), validity-gated by ok-rate
    train()     mixed corpus -> adapter artifact directory (continue the ONE
                life adapter; plain next-token CPT per recipe)
    evaluate()  candidate adapter -> raw eval scores (the gate VERDICT —
                thresholds, two-strike — stays service-owned in gate.py)

`mock` (default) runs the whole cycle headless-green with no GPU. `engram` is
the ported research core (ws-engram-port); until the port lands it fails
loudly with a pointer, never silently.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ..daylog import Block
from ..recipe import Recipe


@dataclass(frozen=True)
class AmplifyResult:
    text: str            # the amplified corpus (paragraphs separated by blank lines)
    ok_rate: float       # generator validity rate; < recipe.ok_rate_min aborts the night
    n_variants: int
    n_negatives: int


@dataclass(frozen=True)
class TrainResult:
    adapter_dir: str     # directory containing the adapter artifact
    adapter_version: str # content-derived version id
    meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class EvalScores:
    new_day_recall: float
    traps_pass: float
    heldout_recall: float
    n_probes: int
    extras: dict = field(default_factory=dict)  # decay/general/read-skill land here


class TrainerBackend(Protocol):
    name: str

    def amplify(self, blocks: list[Block], recipe: Recipe, *, seed: int) -> AmplifyResult: ...

    def train(self, corpus_path: str, recipe: Recipe, *, out_dir: str,
              resume_adapter: str | None) -> TrainResult: ...

    def evaluate(self, adapter_dir: str, blocks: list[Block], recipe: Recipe) -> EvalScores: ...


def get_backend(name: str) -> TrainerBackend:
    if name == "mock":
        from .mock import MockBackend
        return MockBackend()
    if name == "engram":
        from .engram import EngramBackend
        return EngramBackend()
    raise ValueError(f"unknown TRAINER_BACKEND {name!r}")
