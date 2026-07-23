"""Judge parity.

Two halves, tested apart:

  AGGREGATION (offline, exact) — every reference run stores both its per-item
  verdicts and its published summary. Feeding the stored verdicts through our
  summarizer must reproduce the published summary key for key, including the
  micro average. This is what makes every downstream readout comparable, and it
  needs no network.

  ADJUDICATION (live, distributional) — the judge is a sampled model, so it can
  only be checked in distribution. Opt in with MORPHEUS_LIVE_JUDGE=1: a fixed
  slice of a reference prediction set is re-judged and compared to the stored
  verdicts on both agreement and aggregate recall.
"""
from __future__ import annotations

import os

import pytest

from app.morpheus.judge import JudgeConfig, judge, judge_items, parse_verdict, summarize

from . import goldens
from .conftest import needs_goldens

ALL_RUNS = (*goldens.SEED_ENSEMBLE, goldens.REPRODUCTION)

# Judge agreement on a re-run. Gemini at temperature 0 is near-deterministic but
# not contractually so; below this the judge prompt has changed meaning.
MIN_AGREEMENT = 0.90
LIVE_SAMPLE = 200


@needs_goldens
@pytest.mark.parametrize("run", ALL_RUNS)
def test_summary_reproduces_the_published_judge_json(run):
    rows = goldens.scored_rows(run)
    published = goldens.judged(run)
    ours = summarize(rows, [r["judge"] for r in rows], label=published["label"],
                     model=published["model"])
    assert ours == published, "judged summary drifted from the reference run's own summary"


@needs_goldens
@pytest.mark.parametrize("run", ALL_RUNS)
def test_readouts_derive_from_the_published_summary(run):
    """The numbers the phase is graded on, recomputed from the goldens."""
    r = goldens.golden_readout(run)
    published = goldens.judged(run)
    assert r.micro == published["judge_exact_micro"]
    assert r.heldout == published["final_heldout"]["judge_exact"]
    assert r.seen_mean == pytest.approx(
        sum(published[f"s5_d{d}"]["judge_exact"] for d in goldens.DAYS) / len(goldens.DAYS))
    # separation is reported at the judge's own precision (4 dp)
    assert r.separation == pytest.approx(r.seen_mean - r.heldout, abs=1e-4)


@needs_goldens
def test_reference_band_still_matches_the_commissioned_spec():
    """Tripwire: if the golden directory changes, every in-band verdict below it
    changes meaning. Catch that here rather than in a green E2E run."""
    goldens.assert_bands_match_spec()
    band = goldens.reference_band()
    assert band.seen_mean[0] < band.seen_mean[1], "ensemble collapsed to one seed"


@needs_goldens
def test_our_phase1_reproduction_is_in_band():
    """The Phase-1 reproduction was the GO decision for this port. It has to sit
    inside the reference spread, or the port has no target."""
    checks = goldens.reference_band().check(goldens.golden_readout(goldens.REPRODUCTION))
    assert all(checks.values()), checks


def test_verdict_parsing_is_strict():
    assert parse_verdict('{"correct": 1}') == 1
    assert parse_verdict('sure! {"correct":0} hope that helps') == 0
    assert parse_verdict('{"correct": 2}') is None
    assert parse_verdict("the answer is correct") is None
    assert parse_verdict(None) is None


def test_unjudged_items_are_excluded_not_zeroed():
    """An API outage must not read as forgetting. Scoring a failed call as 0
    would drag recall down and could fail the gate on an infrastructure blip."""
    items = [{"suite": "s0_d5"}] * 4
    summary = summarize(items, [1, 1, None, 0])
    assert summary["n_unjudged"] == 1
    assert summary["s0_d5"] == {"n": 3, "judge_exact": round(2 / 3, 4)}
    assert summary["judge_exact_micro"] == round(2 / 3, 4)


def test_judge_threads_without_reordering():
    """Verdicts must stay aligned to their items across the worker pool."""
    items = [{"suite": f"s{i % 2}", "pred": str(i)} for i in range(50)]
    summary = judge(items, JudgeConfig(workers=8),
                    judge_one=lambda item: int(item["pred"]) % 2)
    assert summary["s0"]["judge_exact"] == 0.0    # even index -> even pred -> 0
    assert summary["s1"]["judge_exact"] == 1.0


@needs_goldens
@pytest.mark.skipif(not os.getenv("MORPHEUS_LIVE_JUDGE"),
                    reason="set MORPHEUS_LIVE_JUDGE=1 to re-judge against Vertex")
def test_live_judge_agrees_with_the_reference_verdicts():
    """Re-judge a fixed slice with our prompt and our credentials.

    Checked two ways because they fail differently: ITEM agreement catches a
    prompt whose meaning shifted, AGGREGATE delta catches a systematic
    leniency/strictness shift that item noise would average out."""
    from app.config import get_settings
    settings = get_settings().morpheus

    predictions = goldens.prediction_rows(goldens.REPRODUCTION)
    stored = goldens.scored_rows(goldens.REPRODUCTION)
    paired = [(s, p) for s, p in zip(stored, predictions) if s["judge"] is not None][:LIVE_SAMPLE]
    items = [{"suite": s["suite"], "q": p["q"], "gold": s["gold"], "pred": s["pred"]}
             for s, p in paired]

    config = JudgeConfig(model=settings.judge_model, project=settings.vertex_project,
                         location=settings.vertex_location, workers=settings.judge_workers)
    verdicts = judge_items(items, config)
    reference = [s["judge"] for s, _ in paired]
    assert all(v is not None for v in verdicts), "judge returned unjudged items"

    agreement = sum(int(v == r) for v, r in zip(verdicts, reference)) / len(reference)
    assert agreement >= MIN_AGREEMENT, (
        f"judge agrees with the reference verdicts on only {agreement:.1%} of "
        f"{len(reference)} items — the grading criterion has shifted")
    assert abs(sum(verdicts) / len(verdicts)
               - sum(reference) / len(reference)) <= 0.05, "systematic judge drift"
