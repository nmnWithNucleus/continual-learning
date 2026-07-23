"""Replay + chunking parity — the chain's strongest deterministic anchor.

The reference chain never dumped its rehearsal text, so there is no file to diff
a sampler against. But it recorded something better: for every night, how many
1024-token chunks the MIXED corpus produced, how many of those fit the
matched-compute budget, and how many gradient steps that became.

Those three integers are a fingerprint of the entire deterministic half of a
night. Reproducing them requires getting all of it right simultaneously:

  * the rehearsal RNG stream, seeded ONCE per chain and consumed across nights,
    so night 4's selection depends on every draw nights 1-3 made
  * which paragraphs are eligible (>100 chars) and in what pooled order
  * the character budget (frac x the NEW day's length, not the mixed length)
  * the greedy fill's overshoot-by-one-paragraph behavior
  * tokenization and the chunk slicing, including the off-by-one at the tail
  * the step schedule over the budgeted subset

Any one of them wrong moves the counts. All six nights matching, on two
independent reference runs, is as close to proof as a rewrite can get.
"""
from __future__ import annotations

import random

import pytest

from app.morpheus.replay import sample_replay
from app.morpheus.train import chunk_corpus

from . import goldens
from .conftest import needs_goldens, needs_tokenizer

pytestmark = needs_goldens

# Both runs recorded identical counts, which is itself the claim that this half
# of a night is deterministic — they differ only in the training seed.
FINGERPRINTED_RUNS = ("replay_f30", goldens.REPRODUCTION)


def _steps(chunks_per_epoch: int, batch_size: int, epochs: int) -> int:
    """The training loop's step schedule: whole batches only, per epoch."""
    return epochs * len(range(0, chunks_per_epoch - batch_size + 1, batch_size))


@pytest.fixture(scope="module")
def chain_fingerprint(tokenizer):
    """Re-run the deterministic half of the reference chain and record its shape."""
    corpora = {day: goldens.corpus_path(day).read_text() for day in goldens.DAYS}
    rng = random.Random(goldens.REPLAY_SEED)
    out = {}
    for step, day in enumerate(goldens.DAYS):
        new_day = corpora[day]
        budget = len(chunk_corpus(tokenizer, new_day, goldens.SEQ_LEN))
        text = new_day
        if step:
            rehearsal = sample_replay([corpora[d] for d in goldens.DAYS[:step]],
                                      frac=goldens.REPLAY_FRAC,
                                      target_chars=len(new_day), rng=rng)
            text = f"{new_day}\n\n{rehearsal}"
        chunks = len(chunk_corpus(tokenizer, text, goldens.SEQ_LEN))
        per_epoch = min(chunks, budget)
        out[f"s{step}_d{day}"] = {
            "chunks": chunks, "chunks_per_epoch": per_epoch,
            "steps": _steps(per_epoch, goldens.BATCH_SIZE, goldens.EPOCHS),
            "replay_chars": len(text) - len(new_day) - (2 if step else 0)}
    return out


@pytest.mark.parametrize("run", FINGERPRINTED_RUNS)
@needs_tokenizer
def test_chain_fingerprint_matches_the_reference_run(chain_fingerprint, run):
    reference = goldens.train_report(run)["train"]
    for key, expected in reference.items():
        ours = chain_fingerprint[key]
        assert ours["chunks"] == expected["chunks"], f"{run} {key}: mixed-corpus chunk count"
        assert ours["chunks_per_epoch"] == expected["chunks_per_epoch"], (
            f"{run} {key}: matched-compute budget")
        assert ours["steps"] == expected["steps"], f"{run} {key}: gradient-step schedule"


@needs_tokenizer
def test_rehearsal_displaces_rather_than_adds(chain_fingerprint):
    """Matched compute, stated as the property it exists to guarantee: every
    night after the first has MORE material than it trains on, and its step
    count is set by the new day alone."""
    first = chain_fingerprint[f"s0_d{goldens.DAYS[0]}"]
    assert first["chunks"] == first["chunks_per_epoch"], "night one has nothing to rehearse"
    for step, day in enumerate(goldens.DAYS[1:], start=1):
        night = chain_fingerprint[f"s{step}_d{day}"]
        assert night["chunks"] > night["chunks_per_epoch"], (
            f"night {step} trained on its whole mixed corpus — rehearsal ADDED steps")


@needs_tokenizer
def test_rehearsal_budget_tracks_the_new_day(chain_fingerprint):
    """The mix is ~30% of the NEW day's size, overshooting by at most one
    paragraph — never a fraction of the growing mixed corpus."""
    for step, day in enumerate(goldens.DAYS[1:], start=1):
        new_day_chars = len(goldens.corpus_path(day).read_text())
        got = chain_fingerprint[f"s{step}_d{day}"]["replay_chars"]
        assert got >= goldens.REPLAY_FRAC * new_day_chars
        assert got / new_day_chars == pytest.approx(goldens.REPLAY_FRAC, abs=0.001)


# --------------------------------------------------------------- sampler behavior

def test_selection_is_reproducible_from_the_seed():
    sources = [goldens.corpus_path(goldens.DAYS[0]).read_text()[:400_000]]
    first = sample_replay(sources, frac=0.3, target_chars=100_000, rng=random.Random(7))
    second = sample_replay(sources, frac=0.3, target_chars=100_000, rng=random.Random(7))
    assert first == second and first
