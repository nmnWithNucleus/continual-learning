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

`train()` takes the mixed corpus AND the new-day-only corpus, because the
rehearsal mix is matched-compute: the per-epoch step budget is what the new day
alone would have cost, so replay DISPLACES new-day chunks rather than buying
extra gradient steps. Passing only the mixed corpus would silently make a
30%-rehearsal night a 30%-longer night.

`mock` (default) runs the whole cycle headless-green with no GPU. `morpheus`
is our real nightly-consolidation core (app/morpheus/, ws-morpheus-port):
recipe-coupled amplification, LoRA CPT, and the judged closed-book eval.
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
    """Raw scores. COUNTS, not just rates, for heldout: the contamination check is
    an exact test against the run's own base control, and a rate cannot express
    2/60 vs 5/222 — the same 0.033 carries very different evidence."""
    new_day_recall: float
    traps_pass: float
    heldout_hits: int = 0
    heldout_n: int = 0
    base_heldout_hits: int = 0
    base_heldout_n: int = 0
    n_probes: int = 0
    extras: dict = field(default_factory=dict)  # decay/general/read-skill land here

    @property
    def heldout_recall(self) -> float:
        return self.heldout_hits / self.heldout_n if self.heldout_n else 0.0


class TrainerBackend(Protocol):
    name: str

    def amplify(self, blocks: list[Block], recipe: Recipe, *, seed: int) -> AmplifyResult: ...

    def train(self, corpus_path: str, recipe: Recipe, *, out_dir: str,
              resume_adapter: str | None,
              new_day_corpus_path: str | None = None) -> TrainResult: ...

    def evaluate(self, adapter_dir: str, blocks: list[Block], recipe: Recipe) -> EvalScores: ...


def get_backend(name: str) -> TrainerBackend:
    if name == "mock":
        from .mock import MockBackend
        return MockBackend()
    if name == "morpheus":
        from .morpheus import MorpheusBackend
        return MorpheusBackend()
    raise ValueError(f"unknown TRAINER_BACKEND {name!r}")
