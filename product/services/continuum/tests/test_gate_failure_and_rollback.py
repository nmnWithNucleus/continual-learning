import json
from datetime import date

from tests._helpers import consolidate
from app.publish import ModelDirectory
from app.synth import synth_records
from app.window import window_for


def _night(user, day, recipe, seed=1, policy=None):
    win = window_for(user, date(2026, 7, day), "UTC")
    return consolidate(synth_records(win, seed=seed, events=20), win,
                       recipe=recipe, policy=policy), win


def test_failed_gate_never_activates_and_prior_adapter_survives(
        var_dir, small_recipe, monkeypatch):
    good, _ = _night("u-f", 18, small_recipe)
    assert good.status == "published"
    monkeypatch.setenv("MOCK_GATE", "fail")
    bad, _ = _night("u-f", 19, small_recipe, seed=2)
    assert bad.status == "gate_failed"
    directory = ModelDirectory(var_dir)
    # Consolidation debt, never an ungated swap: night 18's adapter still serves.
    assert directory.active("u-f")["adapter_version"] == good.adapter_version
    assert directory.entries("u-f")[-1]["status"] == "gate_failed"
    state = json.loads((var_dir / "state" / "u-f.json").read_text())
    assert state["consecutive_failures"] == 1
    assert state["debt"] == ["w2026-07-19"]


def test_two_consecutive_failures_freeze_user(var_dir, small_recipe, monkeypatch):
    monkeypatch.setenv("MOCK_GATE", "fail")
    _night("u-z", 18, small_recipe, seed=1)
    _night("u-z", 19, small_recipe, seed=2)
    state = json.loads((var_dir / "state" / "u-z.json").read_text())
    assert state["frozen"]
    frozen, _ = _night("u-z", 20, small_recipe, seed=3)
    assert frozen.status == "frozen"
    assert not frozen.stages_run


def test_pass_after_single_failure_clears_strikes(var_dir, small_recipe, monkeypatch):
    monkeypatch.setenv("MOCK_GATE", "fail")
    _night("u-r", 18, small_recipe, seed=1)
    monkeypatch.delenv("MOCK_GATE")
    ok, _ = _night("u-r", 19, small_recipe, seed=2)
    assert ok.status == "published"
    state = json.loads((var_dir / "state" / "u-r.json").read_text())
    assert state["consecutive_failures"] == 0 and not state["frozen"]


def test_rollback_restores_prior_version(var_dir, small_recipe):
    first, _ = _night("u-rb", 18, small_recipe, seed=1)
    second, _ = _night("u-rb", 19, small_recipe, seed=2)
    directory = ModelDirectory(var_dir)
    assert directory.active("u-rb")["adapter_version"] == second.adapter_version
    result = directory.rollback("u-rb")
    assert result.status == "rolled_back"
    assert directory.active("u-rb")["adapter_version"] == first.adapter_version


def test_snapshot_retention_prunes_old_never_active(var_dir, small_recipe, small_policy):
    versions = []
    for day in (15, 16, 17, 18, 19):
        r, _ = _night("u-p", day, small_recipe, seed=day, policy=small_policy)
        versions.append(r.adapter_version)
    directory = ModelDirectory(var_dir)
    entries = directory.entries("u-p")
    dirs = {e["adapter_version"]: e["adapter_dir"] for e in entries if e["adapter_dir"]}
    from pathlib import Path
    surviving = [v for v in versions if Path(dirs[v]).is_dir()]
    # retention=3 keeps the last three; the active one always survives.
    assert versions[-1] in surviving
    assert len(surviving) == 3
    assert versions[0] not in surviving
