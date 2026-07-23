"""Amplification parity.

Amplification is stochastic at the token level but DETERMINISTIC in everything
that defines the recipe: how many paragraphs are requested, which of them are
deny-then-correct calibration, and which generations survive the validity gate.
Those are the parts tested here, and two of them are checked against the
reference corpora rather than against ourselves:

  neg-frac        our RNG stream's negative count is compared to the count of
                  denial-phrased paragraphs actually present in the reference
                  corpus — an independent measurement of the same quantity.
  validity + size the reference corpus is replayed back through our gate; it
                  must keep 100% of it and rebuild it to the exact character.
"""
from __future__ import annotations

import json
import math
import random

import pytest

from app.morpheus.amplify import AmplifyBelowOkRate, collect, plan
from app.morpheus.blocks import load_blocks

from . import goldens
from .conftest import needs_goldens

pytestmark = needs_goldens

# The RNG plan and the marker census measure the same quantity two different
# ways; they agree to a few paragraphs out of ~11.5k because the regex has a
# small false-positive/negative rate on free prose.
MARKER_CENSUS_TOL = 0.005


def _blocks(day: int):
    return load_blocks(goldens.blocks_path(day), extra_anchors={"day": day})


def _plan(day: int, profile, neg_frac: float = goldens.NEG_FRAC):
    return plan(_blocks(day), profile,
                variants=goldens.VARIANTS, neg_frac=neg_frac,
                rng=random.Random(goldens.AMPLIFY_SEED + day))


@pytest.mark.parametrize("day", goldens.DAYS)
def test_job_count_matches_the_reference_run(day, profile):
    jobs = _plan(day, profile)
    stats = goldens.amplify_stats(day)
    assert len(jobs) == len(_blocks(day)) * goldens.VARIANTS
    assert len(jobs) == stats["ok"] + stats["err"]


@pytest.mark.parametrize("day", goldens.DAYS)
def test_negative_fraction_matches_the_reference_corpus(day, profile):
    jobs = _plan(day, profile)
    planned = sum(1 for j in jobs if j.negative)
    kept = [json.loads(line) for line in
            goldens.narrative_path(day).read_text().splitlines() if line.strip()]
    observed = sum(1 for row in kept if profile.is_calibration(row["text"]))

    # Negatives are drawn per job, so the realized fraction is binomial around
    # 0.15. Three sigma, derived rather than guessed — a hand-picked tolerance
    # either flags healthy runs or hides a real drift, depending on the day's n.
    sigma = math.sqrt(goldens.NEG_FRAC * (1 - goldens.NEG_FRAC) / len(jobs))
    assert planned / len(jobs) == pytest.approx(goldens.NEG_FRAC, abs=3 * sigma), (
        "planned calibration fraction drifted from recipe v1.0's 15%")
    assert planned / len(jobs) == pytest.approx(observed / len(kept), abs=MARKER_CENSUS_TOL), (
        f"day {day}: our stream plans {planned} negatives but the reference corpus "
        f"contains {observed} denial-phrased paragraphs of {len(kept)}")


@pytest.mark.parametrize("day", goldens.DAYS)
def test_styles_rotate_across_the_full_set(day, profile):
    """Every positive style must actually get used. A rotation bug that collapsed
    48 variants onto one style would still produce 48 paragraphs and a perfect
    ok-rate — and a corpus with no diversity, which is the thing that fails."""
    positives = [j.style for j in _plan(day, profile) if not j.negative]
    assert set(positives) == set(profile.styles)


def test_zero_neg_frac_consumes_no_calibration_draws(profile):
    """A calibration-free recipe must draw the IDENTICAL stream a variants-only
    run would. If turning negatives off still burned a draw per job, the two
    recipes would disagree on every style assignment and could not be compared."""
    blocks = _blocks(goldens.DAYS[0])[:5]
    jobs = plan(blocks, profile, variants=4, neg_frac=0.0, rng=random.Random(1))
    oracle = random.Random(1)
    expected = [profile.styles[(v + oracle.randrange(len(profile.styles)))
                               % len(profile.styles)]
                for _ in blocks for v in range(4)]
    assert not any(j.negative for j in jobs)
    assert [j.style for j in jobs] == expected


@pytest.mark.parametrize("day", goldens.DAYS)
def test_validity_gate_accepts_every_reference_paragraph(day, profile):
    """The reference corpus is exactly what the reference validity check kept.
    Ours must keep all of it — a stricter gate silently shrinks the corpus."""
    blocks = _blocks(day)
    by_id = {b.block_id: b for b in blocks}
    kept = [json.loads(line) for line in
            goldens.narrative_path(day).read_text().splitlines() if line.strip()]
    rejected = [row for row in kept
                if not profile.is_valid(row["text"], by_id[row["block_id"]])]
    assert not rejected, (f"day {day}: our validity check rejects {len(rejected)} of "
                          f"{len(kept)} reference paragraphs, e.g. {rejected[0]['text'][:120]!r}")


def test_corpus_rebuild_is_character_exact(profile):
    """Replay the reference generations back through our collector.

    Day 5 is the anchor because its reference run dropped nothing (err=0), so the
    reconstruction is total rather than approximate: same paragraph multiset,
    same character count, ratio exactly 1.0. The ORDER differs by design — the
    shuffle draws from a stream that has already served the job plan — so the
    comparison is over the multiset, and length is order-invariant."""
    day = 5
    stats = goldens.amplify_stats(day)
    assert stats["err"] == 0, "day 5 is the exact-rebuild anchor"
    blocks = _blocks(day)
    jobs = _plan(day, profile)

    pending: dict[str, list[str]] = {}
    for line in goldens.narrative_path(day).read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            pending.setdefault(row["block_id"], []).append(row["text"])
    cursor = dict.fromkeys(pending, 0)
    generations = []
    for job in jobs:
        generations.append(pending[job.block_id][cursor[job.block_id]])
        cursor[job.block_id] += 1

    result = collect(jobs, generations, blocks, profile,
                     ok_rate_min=0.85, rng=random.Random(0))
    golden_corpus = goldens.corpus_path(day).read_text()
    assert (result.ok, result.err, result.ok_rate) == (stats["ok"], 0, 1.0)
    assert result.chars == stats["chars"]
    assert len(result.corpus) == len(golden_corpus), "corpus rebuild ratio must be exactly 1.0"
    assert sorted(result.corpus.split("\n\n")) == sorted(golden_corpus.split("\n\n"))


def test_ok_rate_floor_aborts_the_night(profile):
    """A silently-degraded generator must abort, not train on rubble."""
    blocks = _blocks(goldens.DAYS[0])[:4]
    jobs = plan(blocks, profile, variants=2, neg_frac=0.15, rng=random.Random(3))
    with pytest.raises(AmplifyBelowOkRate, match="refusing to consolidate"):
        collect(jobs, ["too short"] * len(jobs), blocks, profile,
                ok_rate_min=0.85, rng=random.Random(0))
