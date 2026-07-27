"""D18 / C10 — day-log materialization and the day-log fetch.

The materializer is LIFTED from continuum's `app/daylog.py` (the PRODUCT renderer over C2
records — not `morpheus/profiles/*.py`'s parity-locked `Profile.render_block`). Three rules
change, everything else is preserved, and this file is organised on exactly that split:

  PART 1 — the three CHANGED rules, one test each (and then some):
     1. membership is by `ingest_time`, not `t_start`;
     2. segment buckets sit on a GLOBAL epoch grid, not relative to the window start;
     3. one dialect per record — latest `ingest_time` wins per
        (chunk_id, content.kind, discriminator).
  PART 2 — the PRESERVED behaviours, as regressions against the continuum original: same-span
     merge, diarized sub-spans in their own buckets, block grouping by temporal adjacency
     (max_gap_s = 6*segment_seconds), the block_segments cap, `_block_zone`'s preference
     order, the exact anchor line, the Scene/Heard/World-text labels, quality handling, and
     the travel case (two device zones in one window, different local dates).
  PART 3 — the HTTP surface: GET /training/daylog, the C10 body, the cache and its
     invalidation, and the four failure modes.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app import schemas
from app.daylog import (
    daylog_recipe_id,
    DAYLOG_FORMAT_VERSION,
    DEFAULT_BLOCK_SEGMENTS,
    DEFAULT_SEGMENT_SECONDS,
    DayLogRecipeUnusable,
    build_daylog,
    daylog_fingerprint,
    dialect_key,
    materialize_daylog,
    recipe_segmentation,
    select_dialects,
)
from app.db import Store
from tests.conftest import make_c2

UTC = timezone.utc


@pytest.fixture()
def store(client) -> Store:
    """The same DB the `client` fixture just pointed the app at (declares `client` for
    ordering — the env vars only exist because that fixture set them)."""
    return Store(path=os.environ["STORAGE_DB_PATH"],
                 raw_root=os.environ["STORAGE_RAW_DIR"])


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso(epoch: float) -> str:
    """An RFC3339 UTC instant for a C2 t_start/t_end."""
    return _stamp(datetime.fromtimestamp(epoch, tz=UTC))


def _instant(raw: str) -> datetime:
    """Parse EITHER spelling the C10 body uses, so tests compare instants not strings.

    One body, one time axis, two serializations: the WINDOW's `t_start`/`t_end` are minted
    by storage's `db._TS_FMT` and end in `Z` ("2026-07-24T02:00:00Z"), while every SEGMENT
    bound comes from `datetime.isoformat()` and ends in a numeric offset
    ("2026-07-21T09:00:00+00:00"). They name the same instants, but `+` (0x2B) sorts below
    `Z` (0x5A), so string-comparing across the two forms is wrong exactly at equality — the
    same moment reads as "earlier" in the offset spelling. Anything in a test that puts a
    segment bound next to a window bound goes through here first.

    The spellings are not unified in `app/daylog.py` on purpose (see `daylog_body`):
    continuum's renderer also emits `isoformat()` bounds, and the M9 parity diff's D7 check
    compares those payloads byte-for-byte, so respelling storage's side would turn a
    passing check red for cosmetics.
    """
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def _land(store: Store, user_id: str, ingest_time: str, **kwargs) -> dict:
    """Land a C2 record and force its storage-assigned ingest_time.

    ingest_time is minted by storage at write and is deliberately not a C2 field, so a test
    that needs deterministic window membership sets it on the column directly — the same
    'reach into the columns' idiom tests/test_civil_time.py and tests/test_windows.py use.
    """
    record = make_c2(user_id=user_id, **kwargs)
    store.put_context(record)
    with store._connect() as conn:
        conn.execute(
            "UPDATE context_records SET ingest_time = ? WHERE record_id = ?",
            (ingest_time, record["record_id"]),
        )
        conn.commit()
    return record


def _backdate(store: Store, *, hours: int = 1) -> str:
    """Push every landed record's ingest_time into the past, and return the new value.

    ingest_time is minted at write and is not a C2 field, so a record landed over HTTP is
    always ingested `now` — while a window's end is `now - delta`, which would sit BELOW
    its own start and refuse to open. Backdating is what lets these tests exercise the real
    routes against the real clock instead of injecting time everywhere.
    """
    stamp = _stamp(datetime.now(UTC) - timedelta(hours=hours))
    with store._connect() as conn:
        conn.execute("UPDATE context_records SET ingest_time = ?", (stamp,))
        conn.commit()
    return stamp


def _caption(t_start: str, text: str, *, device_tz: str | None = None,
             chunk_id: str | None = None, **kwargs) -> dict:
    """A caption record — the simplest thing that renders a `Scene: ` line."""
    record = make_c2(t_start=t_start, t_end=t_start, chunk_id=chunk_id, **kwargs)
    # No `segments` key at all: C2 declares it as an array, so a null would be a schema
    # violation rather than "no sub-spans".
    record["content"] = {"kind": "caption", "text": text, "language": "en"}
    record["source"]["modality"] = "video"
    if device_tz:
        record["source"]["device_tz"] = device_tz
    return record


def _undiarized(t_start: str, text: str, **kwargs) -> dict:
    """A transcript record with no sub-spans — the parent-chunk ASR path."""
    record = make_c2(t_start=t_start, t_end=t_start, text=text, **kwargs)
    del record["content"]["segments"]
    return record


def _rows(*records_with_ingest) -> list[dict]:
    """Build the store's ingest-range row shape by hand: (record, ingest_time, seq)."""
    return [
        {"seq": seq, "ingest_time": ingest, "record": rec}
        for seq, (rec, ingest) in enumerate(records_with_ingest, start=1)
    ]


def _build(records, *, window_id="w20260724T040000Z", user_id="u1", home_tz="UTC", **kw):
    return build_daylog(records, window_id=window_id, user_id=user_id,
                        home_tz=home_tz, **kw)


# =====================================================================================
# PART 1 — the three CHANGED rules
# =====================================================================================

# --- change 1: membership is by ingest_time, not t_start -----------------------------


