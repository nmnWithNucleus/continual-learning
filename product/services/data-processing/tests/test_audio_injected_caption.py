"""WP-C4 — InjectedCaptionStage: built + tested but NOT registered.

Dogfood-only (Phase-3 replay descriptions): enabling it is a code-level graph
composition — the ratified §2 dialect excludes it, C2 v1 has no
``injected_caption`` sub-schema, and the v0 env knobs
(INJECT_CAPTION_BACKEND/INJECT_CAPTION_INDEX) are dead: the index path is a
CONSTRUCTOR argument. Tests drive explicit resolve() stage sets.

Ported v0 semantics under test: the wall-clock join (rows whose t_start falls
inside the chunk's half-open C1 span), the index's OWN time strings carried
VERBATIM (never re-rendered — a re-rendered stamp silently moves a record out
of its window), blank rows skipped, unsorted indexes handled, and a missing or
malformed index failing LOUDLY (a silent zero-caption day would read exactly
like a day nobody described).
"""
from __future__ import annotations

import json

import pytest

from app.stagegraph.executor import resolve
from app.stagegraph.stage import stages_for
from app.stages.audio.injected_caption import InjectedCaptionStage

from .test_audio_stages import execute

C1 = {
    "contract": "C1", "version": "0", "user_id": "nmn-replay",
    "device_id": "replay-dev", "stream_id": "stream-r", "sequence": 0,
    "chunk_id": "chunk-replay-1", "modality": "audio", "codec": "audio/webm",
    "t_start": "2026-08-06T12:00:00Z", "t_end": "2026-08-06T12:01:00Z",
    "blob_ref": "raw/rr", "blob_sha256": "0" * 64, "blob_bytes": 4,
}
SPAN = 60.0


def _write_index(tmp_path, rows) -> str:
    path = tmp_path / "captions.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return str(path)


def _row(t_start, t_end, text, window="w0"):
    return {"t_start": t_start, "t_end": t_end, "text": text, "window": window}


def _run(index_path):
    return execute([InjectedCaptionStage(index_path)], {}, c1=C1, span=SPAN)


def test_injected_caption_is_not_registered():
    assert "injected_caption" not in [s.name for s in stages_for("audio")]


def test_rows_inside_the_span_become_splits_verbatim(tmp_path):
    # Two spellings the index might carry — both must ride through CHAR-FOR-CHAR
    # (the v0 rule: the index strings ARE the spine recording stamped from).
    path = _write_index(tmp_path, [
        _row("2026-08-06T12:00:05Z", "2026-08-06T12:00:10Z", "making coffee"),
        _row("2026-08-06T12:00:59+00:00", "2026-08-06T12:01:04+00:00",
             "sitting down"),
        _row("2026-08-06T12:02:00Z", "2026-08-06T12:02:05Z", "out of span"),
    ])
    _, result = _run(path)
    assert result.slots["injected_caption"] == {
        "version": "injected_caption.v1-index.v1",
        "splits": [
            {"t_start": "2026-08-06T12:00:05Z",
             "t_end": "2026-08-06T12:00:10Z", "value": "making coffee"},
            {"t_start": "2026-08-06T12:00:59+00:00",
             "t_end": "2026-08-06T12:01:04+00:00", "value": "sitting down"},
        ],
    }


def test_membership_is_half_open_on_t_start(tmp_path):
    path = _write_index(tmp_path, [
        _row("2026-08-06T12:00:00Z", "2026-08-06T12:00:05Z", "at start"),
        _row("2026-08-06T12:01:00Z", "2026-08-06T12:01:05Z", "at end"),
    ])
    _, result = _run(path)
    values = [s["value"] for s in result.slots["injected_caption"]["splits"]]
    assert values == ["at start"]  # t_start == chunk t_end is OUT (half-open)


def test_no_described_window_is_an_honest_empty_claim(tmp_path):
    path = _write_index(tmp_path, [
        _row("2026-08-06T13:00:00Z", "2026-08-06T13:00:05Z", "elsewhere"),
    ])
    _, result = _run(path)
    assert result.slots["injected_caption"] == {
        "version": "injected_caption.v1-index.v1", "splits": []}


def test_blank_text_rows_are_skipped(tmp_path):
    path = _write_index(tmp_path, [
        _row("2026-08-06T12:00:05Z", "2026-08-06T12:00:10Z", "  "),
        _row("2026-08-06T12:00:20Z", "2026-08-06T12:00:25Z", "typing"),
    ])
    _, result = _run(path)
    values = [s["value"] for s in result.slots["injected_caption"]["splits"]]
    assert values == ["typing"]


def test_unsorted_index_is_still_joined_correctly(tmp_path):
    path = _write_index(tmp_path, [
        _row("2026-08-06T12:00:40Z", "2026-08-06T12:00:45Z", "second"),
        _row("2026-08-06T12:00:10Z", "2026-08-06T12:00:15Z", "first"),
    ])
    _, result = _run(path)
    values = [s["value"] for s in result.slots["injected_caption"]["splits"]]
    assert values == ["first", "second"]


def test_missing_index_file_is_a_hole_never_a_silent_empty_day(tmp_path):
    _, result = _run(str(tmp_path / "nope.jsonl"))
    assert result.statuses == {"injected_caption": "failed"}
    assert result.slots == {}


def test_empty_index_path_fails_at_construction():
    with pytest.raises(ValueError, match="index_path"):
        InjectedCaptionStage("")


def test_redeployed_index_is_picked_up(tmp_path):
    path = _write_index(tmp_path, [
        _row("2026-08-06T12:00:05Z", "2026-08-06T12:00:10Z", "old text"),
    ])
    stage = InjectedCaptionStage(path)
    _, result = execute([stage], {}, c1=C1, span=SPAN)
    assert [s["value"] for s in
            result.slots["injected_caption"]["splits"]] == ["old text"]
    _write_index(tmp_path, [
        _row("2026-08-06T12:00:05Z", "2026-08-06T12:00:10Z", "new text after redeploy"),
    ])
    _, result = execute([stage], {}, c1=C1, span=SPAN)
    assert [s["value"] for s in
            result.slots["injected_caption"]["splits"]] == ["new text after redeploy"]


def test_resolves_in_an_explicit_stage_set(tmp_path):
    path = _write_index(tmp_path, [])
    resolved = resolve("audio", [InjectedCaptionStage(path)])
    assert resolved.pipeline_version == "injected_caption.v1-index.v1"
