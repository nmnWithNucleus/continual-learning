"""Injected-caption sidecar — the INJECT_CAPTION_BACKEND=off|index switch.

Hermetic: mock ASR, a two-row index on disk, no GPU. What it pins:

  * off (default) is a no-op — the audio pipeline is byte-identical to a live capture's;
  * on, a chunk gains ONE caption record per index window that falls in its C1 span,
    each schema-valid, kind='caption', with its OWN sub-span and a distinct record_id,
    SHARING (not forking) pipeline_version — the acoustic-sidecar contract;
  * the join is half-open on wall clock, which is what makes a replayed chunk's captions
    land in exactly one day-log segment each;
  * timestamps are copied VERBATIM. Storage compares window bounds as raw strings, so a
    record re-rendered as '+00:00' instead of 'Z' would silently fall out of its window;
  * a chunk with no descriptions yields no captions and still ingests (18 of the 629
    replayed chunks are partially described and one has none at all);
  * a broken index fails LOUDLY rather than looking like a quiet day.
"""
from __future__ import annotations

import json

import pytest

from app import schemas
from app.asr import mock as mock_asr
from app.pipeline import compute_record_id
from app.stages.audio import injected_caption
from tests.conftest import make_c1

_PV = mock_asr.PIPELINE_VERSION            # "asr-mock-v0"

ROWS = [
    {"t_start": "2026-07-09T12:00:00Z", "t_end": "2026-07-09T12:01:00Z",
     "window": "w0_60", "day": 5, "split": "train", "text": "Headline: minute one."},
    {"t_start": "2026-07-09T12:01:00Z", "t_end": "2026-07-09T12:02:00Z",
     "window": "w60_120", "day": 5, "split": "train", "text": "Headline: minute two."},
    {"t_start": "2026-07-09T12:05:00Z", "t_end": "2026-07-09T12:06:00Z",
     "window": "w300_360", "day": 5, "split": "train", "text": "Headline: a later minute."},
]


def write_index(tmp_path, rows=ROWS) -> str:
    path = tmp_path / "captions.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return str(path)


def enable(monkeypatch, tmp_path, rows=ROWS) -> str:
    injected_caption._CACHE.clear()
    path = write_index(tmp_path, rows)
    monkeypatch.setenv("INJECT_CAPTION_BACKEND", "index")
    monkeypatch.setenv("INJECT_CAPTION_INDEX", path)
    return path


# ---- the switch --------------------------------------------------------------

def test_off_by_default(client, monkeypatch, tmp_path):
    monkeypatch.delenv("INJECT_CAPTION_BACKEND", raising=False)
    c1 = make_c1(client.fake_storage, chunk_id="cap-off",
                 t_start="2026-07-09T12:00:00Z", t_end="2026-07-09T12:02:00Z")
    assert len(client.post("/ingest", json=c1).json()["record_ids"]) == 1


def test_backend_set_but_no_index_fails_loudly(client, monkeypatch):
    """Selecting the backend and forgetting the path must not look like a quiet day."""
    injected_caption._CACHE.clear()
    monkeypatch.setenv("INJECT_CAPTION_BACKEND", "index")
    monkeypatch.delenv("INJECT_CAPTION_INDEX", raising=False)
    c1 = make_c1(client.fake_storage, chunk_id="cap-noindex")
    with pytest.raises(ValueError, match="INJECT_CAPTION_INDEX"):
        client.post("/ingest", json=c1)


# ---- the join ----------------------------------------------------------------

def test_chunk_gains_one_caption_per_window_in_its_span(client, monkeypatch, tmp_path):
    enable(monkeypatch, tmp_path)
    c1 = make_c1(client.fake_storage, chunk_id="cap-on",
                 t_start="2026-07-09T12:00:00Z", t_end="2026-07-09T12:02:00Z")
    resp = client.post("/ingest", json=c1)
    assert resp.status_code == 200

    # Primary first, then the sidecar's units in window order.
    assert resp.json()["record_ids"] == [
        compute_record_id("cap-on", _PV, ""),
        compute_record_id("cap-on", _PV, "injcap-w0_60"),
        compute_record_id("cap-on", _PV, "injcap-w60_120"),
    ]
    primary, first, second = client.fake_storage.record_posts
    assert primary["content"]["kind"] == "transcript"
    for record, row in ((first, ROWS[0]), (second, ROWS[1])):
        assert schemas.validate_c2(record) == []
        assert record["content"]["kind"] == "caption"
        assert record["content"]["text"] == row["text"]
        assert record["content"].get("segments") is None
        assert record["pipeline_version"] == _PV          # shares pv; NOT forked
        assert record["enrichments"] == {
            "speakers": [], "faces": [], "places": [], "objects": [],
        }