def test_membership_is_by_ingest_time_not_event_time(store):
    """THE watermark rule. A chunk captured Tuesday and uploaded Friday belongs to
    FRIDAY's window — and renders in a block anchored "On [Tuesday]". Late data cannot be
    lost because on an ingest-time watermark late data does not exist."""
    store.put_profile("u1", "UTC")
    tuesday = "2026-07-21T09:00:00Z"
    # Captured Tuesday, uploaded Friday.
    _land(store, "u1", "2026-07-24T02:00:00Z",
          **{"t_start": tuesday, "t_end": tuesday})
    window = store.open_training_window("u1", now=datetime(2026, 7, 24, 4, 0, 0, tzinfo=UTC))

    body = materialize_daylog(store, "u1", window["window_id"])

    # The window is FRIDAY's on the ingest axis...
    assert body["t_start"] <= "2026-07-24T02:00:00Z" < body["t_end"]
    # ...and the record is in it, anchored to the TUESDAY it was actually lived.
    assert len(body["blocks"]) == 1
    assert body["blocks"][0]["anchors"]["date"] == "2026-07-21"
    assert body["blocks"][0]["text"].startswith("On 2026-07-21, ")
    # The rendered content therefore starts DAYS BEFORE the window's own t_start. That is
    # the late-upload case working, not a bug.
    #
    # Compared as parsed INSTANTS, and by the actual gap. Two reasons, both of which a bare
    # `body["segments"][0]["t_start"] < body["t_start"]` got wrong:
    #   * the two fields are spelled differently (segment bounds carry `+00:00`, window
    #     bounds carry `Z` — see `_instant`), and `+` sorts below `Z`, so that string
    #     compare would have held even for a segment starting exactly AT the window start;
    #   * "days before" is the claim, and `<` alone would also pass for one second before.
    lead = _instant(body["t_start"]) - _instant(body["segments"][0]["t_start"])
    assert lead > timedelta(days=1), lead


def test_a_record_ingested_outside_the_window_is_excluded_even_if_its_event_time_is_inside(store):
    """The mirror image, and the one an event-time filter would get wrong: an event time
    that sits comfortably inside the window's clock range buys nothing if the record
    landed after `t_end`."""
    store.put_profile("u1", "UTC")
    _land(store, "u1", "2026-07-24T02:00:00Z",
          t_start="2026-07-24T02:30:00Z", t_end="2026-07-24T02:30:00Z",
          text="inside the window on both axes")
    window = store.open_training_window("u1", now=datetime(2026, 7, 24, 4, 0, 0, tzinfo=UTC))
    # Lands AFTER the window closed, but with an event time inside its range.
    _land(store, "u1", "2026-07-24T05:00:00Z",
          t_start="2026-07-24T02:31:00Z", t_end="2026-07-24T02:31:00Z",
          text="late arriving straggler")

    body = materialize_daylog(store, "u1", window["window_id"])
    rendered = " ".join(b["text"] for b in body["blocks"])
    assert "inside the window on both axes" in rendered
    assert "late arriving straggler" not in rendered


def test_build_daylog_has_no_event_time_filter_at_all(store):
    """continuum's `in_window(t_start, win)` guard is DELETED, not ported: the range query
    on the ingest axis already decided membership, so re-filtering on the event axis would
    throw away exactly the backlog this design exists to train."""
    far_past = _iso(datetime(2019, 3, 4, 11, 0, 0, tzinfo=UTC).timestamp())
    far_future = _iso(datetime(2031, 3, 4, 11, 0, 0, tzinfo=UTC).timestamp())
    daylog = _build([_caption(far_past, "a very old memory"),
                     _caption(far_future, "a very new one")])
    assert len(daylog.blocks) == 2
    assert daylog.blocks[0].anchors["date"] == "2019-03-04"
    assert daylog.blocks[1].anchors["date"] == "2031-03-04"


# --- change 2: the global epoch grid -------------------------------------------------


def test_segment_buckets_sit_on_the_global_epoch_grid():
    """floor(t_start / segment_seconds), with no window origin in the expression. Two
    moments 1 s apart across an epoch multiple land in DIFFERENT buckets; two moments 9 s
    apart inside one land in the SAME bucket."""
    base = datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp()  # a multiple of 10
    daylog = _build([
        _caption(_iso(base + 9), "end of bucket zero"),
        _caption(_iso(base + 10), "start of bucket one"),
    ])
    assert len(daylog.segments) == 2
    assert daylog.segments[0].t_start == "2026-07-24T12:00:00+00:00"
    assert daylog.segments[1].t_start == "2026-07-24T12:00:10+00:00"

    same = _build([_caption(_iso(base + 1), "a"), _caption(_iso(base + 9), "b")])
    assert len(same.segments) == 1
    assert same.segments[0].caption == ["a", "b"]


def test_the_grid_does_not_move_with_the_window(store):
    """The whole point of going global: a moment's bucket is the SAME whichever window
    collects it, so re-materialization is stable. Window-relative indices would also go
    negative here, because the record's event time precedes both windows."""
    epoch = datetime(2026, 7, 21, 9, 0, 7, tzinfo=UTC).timestamp()
    rec = _caption(_iso(epoch), "one moment")
    first = _build([rec], window_id="w20260724T040000Z")
    second = _build([rec], window_id="w20260731T040000Z")
    assert first.segments[0].t_start == second.segments[0].t_start == \
        "2026-07-21T09:00:00+00:00"
    # Only the seg_id's window prefix differs — the bucket itself did not move.
    assert first.segments[0].seg_id == "w20260724T040000Z_s00000"
    assert second.segments[0].seg_id == "w20260731T040000Z_s00000"


def test_seg_id_is_the_ordinal_not_the_epoch_bucket_index():
    """`{window_id}_s{ordinal:05d}` where ordinal is the position in the sorted segment
    list. It CANNOT be the epoch index: at 10 s buckets that is a nine-digit number, which
    would blow the fixed {:05d} width on the first record ever materialized."""
    base = datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp()
    daylog = _build([_caption(_iso(base), "a"),
                     _caption(_iso(base + 5000), "b"),
                     _caption(_iso(base + 90000), "c")])
    assert [s.seg_id for s in daylog.segments] == [
        "w20260724T040000Z_s00000",
        "w20260724T040000Z_s00001",
        "w20260724T040000Z_s00002",
    ]
    assert [b.block_id for b in daylog.blocks] == [
        "w20260724T040000Z_b0000",
        "w20260724T040000Z_b0001",
        "w20260724T040000Z_b0002",
    ]


