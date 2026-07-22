import json
from datetime import date

from app.cycle import run_cycle
from app.publish import ModelDirectory
from app.reservoir import Reservoir
from app.synth import synth_records
from app.window import window_for


def test_full_cycle_publishes_and_admits_reservoir(var_dir, small_recipe, win, day_records):
    result = run_cycle(day_records, win, recipe=small_recipe)
    assert result.status == "published"
    assert result.adapter_version
    directory = ModelDirectory(var_dir)
    active = directory.active("u-test")
    assert active and active["adapter_version"] == result.adapter_version
    entries = directory.entries("u-test")
    assert entries[-1]["status"] == "active"
    assert entries[-1]["contract"] == "C5"
    assert entries[-1]["training_window"] == win.window_id
    # The night's amplified corpus entered the permanent reservoir.
    assert [e.window_id for e in Reservoir(var_dir).entries("u-test")] == [win.window_id]
    # Journal captured every stage.
    journal = json.loads(
        (var_dir / "journal" / "u-test" / f"{win.window_id}.json").read_text())
    assert {"daylog", "amplify", "replay_mix", "train", "gate", "publish"} \
        <= set(journal["stages"])


def test_rerun_is_idempotent(var_dir, small_recipe, win, day_records):
    first = run_cycle(day_records, win, recipe=small_recipe)
    second = run_cycle(day_records, win, recipe=small_recipe)
    assert second.adapter_version == first.adapter_version
    assert {"daylog", "amplify", "replay_mix", "train"} <= set(second.stages_skipped)
    # Re-publish of the identical adapter is fine (idempotent alias flip).
    assert ModelDirectory(var_dir).active("u-test")["adapter_version"] \
        == first.adapter_version


def test_second_night_mixes_replay_and_continues_adapter(var_dir, small_recipe, day_records):
    win1 = window_for("u-test", date(2026, 7, 20), "America/Los_Angeles")
    win2 = window_for("u-test", date(2026, 7, 21), "America/Los_Angeles")
    r1 = run_cycle(synth_records(win1, seed=1, events=25), win1, recipe=small_recipe)
    r2 = run_cycle(synth_records(win2, seed=2, events=25), win2, recipe=small_recipe)
    assert r1.status == r2.status == "published"
    assert r2.adapter_version != r1.adapter_version
    journal2 = json.loads(
        (var_dir / "journal" / "u-test" / f"{win2.window_id}.json").read_text())
    # Night 2 actually pulled replay chars from night 1's reservoir corpus.
    assert journal2["stages"]["replay_mix"]["replay_chars"] > 0
    # Night 2 resumed from night 1's active adapter (the ONE life adapter).
    train_corpus = (var_dir / "cycles" / "u-test" / win2.window_id /
                    "train.corpus.txt").read_text()
    adapter_meta = json.loads(
        (var_dir / "adapters" / "u-test" / win2.window_id / r2.adapter_version /
         "meta.json").read_text())
    assert adapter_meta["resumed_from"] is not None
    assert len(train_corpus) > 0


def test_empty_window_skips_without_strike(var_dir, small_recipe):
    win = window_for("u-empty", date(2026, 7, 20), "UTC")
    result = run_cycle([], win, recipe=small_recipe)
    assert result.status == "skipped_no_data"
    state_path = var_dir / "state" / "u-empty.json"
    assert not state_path.exists() or \
        json.loads(state_path.read_text())["consecutive_failures"] == 0
