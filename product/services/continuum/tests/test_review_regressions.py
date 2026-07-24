"""Regression tests for the adversarial-review findings on the WS1 scaffold."""
import json
from datetime import date

import pytest

from tests._helpers import consolidate
from app.publish import ModelDirectory
from app.reservoir import Reservoir
from app.synth import synth_records
from app.window import window_for


def _night(user, day, recipe, seed=None):
    win = window_for(user, date(2026, 7, day), "UTC")
    return consolidate(synth_records(win, seed=seed if seed is not None else day,
                                     events=20), win, recipe=recipe), win


def test_rerun_of_failed_window_does_not_double_strike(var_dir, small_recipe, monkeypatch):
    monkeypatch.setenv("MOCK_GATE", "fail")
    first, _ = _night("u-a", 18, small_recipe)
    again, _ = _night("u-a", 18, small_recipe)  # identical retry (cron re-fires)
    assert first.status == again.status == "gate_failed"
    state = json.loads((var_dir / "state" / "u-a.json").read_text())
    assert state["consecutive_failures"] == 1   # one bad NIGHT, not two
    assert not state["frozen"]
    # Terminal guard replayed the outcome: no second gate_failed entry.
    entries = ModelDirectory(var_dir).entries("u-a")
    assert sum(1 for e in entries if e["status"] == "gate_failed") == 1


def test_rerun_of_old_window_never_regresses_active_alias(var_dir, small_recipe):
    old, _ = _night("u-b", 18, small_recipe)
    new, _ = _night("u-b", 19, small_recipe)
    directory = ModelDirectory(var_dir)
    n_entries = len(directory.entries("u-b"))
    replay, _ = _night("u-b", 18, small_recipe)  # operator backfill re-run
    assert replay.status == "published"
    assert "publish" in replay.stages_skipped     # terminal guard replay
    assert directory.active("u-b")["adapter_version"] == new.adapter_version
    assert len(directory.entries("u-b")) == n_entries  # no duplicate C5 rows


def test_old_window_reconsolidation_flips_nothing_but_is_recorded(var_dir, small_recipe):
    _night("u-b2", 18, small_recipe)
    new, _ = _night("u-b2", 19, small_recipe)
    # CHANGED records for the old window (new seed) -> genuinely retrains it...
    redo, win18 = _night("u-b2", 18, small_recipe, seed=99)
    assert redo.status == "published"
    directory = ModelDirectory(var_dir)
    # ...entry appended for audit, but the serving alias stays on the newest window,
    assert directory.active("u-b2")["adapter_version"] == new.adapter_version
    # ...and the NEXT night's lineage resumes from the newest window, not w18.
    prior = directory.active_before("u-b2", "w2026-07-20")
    assert prior["training_window"] == "w2026-07-19"


def test_rollback_is_reentrant_down_to_base(var_dir, small_recipe):
    first, _ = _night("u-c", 18, small_recipe)
    second, _ = _night("u-c", 19, small_recipe)
    directory = ModelDirectory(var_dir)
    directory.rollback("u-c")
    assert directory.active("u-c")["adapter_version"] == first.adapter_version
    directory.rollback("u-c")
    assert directory.active("u-c") is None      # base model only
    with pytest.raises(RuntimeError):
        directory.rollback("u-c")               # nothing left — loud, not silent


def test_reservoir_content_change_invalidates_replay_mix(var_dir, small_recipe):
    _night("u-d", 18, small_recipe)
    second, win19 = _night("u-d", 19, small_recipe)
    assert second.status == "published"
    # A past day's reservoir corpus is legitimately re-written...
    long_para = "x" * 200
    Reservoir(var_dir).admit("u-d", "w2026-07-18", small_recipe.recipe_id,
                             "\n\n".join(f"{long_para}{i}" for i in range(10)))
    # ...so re-running night 19 must rebuild its replay mix, not trust the cache.
    redo = consolidate(synth_records(win19, seed=19, events=20), win19,
                       recipe=small_recipe)
    assert "replay_mix" in redo.stages_run
    assert "replay_mix" not in redo.stages_skipped