# --- change 3: one dialect per record, latest ingest_time wins -----------------------


def test_one_dialect_per_record_latest_ingest_time_wins():
    """The cutover double-count fix. Two renderings of ONE chunk under two pipeline
    versions: only the latest-INGESTED survives. Keyed on ingest_time and not on
    pipeline_version because pipeline_version is a COMPOSED string (a mutate stage's
    enabledness is a version fragment) and therefore not orderable."""
    t = _iso(datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp())
    old = _caption(t, "a blurry doorway", chunk_id="chunk-A", pipeline_version="cap-v9")
    new = _caption(t, "a red front door", chunk_id="chunk-A", pipeline_version="cap-v2")
    rows = _rows((old, "2026-07-24T01:00:00Z"), (new, "2026-07-24T03:00:00Z"))

    kept = select_dialects(rows)
    assert [r["record"]["content"]["text"] for r in kept] == ["a red front door"]
    # And it renders once, not twice.
    daylog = _build([r["record"] for r in kept])
    assert daylog.blocks[0].text.endswith("Scene: a red front door")


def test_the_dialect_key_is_kind_aware():
    """Phase-3 proved captions and transcripts can share one pipeline_version, so a
    kind-blind rule would drop transcripts in order to drop captions."""
    t = _iso(datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp())
    cap = _caption(t, "a red front door", chunk_id="chunk-A", pipeline_version="v1")
    asr = _undiarized(t, "hello there", chunk_id="chunk-A", pipeline_version="v1")
    asr["record_id"] = "rec-asr"
    rows = _rows((cap, "2026-07-24T01:00:00Z"), (asr, "2026-07-24T03:00:00Z"))

    kept = select_dialects(rows)
    assert len(kept) == 2
    assert dialect_key(cap)[1] == "caption"
    assert dialect_key(asr)[1] == "transcript"


def test_the_dialect_key_is_discriminator_aware():
    """Two keyframes of ONE video chunk are two UNITS, not two dialects of one. The
    discriminator is what tells them apart — and it is independently readable on C2 only
    since 2026-07-27; before that it lived solely inside the record_id hash."""
    t = _iso(datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp())
    kf0 = _caption(t, "a red front door", chunk_id="chunk-A")
    kf0["record_id"], kf0["discriminator"] = "rec-kf0", "kf:0"
    kf1 = _caption(t, "a hallway", chunk_id="chunk-A")
    kf1["record_id"], kf1["discriminator"] = "rec-kf1", "kf:1"
    assert schemas.validate_c2(kf0) == [] and schemas.validate_c2(kf1) == []

    kept = select_dialects(_rows((kf0, "2026-07-24T01:00:00Z"),
                                 (kf1, "2026-07-24T01:00:00Z")))
    assert len(kept) == 2
    # ...but two DIALECTS of keyframe 0 collapse to one.
    kf0b = _caption(t, "a red door, ajar", chunk_id="chunk-A", pipeline_version="cap-v2")
    kf0b["record_id"], kf0b["discriminator"] = "rec-kf0-v2", "kf:0"
    kept = select_dialects(_rows((kf0, "2026-07-24T01:00:00Z"),
                                 (kf1, "2026-07-24T01:00:00Z"),
                                 (kf0b, "2026-07-24T03:00:00Z")))
    assert sorted(r["record"]["record_id"] for r in kept) == ["rec-kf0-v2", "rec-kf1"]


def test_absent_and_empty_discriminator_are_the_same_key():
    """The 1:1 case. A producer that starts emitting `discriminator: ""` must not
    double-count against its own earlier records, which carried no key at all."""
    t = _iso(datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp())
    without = _caption(t, "old dialect", chunk_id="chunk-A")
    with_empty = _caption(t, "new dialect", chunk_id="chunk-A", pipeline_version="cap-v2")
    with_empty["record_id"], with_empty["discriminator"] = "rec-2", ""
    assert dialect_key(without) == dialect_key(with_empty)
    kept = select_dialects(_rows((without, "2026-07-24T01:00:00Z"),
                                 (with_empty, "2026-07-24T03:00:00Z")))
    assert [r["record"]["content"]["text"] for r in kept] == ["new dialect"]


def test_the_dialect_tiebreak_is_deterministic_within_one_second():
    """ingest_time is minted at SECOND granularity, so a whole data-processing flush lands
    inside one value and "latest wins" would otherwise be decided by dict ordering. rowid
    is the tiebreak — the same stable one every ordered read in this service uses."""
    t = _iso(datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp())
    first = _caption(t, "first of the flush", chunk_id="chunk-A", pipeline_version="v1")
    second = _caption(t, "second of the flush", chunk_id="chunk-A", pipeline_version="v2")
    same_second = "2026-07-24T03:00:00Z"
    kept = select_dialects(_rows((first, same_second), (second, same_second)))
    assert [r["record"]["content"]["text"] for r in kept] == ["second of the flush"]
    # ...and it does not depend on which order they were handed in.
    reordered = [
        {"seq": 2, "ingest_time": same_second, "record": second},
        {"seq": 1, "ingest_time": same_second, "record": first},
    ]
    assert [r["record"]["content"]["text"] for r in select_dialects(reordered)] == \
        ["second of the flush"]


