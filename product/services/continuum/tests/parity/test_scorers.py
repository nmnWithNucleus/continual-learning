"""Scorer parity — exact, on the reference transcripts.

The trap rate is the one gate check that runs with no network, so it is the one
that must never drift. Here our `trap_score` is re-run over the reference runs'
own prediction logs and compared to the trap rates recorded in their published
analysis. Same transcripts in, same numbers out, or the calibration gate is
measuring something other than what the goldens measured.
"""
from __future__ import annotations

import json
from statistics import mean

import pytest

from app.morpheus.eval import trap_rates
from app.morpheus.scorers import TRAP_MARKERS, contains, f1, order_score, trap_score

from . import goldens
from .conftest import needs_goldens


@needs_goldens
@pytest.mark.parametrize("run", goldens.SEED_ENSEMBLE)
def test_trap_rates_reproduce_the_reference_analysis(run):
    published = json.loads((goldens.PHASED / "analysis.json").read_text())[run]["D4_traps_by_step"]
    recomputed = trap_rates(goldens.prediction_rows(run), len(goldens.DAYS))
    assert {str(k): v for k, v in recomputed.items()} == published


@needs_goldens
def test_trap_scoring_is_stable_across_every_reference_transcript():
    """Every trap prediction in every reference run, scored twice — the marker
    list is a frozen contract, so a re-score must be bit-identical."""
    for run in (*goldens.SEED_ENSEMBLE, goldens.REPRODUCTION):
        traps = [r for r in goldens.prediction_rows(run) if r["suite"].endswith("_traps")]
        assert traps, f"{run} has no trap predictions"
        first = [trap_score("", r["pred"]) for r in traps]
        assert first == [trap_score("", r["pred"]) for r in traps]
        assert 0.0 <= mean(first) <= 1.0


def test_trap_markers_are_a_frozen_contract():
    """A changed marker list silently re-bases every historical trap number."""
    assert len(TRAP_MARKERS) == 27
    assert TRAP_MARKERS[0] == "didn't" and TRAP_MARKERS[-1] == "not mention"
    assert len(set(TRAP_MARKERS)) == len(TRAP_MARKERS), "duplicate marker"


def test_trap_score_needs_a_refusal_not_just_a_negative_word():
    assert trap_score("", "He never visited Anchorage.") == 1.0
    assert trap_score("", "There is no record of that stop.") == 1.0
    assert trap_score("", "He ate a burger at the Anchorage stadium.") == 0.0


def test_f1_is_multiset_token_overlap_with_articles_dropped():
    assert f1("a red car", "the red car") == 1.0            # articles are noise
    assert f1("red car", "red car red car") == pytest.approx(2 / 3)
    assert f1("red", "blue") == 0.0
    assert f1("", "anything") == 0.0


def test_contains_normalizes_punctuation_and_case():
    assert contains("Bengals helmet", "a white shirt with a BENGALS HELMET logo") == 1.0
    assert contains("Bengals helmet", "a helmet with a Bengals logo") == 0.0


def test_order_score_penalizes_coverage():
    """Naming two cities in the right order is not the same as naming ten."""
    cities = json.dumps(["Boston", "Chicago", "Denver", "Austin"])
    assert order_score(cities, "Boston then Chicago then Denver then Austin") == 1.0
    assert order_score(cities, "Boston then Chicago") == pytest.approx(0.5)
    assert order_score(cities, "Austin then Boston") == 0.0
    assert order_score(cities, "Boston only") == 0.0
