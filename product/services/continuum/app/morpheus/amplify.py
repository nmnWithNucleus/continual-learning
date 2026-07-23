"""Amplification — the write step of the recipe.

Facts do not enter weights by being shown once in the form they were recorded.
They enter by being restated many ways, from many angles, with the anchors woven
into the prose each time (EntiGraph, arXiv 2409.07431). So each block is expanded
into `variants` retellings that state the same facts differently, and a
`neg_frac` slice of those are deny-then-correct paragraphs which is where refusal
calibration comes from. Recipe v1.0: 48x, 15% negatives.

Two invariants make this safe to run unattended:

  ok-rate gate  — a generator that silently degrades produces text that fails the
                  profile's validity check. Below `ok_rate_min` the night ABORTS
                  rather than training on rubble; the prior adapter keeps serving.
  RNG stream    — style assignment, negative selection, and the final shuffle all
                  draw from ONE seeded stream in a fixed order, so a night is
                  reproducible from (seed, blocks) alone.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Sequence

from .blocks import Block
from .generate import Generator


@dataclass(frozen=True)
class AmplifyJob:
    block_index: int
    block_id: str
    style: str
    prompt: str
    negative: bool


@dataclass(frozen=True)
class Narrative:
    block_id: str
    text: str


@dataclass(frozen=True)
class AmplifyResult:
    corpus: str                                  # the CPT corpus: paragraphs, blank-line separated
    narratives: list[Narrative] = field(default_factory=list)
    ok: int = 0
    err: int = 0
    planned: int = 0
    planned_negatives: int = 0

    @property
    def ok_rate(self) -> float:
        return self.ok / max(1, self.ok + self.err)

    @property
    def neg_frac(self) -> float:
        """Fraction of PLANNED jobs that were negatives. Planned, not surviving:
        the validity gate must not be able to quietly rebalance calibration."""
        return self.planned_negatives / max(1, self.planned)

    @property
    def chars(self) -> int:
        return sum(len(n.text) for n in self.narratives)

    def stats(self) -> dict:
        return {"ok": self.ok, "err": self.err, "ok_rate": round(self.ok_rate, 3),
                "planned": self.planned, "planned_negatives": self.planned_negatives,
                "neg_frac": round(self.neg_frac, 4), "chars": self.chars}


class AmplifyBelowOkRate(RuntimeError):
    """The generator produced too little usable text to train on tonight."""


def plan(blocks: Sequence[Block], profile, *, variants: int, neg_frac: float,
         rng: random.Random) -> list[AmplifyJob]:
    """Build every amplification request for one day.

    Style rotation is `(variant + draw) % len(styles)`: the per-variant offset
    guarantees a block's retellings spread across the style set instead of
    clustering, while the draw stops every block from getting the same rotation.
    When `neg_frac` is 0 no draw is made at all — a calibration-free recipe must
    consume the identical stream a `variants`-only run would."""
    jobs: list[AmplifyJob] = []
    styles = profile.styles
    for i, block in enumerate(blocks):
        for v in range(variants):
            negative = bool(neg_frac) and rng.random() < neg_frac
            style = profile.neg_style if negative else styles[(v + rng.randrange(len(styles)))
                                                              % len(styles)]
            jobs.append(AmplifyJob(block_index=i, block_id=block.block_id, style=style,
                                   prompt=profile.amplify_prompt(block, style),
                                   negative=negative))
    return jobs


def collect(jobs: Sequence[AmplifyJob], generations: Sequence[str],
            blocks: Sequence[Block], profile, *, ok_rate_min: float,
            rng: random.Random) -> AmplifyResult:
    """Validity-gate the generations and shuffle them into a CPT corpus.

    The shuffle matters: CPT sees the corpus as a stream of independent
    paragraphs, and leaving them in block order would let the model learn the
    day's running order as a shortcut instead of the facts."""
    if len(generations) != len(jobs):
        raise ValueError(f"generator returned {len(generations)} texts for {len(jobs)} jobs")
    kept: list[Narrative] = []
    err = 0
    for job, text in zip(jobs, generations):
        if profile.is_valid(text, blocks[job.block_index]):
            kept.append(Narrative(block_id=job.block_id, text=text.strip()))
        else:
            err += 1
    ok_rate = len(kept) / max(1, len(jobs))
    if ok_rate < ok_rate_min:
        raise AmplifyBelowOkRate(
            f"amplification ok-rate {ok_rate:.3f} < {ok_rate_min} "
            f"({len(kept)}/{len(jobs)} valid) — refusing to consolidate tonight; "
            "the prior adapter keeps serving and this window becomes debt")
    shuffled = [n.text for n in kept]
    rng.shuffle(shuffled)
    return AmplifyResult(corpus="\n\n".join(shuffled), narratives=kept, ok=len(kept),
                         err=err, planned=len(jobs),
                         planned_negatives=sum(1 for j in jobs if j.negative))


def amplify(blocks: Sequence[Block], generator: Generator, profile, *,
            variants: int, neg_frac: float, ok_rate_min: float,
            seed: int) -> AmplifyResult:
    """One day of blocks -> one night's amplified corpus.

    `seed` is the FULL stream seed. Callers decorrelate consecutive days
    themselves (the research chain uses `base_seed + day_number`); the kernel
    stays ignorant of what a "day" is."""
    rng = random.Random(seed)
    jobs = plan(blocks, profile, variants=variants, neg_frac=neg_frac, rng=rng)
    return collect(jobs, generator([j.prompt for j in jobs]), blocks, profile,
                   ok_rate_min=ok_rate_min, rng=rng)