def test_a_reprocess_that_survives_selection_renders_once_end_to_end(store):
    """The whole rule, through the store: land a chunk, reprocess it under a NEW
    pipeline_version so both rows exist, and materialize. The day-log carries the new
    dialect exactly once."""
    store.put_profile("u1", "UTC")
    t = "2026-07-24T02:00:00Z"
    old = _caption(t, "a blurry doorway", chunk_id="chunk-A", pipeline_version="cap-v1",
                   user_id="u1")
    new = _caption(t, "a red front door", chunk_id="chunk-A", pipeline_version="cap-v2",
                   user_id="u1")
    for record, ingest in ((old, "2026-07-24T02:05:00Z"), (new, "2026-07-24T02:06:00Z")):
        store.put_context(record)
        with store._connect() as conn:
            conn.execute("UPDATE context_records SET ingest_time = ? WHERE record_id = ?",
                         (ingest, record["record_id"]))
            conn.commit()
    window = store.open_training_window("u1", now=datetime(2026, 7, 24, 4, 0, 0, tzinfo=UTC))

    body = materialize_daylog(store, "u1", window["window_id"])
    rendered = " ".join(b["text"] for b in body["blocks"])
    assert rendered.count("a red front door") == 1
    assert "a blurry doorway" not in rendered


# =====================================================================================
# PART 2 — PRESERVED behaviours (regressions against the continuum original)
# =====================================================================================


def test_same_span_records_merge_into_one_segment():
    t = _iso(datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp())
    cap = _caption(t, "a red door", chunk_id="c1")
    ocr = _caption(t, "EXIT", chunk_id="c2")
    ocr["content"]["kind"] = "ocr"
    asr = _undiarized(t, "hello there", chunk_id="c3")
    daylog = _build([cap, ocr, asr])
    assert len([s for s in daylog.segments if not s.is_empty()]) == 1
    seg = daylog.segments[0]
    assert seg.caption == ["a red door"]
    assert seg.ocr == ["EXIT"]
    assert seg.asr[0]["text"] == "hello there"


def test_diarized_subspans_land_in_their_own_buckets_by_their_own_t_start():
    """A 30 s VAD chunk spans three buckets and its speech must be anchored where it was
    actually said, not where the chunk began."""
    base = datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp()
    rec = make_c2(chunk_id="c1", t_start=_iso(base), t_end=_iso(base + 30),
                  text="first second")
    rec["content"]["segments"] = [
        {"t_start": _iso(base), "t_end": _iso(base + 2), "text": "first", "speaker": "a"},
        {"t_start": _iso(base + 25), "t_end": _iso(base + 27), "text": "second",
         "speaker": "b"},
    ]
    daylog = _build([rec])
    non_empty = [s for s in daylog.segments if not s.is_empty()]
    assert len(non_empty) == 2                      # bucket +0 and bucket +20
    assert non_empty[0].asr[0] == {"spk": "a", "text": "first", "t": _iso(base)}
    assert non_empty[1].asr[0]["spk"] == "b"
    # The parent chunk's own text is NOT added on top of its sub-spans.
    assert "first second" not in [a["text"] for s in non_empty for a in s.asr]


def test_blocks_break_on_a_gap_of_more_than_six_segments():
    """max_gap_s = 6 * segment_seconds. A camera-off gap starts a new block, so one anchor
    line never spans hours of silence."""
    base = datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp()
    # 50 s apart: gap between bucket-0's END (+10) and bucket-5's start (+50) is 40 s <= 60.
    together = _build([_caption(_iso(base), "a"), _caption(_iso(base + 50), "b")])
    assert len(together.blocks) == 1
    # 80 s apart: gap is 70 s > 60 -> two blocks.
    apart = _build([_caption(_iso(base), "a"), _caption(_iso(base + 80), "b")])
    assert len(apart.blocks) == 2
    assert [len(b.seg_ids) for b in apart.blocks] == [1, 1]


def test_a_block_holds_at_most_block_segments_segments():
    base = datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp()
    records = [_caption(_iso(base + 10 * i), f"caption {i}") for i in range(30)]
    daylog = _build(records)
    assert len(daylog.segments) == 30
    assert [len(b.seg_ids) for b in daylog.blocks] == [12, 12, 6]
    # Every seg_id a block cites is a real segment, and each is cited exactly once.
    cited = [sid for b in daylog.blocks for sid in b.seg_ids]
    assert cited == [s.seg_id for s in daylog.segments]


def test_the_anchor_line_and_labels_are_byte_exact():
    """The rendered block is what the model trains on, so its bytes are the interface —
    including the EN DASH between the two clock readings and the label spellings."""
    base = datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp()
    cap = _caption(_iso(base), "a red door", chunk_id="c1")
    ocr = _caption(_iso(base), "EXIT", chunk_id="c2")
    ocr["content"]["kind"] = "ocr"
    asr = _undiarized(_iso(base), "hello there", chunk_id="c3")
    block = _build([cap, ocr, asr]).blocks[0]
    assert block.text == (
        "On 2026-07-24, around 12:00–12:00 local time:\n"
        "Scene: a red door\n"
        "Heard: hello there\n"
        "World text (OCR): EXIT"
    )
    assert block.anchors == {"date": "2026-07-24", "place": None}


def test_a_diarized_speaker_is_named_in_the_heard_line():
    base = datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp()
    rec = make_c2(chunk_id="c1", t_start=_iso(base), t_end=_iso(base + 2), text="hi")
    rec["content"]["segments"] = [
        {"t_start": _iso(base), "t_end": _iso(base + 1), "text": "hi", "speaker": "ana"},
        {"t_start": _iso(base + 1), "t_end": _iso(base + 2), "text": "hey",
         "speaker": None},
    ]
    block = _build([rec]).blocks[0]
    assert "Heard: ana: hi | hey" in block.text


def test_only_the_lines_with_content_appear():
    base = datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp()
    block = _build([_caption(_iso(base), "just a caption")]).blocks[0]
    assert block.text == "On 2026-07-24, around 12:00–12:00 local time:\nScene: just a caption"
    assert "Heard:" not in block.text and "World text" not in block.text


def test_empty_text_never_creates_a_bucket():
    base = datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp()
    empty = _caption(_iso(base), "   ", chunk_id="c1")
    assert _build([empty]).segments == []