def test_sub_spans_are_the_index_strings_verbatim(client, monkeypatch, tmp_path):
    """Storage's range read compares t_start as a STRING: '…+00:00' >= '…Z' is False,
    so a re-rendered timestamp would silently drop the record out of its window."""
    enable(monkeypatch, tmp_path)
    c1 = make_c1(client.fake_storage, chunk_id="cap-spans",
                 t_start="2026-07-09T12:00:00Z", t_end="2026-07-09T12:02:00Z")
    client.post("/ingest", json=c1)
    _, first, second = client.fake_storage.record_posts
    assert (first["t_start"], first["t_end"]) == (ROWS[0]["t_start"], ROWS[0]["t_end"])
    assert (second["t_start"], second["t_end"]) == (ROWS[1]["t_start"], ROWS[1]["t_end"])
    # ... and NOT the chunk's own span, which is what a sub-span-less unit would carry.
    assert first["t_end"] != c1["t_end"]


def test_membership_is_half_open_on_t_start(client, monkeypatch, tmp_path):
    """A window starting exactly at the chunk end belongs to the NEXT chunk — the same
    attribution rule the day-log applies, so no caption is emitted twice."""
    enable(monkeypatch, tmp_path)
    c1 = make_c1(client.fake_storage, chunk_id="cap-halfopen",
                 t_start="2026-07-09T12:00:00Z", t_end="2026-07-09T12:01:00Z")
    ids = client.post("/ingest", json=c1).json()["record_ids"]
    assert ids == [compute_record_id("cap-halfopen", _PV, ""),
                   compute_record_id("cap-halfopen", _PV, "injcap-w0_60")]


def test_chunk_with_no_described_window_still_ingests(client, monkeypatch, tmp_path):
    """One replayed chunk has zero descriptions and 18 more are partial: a miss must be
    an empty result, not a failed chunk."""
    enable(monkeypatch, tmp_path)
    c1 = make_c1(client.fake_storage, chunk_id="cap-gap",
                 t_start="2026-07-09T13:00:00Z", t_end="2026-07-09T13:20:00Z")
    assert client.post("/ingest", json=c1).json()["record_ids"] == [
        compute_record_id("cap-gap", _PV, "")]


def test_blank_text_rows_are_skipped(client, monkeypatch, tmp_path):
    rows = [dict(ROWS[0], text="   "), ROWS[1]]
    enable(monkeypatch, tmp_path, rows)
    c1 = make_c1(client.fake_storage, chunk_id="cap-blank",
                 t_start="2026-07-09T12:00:00Z", t_end="2026-07-09T12:02:00Z")
    assert client.post("/ingest", json=c1).json()["record_ids"] == [
        compute_record_id("cap-blank", _PV, ""),
        compute_record_id("cap-blank", _PV, "injcap-w60_120")]


def test_unsorted_index_is_still_joined_correctly(client, monkeypatch, tmp_path):
    enable(monkeypatch, tmp_path, [ROWS[2], ROWS[1], ROWS[0]])
    c1 = make_c1(client.fake_storage, chunk_id="cap-unsorted",
                 t_start="2026-07-09T12:00:00Z", t_end="2026-07-09T12:02:00Z")
    assert client.post("/ingest", json=c1).json()["record_ids"] == [
        compute_record_id("cap-unsorted", _PV, ""),
        compute_record_id("cap-unsorted", _PV, "injcap-w0_60"),
        compute_record_id("cap-unsorted", _PV, "injcap-w60_120")]


# ---- failure is loud ---------------------------------------------------------

def test_missing_index_file_fails_the_chunk(client, monkeypatch, tmp_path):
    """An operator asked for injection; producing zero captions instead would be
    indistinguishable from a day nobody described. The required sidecar raises, which
    is a 500 under a real server (and a dead-letter under the async worker) — either
    way the run stops instead of quietly writing a caption-less day."""
    injected_caption._CACHE.clear()
    monkeypatch.setenv("INJECT_CAPTION_BACKEND", "index")
    monkeypatch.setenv("INJECT_CAPTION_INDEX", str(tmp_path / "absent.jsonl"))
    c1 = make_c1(client.fake_storage, chunk_id="cap-missing")
    with pytest.raises(FileNotFoundError):
        client.post("/ingest", json=c1)


# ---- stage declaration -------------------------------------------------------

def test_stage_is_a_sidecar_that_shares_the_dialect(monkeypatch, tmp_path):
    from app.stagegraph.stage import stages_for
    stage = {s.name: s for s in stages_for("audio")}["injected_caption"]
    assert stage.kind == "sidecar" and stage.needs == ()
    assert stage.version_fragment(None) == ""        # additive: never forks the dialect
    assert stage.order not in {s.order for s in stages_for("audio")
                               if s.name != "injected_caption"}
