"""The three storage client seams — interfaces with local backends (2c).

These prove the seams behave, and — critically for the migration — that routing
the day-log through the client changes NO bytes: `LocalDayLogClient` produces
exactly what the pre-2c inline `build_daylog` + renderer produced.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.clients import (LocalDayLogClient, LocalRecipeRegistry, LocalReservoirClient,
                         day_log_client, recipe_registry, reservoir_client)
from app.clients.daylog_client import daylog_fingerprint
from app.clients.registry import RecipeNotFound
from app.config import get_settings
from app.daylog import build_daylog
from app.renderer import blocks_text, render_daylog_files
from app.synth import synth_records
from app.window import window_for


# ------------------------------------------------------------------ day-log seam

def _win():
    return window_for("u-c", date(2026, 7, 20), "America/Los_Angeles")


def test_local_daylog_client_is_byte_identical_to_inline_build():
    """The migration's core guarantee: fetching the day-log through the client
    yields exactly the segments/blocks the inline path built."""
    win = _win()
    records = synth_records(win, seed=7, events=40)
    inline = build_daylog(records, win, segment_seconds=10, block_segments=12)
    fetched = LocalDayLogClient.from_records(records).fetch_daylog(win)
    assert [s.__dict__ for s in fetched.segments] == [s.__dict__ for s in inline.segments]
    assert [b.__dict__ for b in fetched.blocks] == [b.__dict__ for b in inline.blocks]
    assert blocks_text(fetched.blocks) == blocks_text(inline.blocks)


def test_rendered_daylog_files_are_byte_identical(tmp_path):
    win = _win()
    records = synth_records(win, seed=7, events=40)
    a = render_daylog_files(build_daylog(records, win), tmp_path / "inline")
    b = render_daylog_files(
        LocalDayLogClient.from_records(records).fetch_daylog(win), tmp_path / "client")
    for key in ("segments", "blocks", "day_txt"):
        assert open(a[key]).read() == open(b[key]).read(), key


def test_fingerprint_is_stable_and_content_sensitive():
    win = _win()
    dl = LocalDayLogClient.from_records(synth_records(win, seed=7, events=30)).fetch_daylog(win)
    dl2 = LocalDayLogClient.from_records(synth_records(win, seed=7, events=30)).fetch_daylog(win)
    other = LocalDayLogClient.from_records(synth_records(win, seed=8, events=30)).fetch_daylog(win)
    assert daylog_fingerprint(dl) == daylog_fingerprint(dl2)
    assert daylog_fingerprint(dl) != daylog_fingerprint(other)


def test_daylog_client_carries_recipe_segmentation(small_recipe):
    client = day_log_client(get_settings(), small_recipe,
                            record_provider=lambda w: [])
    assert client.segment_seconds == small_recipe.segment_seconds
    assert client.block_segments == small_recipe.block_segments


# ------------------------------------------------------------------ registry seam

def test_registry_resolves_shipped_recipe_and_policy():
    registry = recipe_registry(get_settings())
    assert registry.fetch_recipe("consolidation-v1.0").recipe_id == "consolidation-v1.0"
    assert registry.fetch_policy("gate-policy-v1.1").policy_id == "gate-policy-v1.1"


def test_registry_unknown_id_raises(tmp_path):
    registry = LocalRecipeRegistry(recipes_dir=tmp_path, policies_dir=tmp_path)
    with pytest.raises(RecipeNotFound):
        registry.fetch_recipe("nope")
    with pytest.raises(RecipeNotFound):
        registry.fetch_policy("nope")


def test_registry_id_cannot_escape_the_dir(tmp_path):
    registry = LocalRecipeRegistry(recipes_dir=tmp_path, policies_dir=tmp_path)
    with pytest.raises(RecipeNotFound):
        registry.fetch_recipe("../../etc/passwd")


def test_registry_rejects_id_content_mismatch(tmp_path):
    """A registry that returns a differently-identified artifact would let a night
    mis-record what it trained under. The file's own id is authoritative."""
    import json
    from pathlib import Path
    real = json.loads((Path(get_settings().recipes_dir) / "consolidation-v1.0.json").read_text())
    (tmp_path / "mislabeled.json").write_text(json.dumps(real))   # id inside != filename
    registry = LocalRecipeRegistry(recipes_dir=tmp_path, policies_dir=tmp_path)
    with pytest.raises(RecipeNotFound, match="disagree"):
        registry.fetch_recipe("mislabeled")


# ----------------------------------------------------------------- reservoir seam

def test_reservoir_client_admit_and_amp_replay_match_the_reservoir(tmp_path):
    client = LocalReservoirClient(tmp_path)
    para = "y" * 300
    client.admit("u-r", "w2026-07-18", "test-recipe-v0",
                 "\n\n".join(f"{para}{i}" for i in range(20)))
    replay = client.sample_replay("u-r", target_chars=10_000, frac=0.3, seed=1,
                                  before_window="w2026-07-20", source="amp")
    assert replay and all(len(p) > 100 for p in replay.split("\n\n"))


def test_reservoir_client_rawlog_reads_prior_daylogs(tmp_path):
    """rawlog replay re-reads prior day-logs via the day-log client — the locked
    architecture — never the amplified store."""
    win_prior = window_for("u-r", date(2026, 7, 18), "UTC")
    prior_records = synth_records(win_prior, seed=3, events=40)
    daylog = LocalDayLogClient.from_records(prior_records)
    client = LocalReservoirClient(tmp_path, daylog_client=daylog)
    # The reservoir ledger records that the prior window ran (amplified corpus is
    # audit/provenance); rawlog replay reads the day-log, not this corpus.
    client.admit("u-r", "w2026-07-18", "test-recipe-v0", "amplified\n\nprovenance only")
    replay = client.sample_replay(
        "u-r", target_chars=100_000, frac=0.5, seed=1, before_window="w2026-07-20",
        source="rawlog", prior_windows=[win_prior])
    day_text = blocks_text(daylog.fetch_daylog(win_prior).blocks)
    assert replay
    # Every replayed paragraph came from the raw day-log, not the amplified corpus.
    assert "provenance only" not in replay
    for para in replay.split("\n\n"):
        assert para in day_text


def test_reservoir_client_rawlog_needs_a_daylog_client(tmp_path):
    client = LocalReservoirClient(tmp_path)   # no day-log client
    with pytest.raises(ValueError, match="day-log client"):
        client.sample_replay("u-r", target_chars=100, frac=0.3, seed=1,
                             source="rawlog", prior_windows=[_win()])


def test_reservoir_factory_wires_the_daylog_client():
    settings = get_settings()
    client = reservoir_client(settings, daylog_client=LocalDayLogClient.from_records([]))
    assert isinstance(client, LocalReservoirClient)


def test_run_cycle_default_wires_all_three_clients(var_dir, monkeypatch):
    """The default path (no injected clients) must resolve the registry, day-log
    client, and reservoir client from settings — a guard against the factories
    silently going unimported."""
    from app.cycle import run_cycle
    # Default day-log client fetches via the C10 read; stub it to an empty window.
    monkeypatch.setattr(
        "app.context_reader.fetch_window_records", lambda *a, **k: [])
    win = window_for("u-default", date(2026, 7, 20), "UTC")
    result = run_cycle(win)   # no daylog_client, no registry, no recipe, no policy
    assert result.status == "skipped_no_data"