def test_quality_is_none_when_nothing_is_scored_and_the_min_over_what_is():
    """C2 v0 carries no quality field, so every segment comes out UNSCORED — which is not
    the same as scoring zero, and continuum's amplification gate deliberately lets None
    through. When scores do exist a block takes the min over the SCORED segments only; an
    unscored neighbour must not drag the block to None."""
    from app.daylog import _render_block

    base = datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp()
    daylog = _build([_caption(_iso(base), "a"), _caption(_iso(base + 10), "b"),
                     _caption(_iso(base + 20), "c")])
    assert [s.quality for s in daylog.segments] == [None, None, None]
    assert len(daylog.blocks) == 1 and daylog.blocks[0].quality is None

    daylog.segments[0].quality = 0.9
    daylog.segments[1].quality = 0.4        # segments[2] stays unscored
    assert _render_block("w1", "UTC", 0, daylog.segments).quality == 0.4


# --- _block_zone: the D17 preference order, preserved exactly -------------------------


def test_the_anchor_line_uses_the_capturing_devices_zone():
    """The device's zone WINS over the profile fallback: it is the only value that is right
    when the user was travelling, because it says where they physically were."""
    t = _iso(datetime(2026, 7, 24, 6, 0, 0, tzinfo=UTC).timestamp())
    block = _build([_caption(t, "a shrine", device_tz="Asia/Tokyo")], home_tz="UTC").blocks[0]
    assert "15:00" in block.text            # 06:00 UTC -> 15:00 Tokyo
    assert "06:00" not in block.text        # the UTC reading must NOT appear


def test_the_anchor_line_falls_back_to_home_tz_when_the_device_is_silent():
    """Pre-D17 records (and devices that cannot determine a zone) carry no device_tz; they
    render in the user's profile home_tz, not in UTC and not by crashing."""
    t = _iso(datetime(2026, 7, 24, 6, 0, 0, tzinfo=UTC).timestamp())
    block = _build([_caption(t, "a shrine")], home_tz="Asia/Tokyo").blocks[0]
    assert "15:00" in block.text


def test_an_unresolvable_device_zone_degrades_instead_of_killing_the_night():
    """A garbage zone id from one device must never sink a whole training run — it
    DEGRADES to the profile fallback rather than raising."""
    t = _iso(datetime(2026, 7, 24, 6, 0, 0, tzinfo=UTC).timestamp())
    block = _build([_caption(t, "a shrine", device_tz="Mars/Olympus_Mons")],
                   home_tz="UTC").blocks[0]
    assert "06:00" in block.text


def test_the_first_contributing_record_stamps_the_bucket_zone():
    """A ~10 s bucket cannot straddle a real zone change, so first-writer-wins is not a
    lossy choice — it just avoids re-deciding per contributing record."""
    base = datetime(2026, 7, 24, 6, 0, 0, tzinfo=UTC).timestamp()
    daylog = _build([_caption(_iso(base), "a", device_tz="Asia/Tokyo", chunk_id="c1"),
                     _caption(_iso(base + 1), "b", device_tz="Europe/Paris", chunk_id="c2")])
    assert daylog.segments[0].tz == "Asia/Tokyo"


def test_travel_two_zones_in_one_window_render_with_different_local_dates():
    """The case a single per-window timezone structurally cannot express: two blocks
    captured in different zones each render in their OWN zone, and the DATES differ — which
    is exactly the failure a wrong tz hides."""
    base = datetime(2026, 7, 24, 6, 0, 0, tzinfo=UTC).timestamp()
    boarding = _caption(_iso(base), "boarding", device_tz="America/Los_Angeles",
                        chunk_id="c1")
    landed = _caption(_iso(base + 10 * 3600), "landed", device_tz="Asia/Tokyo",
                      chunk_id="c2")
    blocks = _build([boarding, landed], home_tz="UTC").blocks
    assert len(blocks) == 2, [b.text for b in blocks]
    assert "23:00" in blocks[0].text            # 06:00 UTC -> 23:00 previous day in LA
    assert "01:00" in blocks[1].text            # 16:00 UTC -> 01:00 next day in Tokyo
    assert blocks[0].anchors["date"] == "2026-07-23"
    assert blocks[1].anchors["date"] == "2026-07-25"
    assert blocks[0].anchors["date"] != blocks[1].anchors["date"]


def test_the_travel_case_survives_the_round_trip_through_http(client, store):
    """...and the same thing end to end, because the zone is resolved at materialization
    and must not be flattened by the cache or the response model."""
    store.put_profile("u1", "UTC")
    base = datetime(2026, 7, 24, 6, 0, 0, tzinfo=UTC).timestamp()
    for offset, text, tz, chunk in ((0, "boarding", "America/Los_Angeles", "c1"),
                                    (10 * 3600, "landed", "Asia/Tokyo", "c2")):
        record = _caption(_iso(base + offset), text, device_tz=tz, chunk_id=chunk,
                          user_id="u1")
        assert client.post("/context/records", json=record).status_code == 200
    _backdate(store)
    window = client.post("/training/windows", json={"user_id": "u1"}).json()

    body = client.get("/training/daylog",
                      params={"user_id": "u1", "window_id": window["window_id"]}).json()
    dates = [b["anchors"]["date"] for b in body["blocks"]]
    assert dates == ["2026-07-23", "2026-07-25"]
    # home_tz on the body is the FALLBACK zone, which neither block ended up using.
    assert body["home_tz"] == "UTC"
    assert [s["tz"] for s in body["segments"]] == ["America/Los_Angeles", "Asia/Tokyo"]


# =====================================================================================
# PART 3 — the HTTP surface: GET /training/daylog
# =====================================================================================


def _window_with_one_caption(client, store, user_id="u1", home_tz="UTC",
                             text="a red front door") -> dict:
    store.put_profile(user_id, home_tz)
    record = _caption(_iso(datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp()), text,
                      user_id=user_id, chunk_id="chunk-A")
    assert client.post("/context/records", json=record).status_code == 200
    _backdate(store)
    return client.post("/training/windows", json={"user_id": user_id}).json()


def _window_with_captions(client, store, offsets, *, user_id="u1", home_tz="UTC") -> dict:
    """A window over one caption per offset (seconds from 2026-07-24T12:00:00Z).

    That base instant is a multiple of BOTH grids under test (10 s and 60 s), so a bucket
    boundary never falls mid-fixture and the two shapes differ only where the knob says.
    """
    store.put_profile(user_id, home_tz)
    base = datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp()
    for i, offset in enumerate(offsets):
        record = _caption(_iso(base + offset), f"caption {i:02d}",
                          user_id=user_id, chunk_id=f"chunk-{i:02d}")
        assert client.post("/context/records", json=record).status_code == 200
    _backdate(store)
    return client.post("/training/windows", json={"user_id": user_id}).json()


