from app.backends.base import EvalScores
from app.gate import run_gate


def _scores(**over):
    base = dict(new_day_recall=0.26, traps_pass=0.50, heldout_recall=0.02,
                n_probes=150, extras={})
    base.update(over)
    return EvalScores(**base)


def test_green_scores_pass(small_recipe):
    report = run_gate(_scores(), small_recipe)
    assert report.passed and not report.reasons


def test_each_check_blocks_alone(small_recipe):
    for bad in (dict(new_day_recall=0.05), dict(traps_pass=0.10),
                dict(heldout_recall=0.20), dict(n_probes=10)):
        report = run_gate(_scores(**bad), small_recipe)
        assert not report.passed
        assert report.reasons


def test_unwired_checks_are_visibly_skipped(small_recipe):
    report = run_gate(_scores(), small_recipe)
    assert "decay_spot_check" in report.skipped
    assert "general_canary" in report.skipped
    assert "read_skill_canary" in report.skipped
