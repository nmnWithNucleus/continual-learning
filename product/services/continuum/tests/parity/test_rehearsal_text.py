"""Rehearsal-text parity — WHICH paragraphs get replayed, not just how many.

The chunk-count fingerprint (test_replay_and_chunking.py) constrains the mixed
corpus's total token volume. It does NOT pin the sampled text: one paragraph of
difference is ~250 tokens and usually will not cross a 1024-token boundary. That
left the actual rehearsal content as the one unverified kernel surface, and the
obvious suspect whenever a chain underperforms.

It is now pinned. The reference sampler is deterministic given (seed, corpora) and
both are on disk, so the rehearsal text it WOULD select is recoverable even though
no run ever dumped it. Diffed against ours it is byte-identical on every night of
every seed checked; the hashes below are frozen from that comparison.

Hashing rather than storing the text: the five nights of one chain are ~15 MB.
"""
from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

import pytest

from app.morpheus.replay import sample_replay

from . import goldens
from .conftest import needs_goldens

pytestmark = needs_goldens

GOLDENS = json.loads((Path(__file__).parent / "rehearsal_goldens.json").read_text())


def _chain_hashes(seed: int) -> list[str]:
    """The rehearsal text for each night of a chain, as the chain would draw it.

    Consumed in chain order on ONE stream: night 5's selection depends on every
    draw nights 1-4 made, so a per-night reseed would be a different sampler."""
    corpora = {d: goldens.corpus_path(d).read_text() for d in goldens.DAYS}
    rng = random.Random(seed)
    out = []
    for step, day in enumerate(goldens.DAYS):
        if not step:
            continue                                  # night one has nothing to rehearse
        text = sample_replay([corpora[d] for d in goldens.DAYS[:step]],
                             frac=goldens.REPLAY_FRAC,
                             target_chars=len(corpora[day]), rng=rng)
        out.append(hashlib.sha256(text.encode()).hexdigest()[:16])
    return out


@pytest.mark.parametrize("key", sorted(GOLDENS))
def test_rehearsal_text_is_byte_identical_to_the_reference_sampler(key):
    assert _chain_hashes(int(key.removeprefix("seed"))) == GOLDENS[key], (
        f"{key}: the rehearsal text our sampler selects has changed. These hashes were "
        "taken from a byte-for-byte diff against the reference sampler at b3c58e1 — a "
        "change here means we no longer rehearse the same paragraphs, which is invisible "
        "to the chunk-count check.")


def test_the_production_chain_seed_is_covered():
    """Seed 7 is the stream the reference chain and our parity chains both used."""
    assert "seed7" in GOLDENS and len(GOLDENS["seed7"]) == len(goldens.DAYS) - 1