def test_unsafe_user_id_is_rejected_everywhere(var_dir, small_recipe):
    win = window_for("../evil", date(2026, 7, 20), "UTC")
    with pytest.raises(ValueError, match="user_id"):
        consolidate([], win, recipe=small_recipe)
    with pytest.raises(ValueError):
        ModelDirectory(var_dir).entries("a/b")
    with pytest.raises(ValueError):
        Reservoir(var_dir).entries("..")


def test_debt_clears_when_failed_window_later_consolidates(var_dir, small_recipe, monkeypatch):
    monkeypatch.setenv("MOCK_GATE", "fail")
    _night("u-e", 18, small_recipe)
    monkeypatch.delenv("MOCK_GATE")
    # New records for the same window (the failed-day retry path) -> passes.
    redo, _ = _night("u-e", 18, small_recipe, seed=42)
    assert redo.status == "published"
    state = json.loads((var_dir / "state" / "u-e.json").read_text())
    assert state["debt"] == []


def test_boundary_straddling_subspan_lands_in_window():
    from app.daylog import build_daylog
    win = window_for("u-f", date(2026, 7, 20), "UTC")
    from datetime import timedelta
    before = win.start_utc - timedelta(seconds=5)   # parent chunk starts pre-boundary
    inside = win.start_utc + timedelta(seconds=2)
    rec = {"record_id": "r", "user_id": "u-f", "t_start": before.isoformat(),
           "t_end": inside.isoformat(),
           "content": {"kind": "transcript", "text": "early late", "segments": [
               {"t_start": before.isoformat(), "t_end": before.isoformat(),
                "text": "early", "speaker": None},
               {"t_start": inside.isoformat(), "t_end": inside.isoformat(),
                "text": "late", "speaker": None}]}}
    daylog = build_daylog([rec], win)
    texts = [a["text"] for s in daylog.segments for a in s.asr]
    assert texts == ["late"]  # in-window speech kept, pre-boundary speech excluded


def test_block_anchor_uses_local_clock(var_dir, small_recipe):
    from app.daylog import build_daylog
    win = window_for("u-g", date(2026, 7, 20), "America/Los_Angeles")
    daylog = build_daylog(synth_records(win, seed=7, events=10), win)
    header = daylog.blocks[0].text.splitlines()[0]
    assert "local time" in header and "UTC" not in header
    # Synthetic activity starts ~4h into the window (08:00 local, 15:00 UTC):
    # a local rendering shows 08:xx, the old UTC bug showed 15:xx.
    assert header.split("around ")[1][:2] == "08"


def test_blocks_break_on_camera_off_gaps(var_dir, small_recipe):
    from app.daylog import build_daylog
    from datetime import timedelta
    win = window_for("u-h", date(2026, 7, 20), "UTC")
    recs = []
    for i, offset in enumerate((0, 10, 7200, 7210)):  # two pairs, 2h apart
        t0 = win.start_utc + timedelta(seconds=offset)
        recs.append({"record_id": f"r{i}", "user_id": "u-h",
                     "t_start": t0.isoformat(),
                     "t_end": (t0 + timedelta(seconds=10)).isoformat(),
                     "content": {"kind": "caption", "text": f"scene {i}"}})
    daylog = build_daylog(recs, win)
    assert len(daylog.blocks) == 2  # the 2h gap split them despite count < 12


def test_gate_report_persisted_with_skipped_checks(var_dir, small_recipe, win, day_records):
    consolidate(day_records, win, recipe=small_recipe)
    journal = json.loads(
        (var_dir / "journal" / "u-test" / f"{win.window_id}.json").read_text())
    pub = journal["stages"]["publish"]
    assert "read_skill_canary" in pub["skipped_checks"]
    assert pub["checks"]["heldout"] is True
    entry = ModelDirectory(var_dir).entries("u-test")[-1]
    assert "skipped_checks" in entry["eval_report"]


def test_torn_entries_line_is_skipped_not_fatal(var_dir, small_recipe):
    first, _ = _night("u-i", 18, small_recipe)
    entries_path = var_dir / "model_directory" / "u-i" / "entries.jsonl"
    with entries_path.open("a") as f:
        f.write('{"torn": tru')  # crash mid-append, no newline
    directory = ModelDirectory(var_dir)
    assert len(directory.entries("u-i")) == 1  # torn tail skipped
    second, _ = _night("u-i", 19, small_recipe)  # next append self-repairs
    assert second.status == "published"
    assert len(directory.entries("u-i")) == 2