def test_the_daylog_fetch_serves_a_valid_c10_body(client, store):
    window = _window_with_one_caption(client, store)
    response = client.get("/training/daylog",
                          params={"user_id": "u1", "window_id": window["window_id"]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert schemas.validate_c10(body) == []
    assert body["contract"] == "C10" and body["version"] == "1"
    assert body["user_id"] == "u1"
    assert body["window_id"] == window["window_id"]
    # The window's INGEST bounds, verbatim from the ledger.
    assert (body["t_start"], body["t_end"]) == (window["t_start"], window["t_end"])
    assert body["daylog_format_version"] == DAYLOG_FORMAT_VERSION
    # The body stamps the recipe the service is CONFIGURED with, whatever that is —
    # asserting a version literal here made this test fail on a legitimate re-pin
    # (v1.0 -> v1.1, 2026-07-27) without anything being wrong.
    assert body["recipe_id"] == daylog_recipe_id()
    assert body["home_tz"] == "UTC"
    assert body["content_fingerprint"]
    assert body["blocks"][0]["text"].endswith("Scene: a red front door")


def test_home_tz_on_the_body_is_the_fallback_zone_actually_used(client, store):
    """The D17 follow-up this field closes: without it a night rendered under the wrong
    profile zone is unfalsifiable after the fact."""
    window = _window_with_one_caption(client, store, home_tz="Asia/Tokyo")
    body = client.get("/training/daylog",
                      params={"user_id": "u1", "window_id": window["window_id"]}).json()
    assert body["home_tz"] == "Asia/Tokyo"
    # ...and it is the zone the block actually rendered in (no device_tz on the record).
    assert body["blocks"][0]["text"].startswith("On 2026-07-24, around 21:00")


def test_an_empty_window_is_an_empty_daylog_not_an_error(client, store):
    """A window with no records is a legitimate, servable day-log — the cycle reads it and
    closes with skipped_no_data, which deliberately does NOT advance the watermark."""
    store.put_profile("u1", "UTC")
    old = _caption("2026-07-01T12:00:00Z", "ancient", chunk_id="chunk-old", user_id="u1")
    assert client.post("/context/records", json=old).status_code == 200
    with store._connect() as conn:
        conn.execute("UPDATE context_records SET ingest_time = '2026-07-01T12:00:00Z'")
        conn.commit()
    window = store.open_training_window("u1", now=datetime(2026, 7, 2, 0, 0, 0, tzinfo=UTC))
    # Force the window's floor past the only record so the range is genuinely empty.
    with store._connect() as conn:
        conn.execute("UPDATE training_windows SET t_start = '2026-07-01T13:00:00Z'")
        conn.commit()

    body = client.get("/training/daylog",
                      params={"user_id": "u1", "window_id": window["window_id"]}).json()
    assert body["segments"] == [] and body["blocks"] == []
    assert schemas.validate_c10(body) == []


def test_the_daylog_is_materialized_once_and_cached(client, store):
    window = _window_with_one_caption(client, store)
    params = {"user_id": "u1", "window_id": window["window_id"]}
    first = client.get("/training/daylog", params=params).json()

    with store._connect() as conn:
        cached = conn.execute("SELECT * FROM day_logs").fetchall()
    assert len(cached) == 1
    assert cached[0]["content_fingerprint"] == first["content_fingerprint"]

    second = client.get("/training/daylog", params=params).json()
    assert second == first


def test_the_cache_is_invalidated_when_a_record_inside_the_window_is_rewritten(client, store):
    """The one hole a cache over an upsert has: a SAME-VERSION reprocess rewrites a
    record's text in place while deliberately preserving its ingest_time, so it stays
    inside a window that may already have been rendered and served."""
    window = _window_with_one_caption(client, store, text="a blurry doorway")
    params = {"user_id": "u1", "window_id": window["window_id"]}
    before = client.get("/training/daylog", params=params).json()
    assert "a blurry doorway" in before["blocks"][0]["text"]

    # Same record_id (deterministic on chunk_id + pipeline_version), same version, better
    # text — an in-place correction, which is the upsert branch of put_context.
    fixed = _caption(_iso(datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp()),
                     "a red front door", chunk_id="chunk-A", user_id="u1")
    assert client.post("/context/records", json=fixed).status_code == 200

    after = client.get("/training/daylog", params=params).json()
    assert "a red front door" in after["blocks"][0]["text"]
    assert after["content_fingerprint"] != before["content_fingerprint"]


def test_a_corrected_home_tz_re_materializes_rather_than_serving_the_typo_forever(
        client, store):
    """`home_tz` is a RENDER INPUT, not a note on the row.

    It is the fallback zone for every record that carries no `device_tz`, so it fixes the
    anchor line's local DATE — the one thing a day-log asserts that nothing downstream can
    re-derive. A day-log rendered under an operator's typo'd zone (Pacific/Kiritimati,
    UTC+14, one keystroke from nothing in particular) must not keep serving that date after
    the profile is corrected: format_version and recipe_id are compared for exactly this
    reason and home_tz is the third input of the same kind.
    """
    window = _window_with_one_caption(client, store, home_tz="Pacific/Kiritimati")
    params = {"user_id": "u1", "window_id": window["window_id"]}

    typo = client.get("/training/daylog", params=params).json()
    assert typo["home_tz"] == "Pacific/Kiritimati"
    assert typo["blocks"][0]["anchors"]["date"] == "2026-07-25"   # 12:00Z at UTC+14

    store.put_profile("u1", "America/New_York")

    corrected = client.get("/training/daylog", params=params).json()
    assert corrected["home_tz"] == "America/New_York"
    assert corrected["blocks"][0]["anchors"]["date"] == "2026-07-24"
    assert corrected["blocks"][0]["text"].startswith("On 2026-07-24, around 08:00")
    assert corrected["content_fingerprint"] != typo["content_fingerprint"]
    # The cache was REPLACED, not shadowed: the row now records the zone actually used.
    with store._connect() as conn:
        rows = conn.execute("SELECT home_tz FROM day_logs").fetchall()
    assert [r["home_tz"] for r in rows] == ["America/New_York"]


def test_a_cached_daylog_still_serves_after_the_profile_disappears(client, store):
    """The zone is compared only when there IS a current one to compare against: the
    profile is required to BUILD and not to SERVE. A materialized night already records the
    zone it rendered under, so losing the profile afterwards must not retroactively make it
    unreadable — and must not silently re-render it either."""
    window = _window_with_one_caption(client, store, home_tz="Asia/Tokyo")
    params = {"user_id": "u1", "window_id": window["window_id"]}
    first = client.get("/training/daylog", params=params).json()

    with store._connect() as conn:
        conn.execute("DELETE FROM user_profiles WHERE user_id = 'u1'")
        conn.commit()

    again = client.get("/training/daylog", params=params)
    assert again.status_code == 200, again.text
    assert again.json() == first


def test_a_format_version_bump_re_materializes_rather_than_serving_the_old_dialect(
        client, store, monkeypatch):
    window = _window_with_one_caption(client, store)
    params = {"user_id": "u1", "window_id": window["window_id"]}
    first = client.get("/training/daylog", params=params).json()
    assert first["daylog_format_version"] == DAYLOG_FORMAT_VERSION

    from app import daylog as daylog_module
    monkeypatch.setattr(daylog_module, "DAYLOG_FORMAT_VERSION", "99")
    second = client.get("/training/daylog", params=params).json()
    assert second["daylog_format_version"] == "99"
    with store._connect() as conn:
        rows = conn.execute("SELECT daylog_format_version FROM day_logs").fetchall()
    assert [r["daylog_format_version"] for r in rows] == ["99"]


def test_the_recipe_id_is_service_config_never_a_request_parameter(client, store,
                                                                   monkeypatch):
    """recipe_id is GLOBAL and versioned and it enters continuum's stage keys, so a caller
    who could choose it per fetch could fork the id per user."""
    monkeypatch.setenv("STORAGE_DAYLOG_RECIPE_ID", "consolidation-test-1min-v1.0")
    window = _window_with_one_caption(client, store)
    params = {"user_id": "u1", "window_id": window["window_id"]}
    body = client.get("/training/daylog", params=params).json()
    assert body["recipe_id"] == "consolidation-test-1min-v1.0"
    # ...and the stamp is a fact about the RENDER, not a label beside it: this recipe
    # declares corpus.segment_seconds=60, so the buckets are 60 s wide and not the 10 s of
    # the module constants. (Asserting only the id is what let the two drift apart.)
    assert body["segments"][0]["t_end"] == "2026-07-24T12:01:00+00:00"
    # Passing it as a query parameter is ignored, not honoured.
    other = client.get("/training/daylog",
                       params={**params, "recipe_id": "something-else"}).json()
    assert other["recipe_id"] == "consolidation-test-1min-v1.0"


def test_the_segment_grid_comes_from_the_stamped_recipes_corpus_knobs(
        client, store, monkeypatch):
    """The body stamps `recipe_id`, so the render must USE that recipe's `corpus` knobs.

    continuum keys its journal stage on (recipe_id, content_fingerprint) and records
    recipe_id in C5 lineage, so a night rendered on a 10 s grid while claiming a recipe
    that declares 60 s is audited as trained under a recipe it was not trained under — and
    nothing downstream can tell afterwards. consolidation-test-1min-v1.0 declares
    segment_seconds=60: three captions 20 s apart are ONE bucket under it and THREE under
    consolidation-v1.0's 10 s grid.
    """
    monkeypatch.setenv("STORAGE_DAYLOG_RECIPE_ID", "consolidation-test-1min-v1.0")
    window = _window_with_captions(client, store, [0, 20, 40])
    params = {"user_id": "u1", "window_id": window["window_id"]}

    body = client.get("/training/daylog", params=params).json()
    assert body["recipe_id"] == "consolidation-test-1min-v1.0"
    assert len(body["segments"]) == 1
    assert (body["segments"][0]["t_start"], body["segments"][0]["t_end"]) == (
        "2026-07-24T12:00:00+00:00", "2026-07-24T12:01:00+00:00")
    assert body["segments"][0]["caption"] == ["caption 00", "caption 01", "caption 02"]

    # The control, over the SAME records: re-pin the default recipe and the 10 s grid comes
    # back. (It re-materializes because recipe_id no longer matches the cached row.)
    monkeypatch.delenv("STORAGE_DAYLOG_RECIPE_ID")
    default = client.get("/training/daylog", params=params).json()
    assert default["recipe_id"] == daylog_recipe_id()
    assert len(default["segments"]) == 3
    assert default["segments"][0]["t_end"] == "2026-07-24T12:00:10+00:00"


def test_the_block_cap_comes_from_the_stamped_recipes_corpus_knobs(
        client, store, monkeypatch):
    """The other `corpus` knob, on the same rule: block_segments=5 in the test recipe, 12
    in consolidation-v1.0. Eight captions a minute apart are 5+3 under the first and a
    single block under the second."""
    monkeypatch.setenv("STORAGE_DAYLOG_RECIPE_ID", "consolidation-test-1min-v1.0")
    window = _window_with_captions(client, store, [60 * i for i in range(8)])
    params = {"user_id": "u1", "window_id": window["window_id"]}

    body = client.get("/training/daylog", params=params).json()
    assert [len(b["seg_ids"]) for b in body["blocks"]] == [5, 3]

    monkeypatch.delenv("STORAGE_DAYLOG_RECIPE_ID")
    default = client.get("/training/daylog", params=params).json()
    assert [len(b["seg_ids"]) for b in default["blocks"]] == [8]


def test_an_unresolvable_recipe_refuses_instead_of_falling_back_to_the_constants(
        client, store, monkeypatch):
    """FAIL LOUDLY, because the silent fallback IS the bug: rendering under the module
    constants while stamping a recipe id we could not even read produces an artifact whose
    provenance is wrong and uncheckable. 500 because recipe_id is service config and never
    a request parameter — the caller cannot fix this."""
    monkeypatch.setenv("STORAGE_DAYLOG_RECIPE_ID", "no-such-recipe-v9.9")
    window = _window_with_one_caption(client, store)
    params = {"user_id": "u1", "window_id": window["window_id"]}

    response = client.get("/training/daylog", params=params)
    assert response.status_code == 500, response.text
    detail = response.json()["detail"]
    assert detail["error"] == "day-log recipe cannot be resolved"
    assert detail["recipe_id"] == "no-such-recipe-v9.9"

    # Nothing was cached under a recipe that does not exist, so re-pinning a real one is
    # all it takes to recover.
    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM day_logs").fetchone()["n"] == 0
    monkeypatch.setenv("STORAGE_DAYLOG_RECIPE_ID", "consolidation-v1.0")
    assert client.get("/training/daylog", params=params).status_code == 200


def test_a_recipe_without_usable_corpus_knobs_refuses(client, store, monkeypatch, tmp_path):
    """A registry that answers is not the same as a recipe that segments. A recipe with no
    `corpus` block — or with a knob that cannot divide an epoch grid — is refused rather
    than quietly completed from the constants, for the same reason as an unknown id."""
    synthetic = tmp_path / "synthetic-recipes"
    synthetic.mkdir()
    artifact = synthetic / "knobless-v1.0.json"
    artifact.write_text(json.dumps({"recipe_id": "knobless-v1.0",
                                    "train": {"lr": 0.0001}}))
    monkeypatch.setenv("STORAGE_RECIPES_DIR", str(synthetic))
    monkeypatch.setenv("STORAGE_DAYLOG_RECIPE_ID", "knobless-v1.0")
    window = _window_with_one_caption(client, store)

    with pytest.raises(DayLogRecipeUnusable) as refused:
        materialize_daylog(store, "u1", window["window_id"])
    assert refused.value.detail["error"] == "day-log recipe has no corpus block"

    artifact.write_text(json.dumps({
        "recipe_id": "knobless-v1.0",
        "corpus": {"segment_seconds": 0, "block_segments": 12}}))
    with pytest.raises(DayLogRecipeUnusable) as refused:
        materialize_daylog(store, "u1", window["window_id"])
    assert refused.value.detail["knob"] == "corpus.segment_seconds"

    # ...and against the REAL registry the knobs are read verbatim, both of them — which
    # is also what keeps the module constants honest as build_daylog's defaults.
    monkeypatch.delenv("STORAGE_RECIPES_DIR")
    assert recipe_segmentation("consolidation-v1.0") == (DEFAULT_SEGMENT_SECONDS,
                                                         DEFAULT_BLOCK_SEGMENTS)


def test_the_fingerprint_is_stable_across_renders_and_moves_with_content(client, store):
    """content_fingerprint is a journal stage key, compared only to ITSELF across runs."""
    window = _window_with_one_caption(client, store)
    params = {"user_id": "u1", "window_id": window["window_id"]}
    body = client.get("/training/daylog", params=params).json()
    # Re-deriving it from the served body reproduces it exactly (nothing hidden inside).
    from app.daylog import Block, DayLog, Segment
    rebuilt = DayLog(
        window_id=body["window_id"], user_id=body["user_id"], home_tz=body["home_tz"],
        segments=[Segment(**s) for s in body["segments"]],
        blocks=[Block(**b) for b in body["blocks"]],
    )
    assert daylog_fingerprint(rebuilt) == body["content_fingerprint"]


def test_a_user_without_a_profile_is_refused_loudly(client, store):
    """409, not a UTC render. There is no server-side default timezone anywhere in the
    system, so substituting one would anchor a whole night to the wrong local date and
    nothing downstream could tell."""
    record = _caption(_iso(datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp()), "hi")
    assert client.post("/context/records", json=record).status_code == 200
    _backdate(store)
    window = client.post("/training/windows", json={"user_id": "user-1"}).json()

    response = client.get("/training/daylog",
                          params={"user_id": "user-1", "window_id": window["window_id"]})
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "no profile"
    # Nothing was cached, so writing the profile is all it takes to recover.
    store.put_profile("user-1", "UTC")
    assert client.get("/training/daylog",
                      params={"user_id": "user-1",
                              "window_id": window["window_id"]}).status_code == 200


def test_an_unknown_window_is_a_404(client, store):
    store.put_profile("u1", "UTC")
    response = client.get("/training/daylog",
                          params={"user_id": "u1", "window_id": "w20260724T040000Z"})
    assert response.status_code == 404


def test_a_cross_user_fetch_fails_closed(client, store):
    """window_id is a PER-USER token derived from an instant, so an id-only fetch would be
    a cross-user read of somebody's whole day. Isolation fails closed: it 404s rather than
    revealing that the id names someone else's window."""
    window = _window_with_one_caption(client, store, user_id="u1")
    store.put_profile("u2", "UTC")
    response = client.get("/training/daylog",
                          params={"user_id": "u2", "window_id": window["window_id"]})
    assert response.status_code == 404


def test_a_malformed_window_id_is_a_422(client, store):
    store.put_profile("u1", "UTC")
    response = client.get("/training/daylog",
                          params={"user_id": "u1", "window_id": "w2026-07-24"})
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "malformed window_id"


def test_the_day_log_table_appears_on_a_preexisting_db(tmp_path):
    """The D17 migration idiom: a live DB created before this table existed must gain it.
    New TABLES and INDEXES ride in _SCHEMA alone (CREATE ... IF NOT EXISTS runs on a live
    DB); only new COLUMNS on existing tables need a _MIGRATIONS entry, and this adds none.
    """
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE context_records (record_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, "
        " t_start TEXT NOT NULL, t_end TEXT, chunk_id TEXT, pipeline_version TEXT, "
        " ingest_time TEXT NOT NULL, record_json TEXT NOT NULL);"
    )
    conn.commit()
    conn.close()

    store = Store(path=str(db), raw_root=str(tmp_path / "raw"))
    with store._connect() as check:
        tables = {r["name"] for r in check.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        indexes = {r["name"] for r in check.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert "day_logs" in tables
    assert "idx_day_logs_user_range" in indexes
    assert "idx_context_user_ingest" in indexes
