import pytest

from app.backends.base import EvalScores
from app.gate import run_gate
from app.policy import heldout_p_value, load_policy


def _scores(**over):
    """A reference-quality night: recall in the measured band, traps mid-range,
    heldout indistinguishable from the base control."""
    base = dict(new_day_recall=0.26, traps_pass=0.35,
                heldout_hits=2, heldout_n=222, base_heldout_hits=0, base_heldout_n=222,
                n_probes=310, extras={})
    base.update(over)
    return EvalScores(**base)


def test_reference_quality_night_passes(small_policy):
    report = run_gate(_scores(), small_policy)
    assert report.passed and not report.reasons
    assert report.policy_id == "test-policy-v0"


def test_each_check_blocks_alone(small_policy):
    for bad in (dict(new_day_recall=0.05),        # did not learn the day
                dict(traps_pass=0.05),            # calibration collapse
                dict(heldout_hits=20),            # contamination
                dict(n_probes=10)):               # not enough eval to judge
        report = run_gate(_scores(**bad), small_policy)
        assert not report.passed, bad
        assert report.reasons


def test_traps_floor_admits_the_reference_distribution_but_still_bites(small_policy):
    """Calibrated against the 24 measured reference nights (0.143–0.500).

    The old 0.40 floor blocked 17 of them. The ratified 0.15 admits 23 and blocks
    exactly one — the reference's own minimum, 4/28. That is not a defect: a floor
    that blocks nothing in the observed range is not a gate. At n=28, 0.15 is the
    smallest floor with any teeth at all, since 4/28 = 0.143 is the next value
    below it."""
    for typical in (0.250, 0.286, 0.393, 0.500):
        assert run_gate(_scores(traps_pass=typical), small_policy).checks["traps"], typical
    assert not run_gate(_scores(traps_pass=0.143), small_policy).checks["traps"]
    # And it still catches the failure mode it exists for: our seed 0's night 0.
    assert not run_gate(_scores(traps_pass=0.036), small_policy).checks["traps"]


def test_heldout_is_differential_not_a_fixed_ceiling(small_policy):
    """Same rate, different evidence. 2/60 and 12/222 differ in what they license
    against a 0/n base control, though a fixed ceiling would treat them alike."""
    assert run_gate(_scores(heldout_hits=2, heldout_n=60,
                            base_heldout_hits=0, base_heldout_n=60),
                    small_policy).checks["heldout"]
    assert not run_gate(_scores(heldout_hits=12, heldout_n=222,
                                base_heldout_hits=0, base_heldout_n=222),
                        small_policy).checks["heldout"]


def test_heldout_tolerates_a_base_that_already_knew(small_policy):
    """If the BASE model scores on heldout days, the adapter matching it is not
    contamination — it is a base-model property. A fixed ceiling cannot see this."""
    report = run_gate(_scores(heldout_hits=10, heldout_n=222,
                              base_heldout_hits=9, base_heldout_n=222), small_policy)
    assert report.checks["heldout"]


def test_backstop_catches_a_contaminated_base(small_policy):
    """The differential test goes blind when the base is dirty too — the absolute
    backstop is what remains."""
    report = run_gate(_scores(heldout_hits=80, heldout_n=222,
                              base_heldout_hits=78, base_heldout_n=222), small_policy)
    assert not report.checks["heldout"]
    assert any("backstop" in r for r in report.reasons)


def test_underpowered_heldout_suite_is_flagged_not_hidden(small_policy):
    report = run_gate(_scores(heldout_hits=0, heldout_n=30, base_heldout_n=30), small_policy)
    assert report.passed
    assert any("underpowered" in r for r in report.reasons)


def test_unwired_checks_are_visibly_skipped(small_policy):
    report = run_gate(_scores(), small_policy)
    for check in ("decay_spot_check", "general_canary", "read_skill_canary"):
        assert check in report.skipped


def test_p_value_is_a_proper_one_sided_test():
    assert heldout_p_value(0, 60, 0, 60) == 1.0
    assert heldout_p_value(5, 60, 0, 60) == pytest.approx(0.0287, abs=1e-3)
    assert heldout_p_value(2, 60, 0, 60) > 0.05        # the reference's worst run
    assert heldout_p_value(2, 222, 0, 222) > 0.05


def test_shipped_policy_matches_what_was_ratified():
    """The ratified numbers, asserted against the artifact that actually loads."""
    from pathlib import Path
    policy = load_policy(Path(__file__).resolve().parents[1] /
                         "policies" / "gate-policy-v1.1.json")
    assert policy.policy_id == "gate-policy-v1.1"
    assert policy.traps_pass_min == 0.15
    assert policy.min_probes == 148        # the harness supplies exactly 60+60+28
    assert policy.heldout_probes == 222
    assert policy.heldout_alpha == 0.01
    assert policy.heldout_backstop == 0.15


def test_gate_policy_is_not_in_the_training_recipe():
    """The structural rule: cycle.py hashes recipe_id into the amplify and train
    stage keys, so a gate threshold living in the recipe would make re-deciding
    what is shippable re-run a night of GPU work."""
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    recipe = json.loads((root / "recipes" / "consolidation-v1.0.json").read_text())
    assert "gate" not in recipe and "publish" not in recipe
    cycle_src = (root / "app" / "cycle.py").read_text()
    for stage_key in ("amp_key = _h(", "train_key = _h("):
        fragment = cycle_src[cycle_src.index(stage_key):]
        fragment = fragment[:fragment.index(")\n")]
        assert "policy" not in fragment, f"gate policy leaked into a stage key: {stage_key}"
