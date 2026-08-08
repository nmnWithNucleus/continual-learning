"""D28 / C10 v2 — day-log materialization over C2 v1 slots, and the day-log fetch.

The v2 renderer walks ``content.slots``. This file is organised in four parts:

  PART 1 — the CHANGED rules, one test each (and then some):
     1. membership is by `updated_at` (D27), not `t_start`;
     2. segment buckets sit on a GLOBAL epoch grid, not relative to the window start;
     3. one record per chunk — latest `updated_at` wins per (chunk_id), rowid tiebreak
        (D28; a chunk has no siblings, so (chunk_id) is the whole key).
  PART 2 — slot routing + PRESERVED behaviours: caption→Scene, ocr→World text,
     transcript splits speaker-bucketed by their OWN t_start (both timestamp spellings),
     the RULED asr fallback (spk null) when transcript is absent, holes render as
     absence, empty values are honest claims, no acoustic route; plus same-span merge,
     block grouping and caps, the exact anchor line, `_block_zone`'s preference order,
     quality handling, travel.
  PART 3 — the HTTP surface: GET /training/daylog, the C10 v2 body + stamps, the cache
     and its R2 invalidation, and the failure modes.
  PART 4 — the heal×window matrix, all THREE shapes:
     a filling heal re-windows; a byte-identical still-holey re-POST does not; a
     byte-different-but-NOT-fuller heal (hole migrated between slots) re-windows and
     the renderer shows the new truth.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

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
from tests.conftest import (
    make_c2,
    slot_asr,
    slot_acoustic,
    slot_caption,
    slot_ocr,
    slot_transcript,
)

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
    by storage's `db._TS_FMT` and end in `Z`, while every SEGMENT bound comes from
    `datetime.isoformat()` and ends in a numeric offset. They name the same instants, but
    `+` (0x2B) sorts below `Z` (0x5A), so string-comparing across the two forms is wrong
    exactly at equality. Anything in a test that puts a segment bound next to a window
    bound goes through here first.
    """
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


# --- C2 v1 fixture builders ---------------------------------------------------------


def _v1(t_start: str, slots: dict[str, Any], *, chunk_id: Optional[str] = None,
        user_id: str = "u1", device_tz: Optional[str] = None,
        pipeline_version: str = "asr.v1-mock.v1", modality: str = "audio",
        t_end: Optional[str] = None) -> dict[str, Any]:
    record = make_c2(user_id=user_id, chunk_id=chunk_id, t_start=t_start,
                     t_end=t_end or t_start, slots=slots,
                     pipeline_version=pipeline_version, modality=modality)
    if device_tz:
        record["source"]["device_tz"] = device_tz
    return record


def _cap(t_start: str, text: str, **kw) -> dict[str, Any]:
    """A caption record — the simplest thing that renders a `Scene: ` line."""
    kw.setdefault("modality", "video")
    kw.setdefault("pipeline_version", "clipcap.v1-mock.v1+clipprep.v1-mock.v1")
    return _v1(t_start, {"caption": slot_caption(text)}, **kw)


def _ocr(t_start: str, text: str, **kw) -> dict[str, Any]:
    kw.setdefault("modality", "video")
    kw.setdefault("pipeline_version", "clipprep.v1-mock.v1+screentext.v1-mock.v1")
    return _v1(t_start, {"ocr": slot_ocr(text)}, **kw)


def _asr(t_start: str, text: str, *, splits: Optional[list] = None, **kw) -> dict[str, Any]:
    """An asr-only record — the transcript slot is ABSENT (never attempted or holed),
    so the ruled fallback path renders it."""
    return _v1(t_start, {"asr": slot_asr(text, splits=splits)}, **kw)


def _tr(t_start: str, splits: list, *, asr_text: Optional[str] = None, **kw) -> dict[str, Any]:
    """A record carrying the speaker-aligned transcript slot (plus its asr witness)."""
    slots: dict[str, Any] = {"transcript": slot_transcript(splits)}
    if asr_text is not None:
        slots["asr"] = slot_asr(asr_text)
    kw.setdefault("pipeline_version",
                  "asr.v1-mock.v1+diarize.v1-mock.v1+speaker_align.v1-mock.v1")
    return _v1(t_start, slots, **kw)


def _rows(*records_with_stamp) -> list[dict]:
    """Build the store's window-range row shape by hand: (record, updated_at, seq)."""
    return [
        {"seq": seq, "updated_at": stamp, "record": rec}
        for seq, (rec, stamp) in enumerate(records_with_stamp, start=1)
    ]


def _land(store: Store, record: dict, stamp: str) -> dict:
    """Land a record and force its storage-assigned stamps to `stamp` — the same
    'reach into the columns' idiom tests/test_windows.py uses."""
    store.put_context(record)
    with store._connect() as conn:
        conn.execute(
            "UPDATE context_records SET created_at = ?, updated_at = ? "
            "WHERE record_id = ?",
            (stamp, stamp, record["record_id"]),
        )
        conn.commit()
    return record


def _repin(store: Store, record_id: str, stamp: str) -> None:
    """Re-pin ONE record's updated_at (a heal's bump, made deterministic)."""
    with store._connect() as conn:
        conn.execute("UPDATE context_records SET updated_at = ? WHERE record_id = ?",
                     (stamp, record_id))
        conn.commit()


def _backdate(store: Store, *, hours: int = 1) -> str:
    """Push every landed record's stamps into the past, and return the new value.

    The stamps are minted at write and are not C2 fields, so a record landed just now
    always carries `now` — while a window's end is `now - delta`, which would sit BELOW
    its own start and refuse to open. Backdating lets PART 3 exercise the real routes
    against the real clock instead of injecting time everywhere.
    """
    stamp = _stamp(datetime.now(UTC) - timedelta(hours=hours))
    with store._connect() as conn:
        conn.execute("UPDATE context_records SET created_at = ?, updated_at = ?",
                     (stamp, stamp))
        conn.commit()
    return stamp


def _build(records, *, window_id="w20260724T040000Z", user_id="u1", home_tz="UTC", **kw):
    return build_daylog(records, window_id=window_id, user_id=user_id,
                        home_tz=home_tz, **kw)


def _texts(daylog) -> str:
    blocks = daylog.blocks if hasattr(daylog, "blocks") else daylog["blocks"]
    return " ".join(b.text if hasattr(b, "text") else b["text"] for b in blocks)


# =====================================================================================
# PART 1 — the CHANGED rules
# =====================================================================================

# --- change 1: membership is by updated_at (D27), not t_start ------------------------


def test_membership_is_by_updated_at_not_event_time(store):
    """THE watermark rule on D27's axis. A chunk captured Tuesday and uploaded Friday
    belongs to FRIDAY's window — and renders in a block anchored "On [Tuesday]". Late
    data cannot be lost because on a storage-clock watermark late data does not exist."""
    store.put_profile("u1", "UTC")
    tuesday = "2026-07-21T09:00:00Z"
    _land(store, _cap(tuesday, "a tuesday memory", chunk_id="chunk-tue"),
          "2026-07-24T02:00:00Z")
    window = store.open_training_window("u1", now=datetime(2026, 7, 24, 4, 0, 0, tzinfo=UTC))

    body = materialize_daylog(store, "u1", window["window_id"])

    # The window is FRIDAY's on the updated_at axis...
    assert body["t_start"] <= "2026-07-24T02:00:00Z" < body["t_end"]
    # ...and the record is in it, anchored to the TUESDAY it was actually lived.
    assert len(body["blocks"]) == 1
    assert body["blocks"][0]["anchors"]["date"] == "2026-07-21"
    assert body["blocks"][0]["text"].startswith("On 2026-07-21, ")
    # The rendered content starts DAYS BEFORE the window's own t_start — the late-upload
    # case working, not a bug. Compared as parsed INSTANTS (two spellings) and by the
    # actual gap ("days before" is the claim; `<` alone would pass for one second).
    lead = _instant(body["t_start"]) - _instant(body["segments"][0]["t_start"])
    assert lead > timedelta(days=1), lead


def test_a_record_updated_outside_the_window_is_excluded_even_if_its_event_time_is_inside(store):
    """The mirror image, and the one an event-time filter would get wrong: an event time
    inside the window's clock range buys nothing if the record's updated_at sits after
    `t_end`."""
    store.put_profile("u1", "UTC")
    _land(store, _asr("2026-07-24T02:30:00Z", "inside the window on both axes",
                      chunk_id="chunk-in"), "2026-07-24T02:00:00Z")
    window = store.open_training_window("u1", now=datetime(2026, 7, 24, 4, 0, 0, tzinfo=UTC))
    # Lands AFTER the window closed, but with an event time inside its range.
    _land(store, _asr("2026-07-24T02:31:00Z", "late arriving straggler",
                      chunk_id="chunk-late"), "2026-07-24T05:00:00Z")

    body = materialize_daylog(store, "u1", window["window_id"])
    rendered = " ".join(b["text"] for b in body["blocks"])
    assert "inside the window on both axes" in rendered
    assert "late arriving straggler" not in rendered


def test_build_daylog_has_no_event_time_filter_at_all():
    """The range query on the window axis already decided membership; re-filtering on
    the event axis would throw away exactly the backlog this design exists to train."""
    far_past = _iso(datetime(2019, 3, 4, 11, 0, 0, tzinfo=UTC).timestamp())
    far_future = _iso(datetime(2031, 3, 4, 11, 0, 0, tzinfo=UTC).timestamp())
    daylog = _build([_cap(far_past, "a very old memory", chunk_id="c-old"),
                     _cap(far_future, "a very new one", chunk_id="c-new")])
    assert len(daylog.blocks) == 2
    assert daylog.blocks[0].anchors["date"] == "2019-03-04"
    assert daylog.blocks[1].anchors["date"] == "2031-03-04"


# --- change 2: the global epoch grid -------------------------------------------------


def test_segment_buckets_sit_on_the_global_epoch_grid():
    """floor(t_start / segment_seconds), no window origin in the expression. Two moments
    1 s apart across an epoch multiple land in DIFFERENT buckets; two moments 9 s apart
    inside one land in the SAME bucket."""
    base = datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp()  # a multiple of 10
    daylog = _build([
        _cap(_iso(base + 9), "end of bucket zero", chunk_id="c1"),
        _cap(_iso(base + 10), "start of bucket one", chunk_id="c2"),
    ])
    assert len(daylog.segments) == 2
    assert daylog.segments[0].t_start == "2026-07-24T12:00:00+00:00"
    assert daylog.segments[1].t_start == "2026-07-24T12:00:10+00:00"

    same = _build([_cap(_iso(base + 1), "a", chunk_id="c1"),
                   _cap(_iso(base + 9), "b", chunk_id="c2")])
    assert len(same.segments) == 1
    assert same.segments[0].caption == ["a", "b"]


def test_the_grid_does_not_move_with_the_window():
    """A moment's bucket is the SAME whichever window collects it, so
    re-materialization is stable; window-relative indices would also go negative."""
    epoch = datetime(2026, 7, 21, 9, 0, 7, tzinfo=UTC).timestamp()
    rec = _cap(_iso(epoch), "one moment", chunk_id="c1")
    first = _build([rec], window_id="w20260724T040000Z")
    second = _build([rec], window_id="w20260731T040000Z")
    assert first.segments[0].t_start == second.segments[0].t_start == \
        "2026-07-21T09:00:00+00:00"
    assert first.segments[0].seg_id == "w20260724T040000Z_s00000"
    assert second.segments[0].seg_id == "w20260731T040000Z_s00000"


def test_seg_id_is_the_ordinal_not_the_epoch_bucket_index():
    """`{window_id}_s{ordinal:05d}` — the position in the sorted list, never the epoch
    index (a nine-digit number at 10 s buckets that would blow the fixed width)."""
    base = datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp()
    daylog = _build([_cap(_iso(base), "a", chunk_id="c1"),
                     _cap(_iso(base + 5000), "b", chunk_id="c2"),
                     _cap(_iso(base + 90000), "c", chunk_id="c3")])
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


# --- change 3: one record per chunk, latest updated_at wins, rowid tiebreak ----------


def test_one_record_per_chunk_latest_updated_at_wins():
    """The version-forward case (L8 case 2): a reprocess under a bumped dialect lands
    BESIDE the old record under a new record_id — same chunk, two rows. The day-log
    renders exactly the latest-updated one."""
    t = _iso(datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp())
    old = _cap(t, "a blurry doorway", chunk_id="chunk-A",
               pipeline_version="clipcap.v1-mock.v1")
    new = _cap(t, "a red front door", chunk_id="chunk-A",
               pipeline_version="clipcap.v2-mock.v1")
    assert old["record_id"] != new["record_id"]          # genuinely beside, not upsert
    rows = _rows((old, "2026-07-24T01:00:00Z"), (new, "2026-07-24T03:00:00Z"))

    kept = select_dialects(rows)
    assert [r["record"]["record_id"] for r in kept] == [new["record_id"]]
    daylog = _build([r["record"] for r in kept])
    assert daylog.blocks[0].text.endswith("Scene: a red front door")


def test_the_dedup_key_is_chunk_id_alone():
    """(chunk_id), nothing else (D28): two records of ONE chunk collapse whatever their
    slot sets or dialects; two records of DIFFERENT chunks both survive."""
    t = _iso(datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp())
    cap = _cap(t, "a red front door", chunk_id="chunk-A",
               pipeline_version="clipcap.v1-mock.v1")
    ocr = _ocr(t, "EXIT", chunk_id="chunk-A",
               pipeline_version="screentext.v9-mock.v1")   # different slots, same chunk
    other = _asr(t, "hello there", chunk_id="chunk-B")
    kept = select_dialects(_rows((cap, "2026-07-24T01:00:00Z"),
                                 (ocr, "2026-07-24T03:00:00Z"),
                                 (other, "2026-07-24T01:00:00Z")))
    assert sorted(dialect_key(r["record"]) for r in kept) == ["chunk-A", "chunk-B"]
    assert {r["record"]["record_id"] for r in kept} == \
        {ocr["record_id"], other["record_id"]}


def test_the_dedup_tiebreak_is_rowid_within_one_second():
    """updated_at is second-granularity, so a data-processing flush — or two
    consecutive heals — lands inside one value; rowid breaks the tie and is
    LOAD-BEARING (the D27 upsert keeps rowids stable for exactly this)."""
    t = _iso(datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp())
    first = _cap(t, "first of the flush", chunk_id="chunk-A",
                 pipeline_version="clipcap.v1-mock.v1")
    second = _cap(t, "second of the flush", chunk_id="chunk-A",
                  pipeline_version="clipcap.v2-mock.v1")
    same_second = "2026-07-24T03:00:00Z"
    kept = select_dialects(_rows((first, same_second), (second, same_second)))
    assert [r["record"]["record_id"] for r in kept] == [second["record_id"]]
    # ...and it does not depend on which order they were handed in.
    reordered = [
        {"seq": 2, "updated_at": same_second, "record": second},
        {"seq": 1, "updated_at": same_second, "record": first},
    ]
    assert [r["record"]["record_id"] for r in select_dialects(reordered)] == \
        [second["record_id"]]


def test_a_record_without_a_chunk_id_survives_on_its_own_key(store):
    """Schema-valid C2 v1 always carries source.chunk_id; the defensive posture stands:
    a defect upstream loses a GROUPING, never a record."""
    t = _iso(datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp())
    orphan = _cap(t, "an orphan", chunk_id="c1")
    del orphan["source"]["chunk_id"]
    normal = _cap(t, "a neighbour", chunk_id="c2")
    kept = select_dialects(_rows((orphan, "2026-07-24T01:00:00Z"),
                                 (normal, "2026-07-24T01:00:00Z")))
    assert len(kept) == 2
    assert dialect_key(orphan).startswith("\x00record_id:")


def test_a_version_forward_reprocess_renders_once_end_to_end(store):
    """The whole rule, through the store: land a chunk, version-forward it (a NEW
    record_id lands beside), and materialize. The day-log carries the new dialect
    exactly once."""
    store.put_profile("u1", "UTC")
    t = "2026-07-24T02:00:00Z"
    old = _cap(t, "a blurry doorway", chunk_id="chunk-A",
               pipeline_version="clipcap.v1-mock.v1")
    new = _cap(t, "a red front door", chunk_id="chunk-A",
               pipeline_version="clipcap.v2-mock.v1")
    _land(store, old, "2026-07-24T02:05:00Z")
    _land(store, new, "2026-07-24T02:06:00Z")
    window = store.open_training_window("u1", now=datetime(2026, 7, 24, 4, 0, 0, tzinfo=UTC))

    body = materialize_daylog(store, "u1", window["window_id"])
    rendered = " ".join(b["text"] for b in body["blocks"])
    assert rendered.count("a red front door") == 1
    assert "a blurry doorway" not in rendered


# =====================================================================================
# PART 2 — slot routing + PRESERVED behaviours
# =====================================================================================


def test_same_span_records_merge_into_one_segment():
    t = _iso(datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp())
    daylog = _build([_cap(t, "a red door", chunk_id="c1"),
                     _ocr(t, "EXIT", chunk_id="c2"),
                     _asr(t, "hello there", chunk_id="c3")])
    assert len([s for s in daylog.segments if not s.is_empty()]) == 1
    seg = daylog.segments[0]
    assert seg.caption == ["a red door"]
    assert seg.ocr == ["EXIT"]
    assert seg.asr[0] == {"spk": None, "text": "hello there", "t": t}


def test_transcript_splits_land_in_their_own_buckets_by_their_own_t_start():
    """A 30 s VAD chunk spans three buckets and its speech must be anchored where it
    was actually said, not where the chunk began. The parent asr text is NOT rendered
    on top of its aligned view — one witness, one voice."""
    base = datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp()
    rec = _tr(_iso(base), [
        {"t_start": _iso(base), "t_end": _iso(base + 2), "value": "first",
         "speaker": "speaker-0"},
        {"t_start": _iso(base + 25), "t_end": _iso(base + 27), "value": "second",
         "speaker": "speaker-1"},
    ], asr_text="first second", chunk_id="c1", t_end=_iso(base + 30))
    daylog = _build([rec])
    non_empty = [s for s in daylog.segments if not s.is_empty()]
    assert len(non_empty) == 2                      # bucket +0 and bucket +20
    assert non_empty[0].asr[0] == {"spk": "speaker-0", "text": "first", "t": _iso(base)}
    assert non_empty[1].asr[0]["spk"] == "speaker-1"
    assert "first second" not in [a["text"] for s in non_empty for a in s.asr]


def test_both_split_timestamp_spellings_bucket_correctly():
    """Split timestamps arrive in two spellings: the verbatim C1 root span (trailing Z)
    and the offset-derived `abs_time` form (+00:00, microseconds). Both must bucket by
    instant, not by string — normalizing either would move a record."""
    base = datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp()
    z_spelled = "2026-07-24T12:00:01Z"
    offset_spelled = "2026-07-24T12:00:11.250000+00:00"     # the +00:00-microsecond form
    rec = _tr(_iso(base), [
        {"t_start": z_spelled, "t_end": z_spelled, "value": "zulu line",
         "speaker": "speaker-0"},
        {"t_start": offset_spelled, "t_end": offset_spelled, "value": "offset line",
         "speaker": "speaker-0"},
    ], chunk_id="c1", t_end=_iso(base + 30))
    daylog = _build([rec])
    non_empty = [s for s in daylog.segments if not s.is_empty()]
    assert len(non_empty) == 2                      # buckets +0 and +10, by INSTANT
    assert non_empty[0].asr[0]["text"] == "zulu line"
    assert non_empty[1].asr[0]["text"] == "offset line"
    # The line's own `t` keeps the split's verbatim spelling — never respelled.
    assert non_empty[0].asr[0]["t"] == z_spelled
    assert non_empty[1].asr[0]["t"] == offset_spelled


def test_heard_lines_fall_back_to_asr_when_transcript_is_absent():
    """The ruled fallback (2026-08-06): transcript absent — a hole (incl. a permanent
    one) or a dialect that never attempted it — renders speech from slots.asr, spk
    null, each split by its OWN t_start."""
    base = datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp()
    rec = _asr(_iso(base), "hello over there", splits=[
        {"t_start": _iso(base), "t_end": _iso(base + 2), "value": "hello"},
        {"t_start": _iso(base + 25), "t_end": _iso(base + 27), "value": "over there"},
    ], chunk_id="c1", t_end=_iso(base + 30))
    daylog = _build([rec])
    non_empty = [s for s in daylog.segments if not s.is_empty()]
    assert len(non_empty) == 2
    assert non_empty[0].asr[0] == {"spk": None, "text": "hello", "t": _iso(base)}
    assert non_empty[1].asr[0] == {"spk": None, "text": "over there",
                                   "t": _iso(base + 25)}
    # The Heard line renders the unlabeled lines bare — no fabricated speaker (L11).
    assert "Heard: hello" in daylog.blocks[0].text


def test_the_asr_fallback_without_splits_renders_at_the_records_own_t_start():
    """The whole-chunk shape: an asr slot with no finer timing renders one line at the
    record's t_start."""
    t = _iso(datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp())
    daylog = _build([_asr(t, "a whole chunk of speech", chunk_id="c1")])
    assert daylog.segments[0].asr == [
        {"spk": None, "text": "a whole chunk of speech", "t": t}]


def test_no_fallback_when_the_transcript_slot_is_present_but_empty():
    """Present-with-empty-splits is an aligned-and-EMPTY claim, not a hole: the
    renderer must not second-guess a slot the record does carry (L11)."""
    t = _iso(datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp())
    rec = _tr(t, [], asr_text="speech the aligner judged empty", chunk_id="c1")
    daylog = _build([rec])
    assert daylog.segments == []


def test_an_empty_asr_value_is_honest_silence_not_a_line():
    """`asr.value: ""` is the VAD-gated silence claim; nothing renders."""
    t = _iso(datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp())
    assert _build([_asr(t, "", chunk_id="c1")]).segments == []


def test_ran_and_empty_ocr_contributes_no_line():
    """`ocr.value: ""` = OCR ran, the screen had no legible text. Distinguishable from
    a hole in the RECORD, deliberately not in the day-log (L11)."""
    t = _iso(datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp())
    ran_empty = _ocr(t, "", chunk_id="c1")
    holed = _cap(t, "a desk", chunk_id="c2")        # ocr never attempted / holed
    daylog = _build([ran_empty, holed])
    assert [s.ocr for s in daylog.segments if not s.is_empty()] == [[]]
    assert "World text" not in daylog.blocks[0].text


def test_an_empty_caption_value_renders_no_scene_line():
    t = _iso(datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp())
    assert _build([_cap(t, "   ", chunk_id="c1")]).segments == []


def test_an_empty_slots_map_renders_nothing_and_is_legal():
    """The all-optional-failure record (L7) ships and renders as pure absence."""
    t = _iso(datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp())
    record = _v1(t, {}, chunk_id="c1")
    assert schemas.validate_c2(record) == []
    assert _build([record]).segments == []


def test_holes_render_as_absence_never_as_fabrication():
    """L11: a dialect that attempted screentext but holed it renders NO World-text
    line — and nothing else invents one. The record, not the day-log, carries the
    hole-vs-empty distinction."""
    t = _iso(datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp())
    holed = _v1(t, {"caption": slot_caption("a desk by a window")}, chunk_id="c1",
                modality="video",
                pipeline_version="clipcap.v1-mock.v1+screentext.v1-mock.v1")
    block = _build([holed]).blocks[0]
    assert "Scene: a desk by a window" in block.text
    assert "World text" not in block.text and "Heard" not in block.text


def test_the_acoustic_slot_has_no_route():
    """The C10 v2 contract names no acoustic route; the stage's consumer marker stays
    honestly speculative. Routing it here would fabricate a consumer (L10/L11)."""
    t = _iso(datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp())
    rec = _v1(t, {"acoustic": slot_acoustic(["keyboard typing"], confidence=0.9)},
              chunk_id="c1")
    assert _build([rec]).segments == []


def test_the_anchor_line_and_labels_are_byte_exact():
    """The rendered block is what the model trains on, so its bytes are the interface —
    including the EN DASH between the clock readings and the label spellings (D20: the
    block text is contract, and the parity bar proves both renderers agree on it)."""
    base = datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp()
    block = _build([_cap(_iso(base), "a red door", chunk_id="c1"),
                    _ocr(_iso(base), "EXIT", chunk_id="c2"),
                    _asr(_iso(base), "hello there", chunk_id="c3")]).blocks[0]
    assert block.text == (
        "On 2026-07-24, around 12:00–12:00 local time:\n"
        "Scene: a red door\n"
        "Heard: hello there\n"
        "World text (OCR): EXIT"
    )
    assert block.anchors == {"date": "2026-07-24", "place": None}


def test_an_aligned_speaker_is_named_in_the_heard_line():
    base = datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp()
    rec = _tr(_iso(base), [
        {"t_start": _iso(base), "t_end": _iso(base + 1), "value": "hi",
         "speaker": "speaker-0"},
        {"t_start": _iso(base + 1), "t_end": _iso(base + 2), "value": "hey",
         "speaker": None},                          # alignment found no turn
    ], chunk_id="c1", t_end=_iso(base + 2))
    block = _build([rec]).blocks[0]
    assert "Heard: speaker-0: hi | hey" in block.text


def test_only_the_lines_with_content_appear():
    base = datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp()
    block = _build([_cap(_iso(base), "just a caption", chunk_id="c1")]).blocks[0]
    assert block.text == "On 2026-07-24, around 12:00–12:00 local time:\nScene: just a caption"
    assert "Heard:" not in block.text and "World text" not in block.text


def test_blocks_break_on_a_gap_of_more_than_six_segments():
    """max_gap_s = 6 * segment_seconds — a camera-off gap starts a new block."""
    base = datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp()
    together = _build([_cap(_iso(base), "a", chunk_id="c1"),
                       _cap(_iso(base + 50), "b", chunk_id="c2")])
    assert len(together.blocks) == 1
    apart = _build([_cap(_iso(base), "a", chunk_id="c1"),
                    _cap(_iso(base + 80), "b", chunk_id="c2")])
    assert len(apart.blocks) == 2
    assert [len(b.seg_ids) for b in apart.blocks] == [1, 1]


def test_a_block_holds_at_most_block_segments_segments():
    base = datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp()
    records = [_cap(_iso(base + 10 * i), f"caption {i}", chunk_id=f"c{i}")
               for i in range(30)]
    daylog = _build(records)
    assert len(daylog.segments) == 30
    assert [len(b.seg_ids) for b in daylog.blocks] == [12, 12, 6]
    cited = [sid for b in daylog.blocks for sid in b.seg_ids]
    assert cited == [s.seg_id for s in daylog.segments]


def test_quality_is_none_when_nothing_is_scored_and_the_min_over_what_is():
    """C2 v1 carries no quality field, so every segment is UNSCORED (null throughout
    v2 — the c10 v2 schema says so); unscored is not zero. When scores exist a block
    takes the min over the SCORED segments only."""
    from app.daylog import _render_block

    base = datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp()
    daylog = _build([_cap(_iso(base), "a", chunk_id="c1"),
                     _cap(_iso(base + 10), "b", chunk_id="c2"),
                     _cap(_iso(base + 20), "c", chunk_id="c3")])
    assert [s.quality for s in daylog.segments] == [None, None, None]
    assert len(daylog.blocks) == 1 and daylog.blocks[0].quality is None

    daylog.segments[0].quality = 0.9
    daylog.segments[1].quality = 0.4        # segments[2] stays unscored
    assert _render_block("w1", "UTC", 0, daylog.segments).quality == 0.4


# --- _block_zone: the D17 preference order, preserved exactly -------------------------


def test_the_anchor_line_uses_the_capturing_devices_zone():
    t = _iso(datetime(2026, 7, 24, 6, 0, 0, tzinfo=UTC).timestamp())
    block = _build([_cap(t, "a shrine", device_tz="Asia/Tokyo", chunk_id="c1")],
                   home_tz="UTC").blocks[0]
    assert "15:00" in block.text            # 06:00 UTC -> 15:00 Tokyo
    assert "06:00" not in block.text


def test_the_anchor_line_falls_back_to_home_tz_when_the_device_is_silent():
    t = _iso(datetime(2026, 7, 24, 6, 0, 0, tzinfo=UTC).timestamp())
    block = _build([_cap(t, "a shrine", chunk_id="c1")], home_tz="Asia/Tokyo").blocks[0]
    assert "15:00" in block.text


def test_an_unresolvable_device_zone_degrades_instead_of_killing_the_night():
    t = _iso(datetime(2026, 7, 24, 6, 0, 0, tzinfo=UTC).timestamp())
    block = _build([_cap(t, "a shrine", device_tz="Mars/Olympus_Mons", chunk_id="c1")],
                   home_tz="UTC").blocks[0]
    assert "06:00" in block.text


def test_the_first_contributing_record_stamps_the_bucket_zone():
    base = datetime(2026, 7, 24, 6, 0, 0, tzinfo=UTC).timestamp()
    daylog = _build([_cap(_iso(base), "a", device_tz="Asia/Tokyo", chunk_id="c1"),
                     _cap(_iso(base + 1), "b", device_tz="Europe/Paris", chunk_id="c2")])
    assert daylog.segments[0].tz == "Asia/Tokyo"


def test_travel_two_zones_in_one_window_render_with_different_local_dates():
    base = datetime(2026, 7, 24, 6, 0, 0, tzinfo=UTC).timestamp()
    boarding = _cap(_iso(base), "boarding", device_tz="America/Los_Angeles",
                    chunk_id="c1")
    landed = _cap(_iso(base + 10 * 3600), "landed", device_tz="Asia/Tokyo",
                  chunk_id="c2")
    blocks = _build([boarding, landed], home_tz="UTC").blocks
    assert len(blocks) == 2, [b.text for b in blocks]
    assert "23:00" in blocks[0].text            # 06:00 UTC -> 23:00 previous day in LA
    assert "01:00" in blocks[1].text            # 16:00 UTC -> 01:00 next day in Tokyo
    assert blocks[0].anchors["date"] == "2026-07-23"
    assert blocks[1].anchors["date"] == "2026-07-25"


def test_the_travel_case_survives_the_round_trip_through_http(client, store):
    """...end to end over the REAL wire — the v1 records POST through the v1-only gate,
    and the zone is resolved at materialization, not flattened by the cache."""
    store.put_profile("u1", "UTC")
    base = datetime(2026, 7, 24, 6, 0, 0, tzinfo=UTC).timestamp()
    for offset, text, tz, chunk in ((0, "boarding", "America/Los_Angeles", "c1"),
                                    (10 * 3600, "landed", "Asia/Tokyo", "c2")):
        record = _cap(_iso(base + offset), text, device_tz=tz, chunk_id=chunk)
        assert client.post("/context/records", json=record).status_code == 200
    _backdate(store)
    window = client.post("/training/windows", json={"user_id": "u1"}).json()

    body = client.get("/training/daylog",
                      params={"user_id": "u1", "window_id": window["window_id"]}).json()
    dates = [b["anchors"]["date"] for b in body["blocks"]]
    assert dates == ["2026-07-23", "2026-07-25"]
    assert body["home_tz"] == "UTC"
    assert [s["tz"] for s in body["segments"]] == ["America/Los_Angeles", "Asia/Tokyo"]


# =====================================================================================
# PART 3 — the HTTP surface: GET /training/daylog
# =====================================================================================


def _window_with_one_caption(client, store, user_id="u1", home_tz="UTC",
                             text="a red front door") -> dict:
    store.put_profile(user_id, home_tz)
    record = _cap(_iso(datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp()), text,
                  user_id=user_id, chunk_id="chunk-A")
    assert client.post("/context/records", json=record).status_code == 200
    _backdate(store)
    return client.post("/training/windows", json={"user_id": user_id}).json()


def _window_with_captions(client, store, offsets, *, user_id="u1", home_tz="UTC") -> dict:
    """A window over one caption per offset (seconds from 2026-07-24T12:00:00Z) — a
    multiple of both grids under test (10 s and 60 s)."""
    store.put_profile(user_id, home_tz)
    base = datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp()
    for i, offset in enumerate(offsets):
        record = _cap(_iso(base + offset), f"caption {i:02d}",
                      user_id=user_id, chunk_id=f"chunk-{i:02d}")
        assert client.post("/context/records", json=record).status_code == 200
    _backdate(store)
    return client.post("/training/windows", json={"user_id": user_id}).json()


def test_the_daylog_fetch_serves_a_valid_c10_v2_body(client, store):
    window = _window_with_one_caption(client, store)
    response = client.get("/training/daylog",
                          params={"user_id": "u1", "window_id": window["window_id"]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert schemas.validate_c10(body) == []          # the v2 schema is the gate now
    assert body["contract"] == "C10" and body["version"] == "2"
    assert body["user_id"] == "u1"
    assert body["window_id"] == window["window_id"]
    assert (body["t_start"], body["t_end"]) == (window["t_start"], window["t_end"])
    # The bumped stamps (D28): the slot-walk format and its forked recipe.
    assert body["daylog_format_version"] == DAYLOG_FORMAT_VERSION == "2"
    assert body["recipe_id"] == daylog_recipe_id() == "consolidation-v2.0"
    assert body["home_tz"] == "UTC"
    assert body["content_fingerprint"]
    assert body["blocks"][0]["text"].endswith("Scene: a red front door")


def test_the_bumped_recipe_renders_under_the_same_knobs_it_stamps(client, store):
    """consolidation-v2.0 forks v1.1 with byte-identical corpus knobs; the stamp and
    the render stay the same decision (10 s buckets, 12-segment blocks)."""
    window = _window_with_captions(client, store, [0, 20, 40])
    body = client.get("/training/daylog",
                      params={"user_id": "u1", "window_id": window["window_id"]}).json()
    assert body["recipe_id"] == "consolidation-v2.0"
    assert len(body["segments"]) == 3               # 10 s grid, not some new default
    assert recipe_segmentation("consolidation-v2.0") == (DEFAULT_SEGMENT_SECONDS,
                                                         DEFAULT_BLOCK_SEGMENTS)


def test_home_tz_on_the_body_is_the_fallback_zone_actually_used(client, store):
    window = _window_with_one_caption(client, store, home_tz="Asia/Tokyo")
    body = client.get("/training/daylog",
                      params={"user_id": "u1", "window_id": window["window_id"]}).json()
    assert body["home_tz"] == "Asia/Tokyo"
    assert body["blocks"][0]["text"].startswith("On 2026-07-24, around 21:00")


def test_an_empty_window_is_an_empty_daylog_not_an_error(client, store):
    store.put_profile("u1", "UTC")
    old = _cap("2026-07-01T12:00:00Z", "ancient", chunk_id="chunk-old")
    assert client.post("/context/records", json=old).status_code == 200
    with store._connect() as conn:
        conn.execute("UPDATE context_records SET created_at = '2026-07-01T12:00:00Z', "
                     "updated_at = '2026-07-01T12:00:00Z'")
        conn.commit()
    window = store.open_training_window("u1", now=datetime(2026, 7, 2, 0, 0, 0, tzinfo=UTC))
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
    assert cached[0]["daylog_format_version"] == "2"

    second = client.get("/training/daylog", params=params).json()
    assert second == first


def test_a_byte_different_reprocess_dissolves_out_of_the_rendered_window(store):
    """D27 + R2, end to end at the store level: a same-version reprocess that changes
    bytes BUMPS updated_at, so the record LEAVES the already-rendered window —
    put_context drops the affected caches (both windows), the re-materialized old
    window excludes the record (it dissolves out, D18), and the NEXT window picks it
    up. Trained bodies are history, not edited."""
    store.put_profile("u1", "UTC")
    t = "2026-07-24T02:00:00Z"
    first = _cap(t, "a blurry doorway", chunk_id="chunk-A")
    _land(store, first, "2026-07-24T02:00:00Z")
    window_a = store.open_training_window(
        "u1", now=datetime(2026, 7, 24, 4, 0, 0, tzinfo=UTC))
    before = materialize_daylog(store, "u1", window_a["window_id"])
    assert "a blurry doorway" in before["blocks"][0]["text"]

    fixed = _cap(t, "a red front door", chunk_id="chunk-A")
    assert fixed["record_id"] == first["record_id"]
    store.put_context(fixed)                      # byte-different -> updated_at bumps
    _repin(store, fixed["record_id"], "2026-07-24T05:00:00Z")

    again = materialize_daylog(store, "u1", window_a["window_id"])
    assert again["blocks"] == []                  # cache dropped AND dissolved out
    assert again["content_fingerprint"] != before["content_fingerprint"]

    store.close_training_window("u1", window_a["window_id"], "published")
    window_b = store.open_training_window(
        "u1", now=datetime(2026, 7, 24, 6, 1, 0, tzinfo=UTC))
    body = materialize_daylog(store, "u1", window_b["window_id"])
    rendered = " ".join(b["text"] for b in body["blocks"])
    assert "a red front door" in rendered
    assert "a blurry doorway" not in rendered


def test_a_corrected_home_tz_re_materializes_rather_than_serving_the_typo_forever(
        client, store):
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
    with store._connect() as conn:
        rows = conn.execute("SELECT home_tz FROM day_logs").fetchall()
    assert [r["home_tz"] for r in rows] == ["America/New_York"]


def test_a_cached_daylog_still_serves_after_the_profile_disappears(client, store):
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
    monkeypatch.setenv("STORAGE_DAYLOG_RECIPE_ID", "consolidation-test-1min-v1.0")
    window = _window_with_one_caption(client, store)
    params = {"user_id": "u1", "window_id": window["window_id"]}
    body = client.get("/training/daylog", params=params).json()
    assert body["recipe_id"] == "consolidation-test-1min-v1.0"
    # ...and the stamp is a fact about the RENDER: this recipe declares 60 s buckets.
    assert body["segments"][0]["t_end"] == "2026-07-24T12:01:00+00:00"
    other = client.get("/training/daylog",
                       params={**params, "recipe_id": "something-else"}).json()
    assert other["recipe_id"] == "consolidation-test-1min-v1.0"


def test_the_segment_grid_comes_from_the_stamped_recipes_corpus_knobs(
        client, store, monkeypatch):
    monkeypatch.setenv("STORAGE_DAYLOG_RECIPE_ID", "consolidation-test-1min-v1.0")
    window = _window_with_captions(client, store, [0, 20, 40])
    params = {"user_id": "u1", "window_id": window["window_id"]}

    body = client.get("/training/daylog", params=params).json()
    assert body["recipe_id"] == "consolidation-test-1min-v1.0"
    assert len(body["segments"]) == 1
    assert (body["segments"][0]["t_start"], body["segments"][0]["t_end"]) == (
        "2026-07-24T12:00:00+00:00", "2026-07-24T12:01:00+00:00")
    assert body["segments"][0]["caption"] == ["caption 00", "caption 01", "caption 02"]

    # The control, over the SAME records: the default (v2.0) recipe's 10 s grid.
    monkeypatch.delenv("STORAGE_DAYLOG_RECIPE_ID")
    default = client.get("/training/daylog", params=params).json()
    assert default["recipe_id"] == daylog_recipe_id()
    assert len(default["segments"]) == 3
    assert default["segments"][0]["t_end"] == "2026-07-24T12:00:10+00:00"


def test_the_block_cap_comes_from_the_stamped_recipes_corpus_knobs(
        client, store, monkeypatch):
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
    monkeypatch.setenv("STORAGE_DAYLOG_RECIPE_ID", "no-such-recipe-v9.9")
    window = _window_with_one_caption(client, store)
    params = {"user_id": "u1", "window_id": window["window_id"]}

    response = client.get("/training/daylog", params=params)
    assert response.status_code == 500, response.text
    detail = response.json()["detail"]
    assert detail["error"] == "day-log recipe cannot be resolved"
    assert detail["recipe_id"] == "no-such-recipe-v9.9"

    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM day_logs").fetchone()["n"] == 0
    monkeypatch.setenv("STORAGE_DAYLOG_RECIPE_ID", "consolidation-v2.0")
    assert client.get("/training/daylog", params=params).status_code == 200


def test_a_recipe_without_usable_corpus_knobs_refuses(client, store, monkeypatch, tmp_path):
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

    monkeypatch.delenv("STORAGE_RECIPES_DIR")
    assert recipe_segmentation("consolidation-v1.0") == (DEFAULT_SEGMENT_SECONDS,
                                                         DEFAULT_BLOCK_SEGMENTS)


def test_the_fingerprint_is_stable_across_renders_and_moves_with_content(client, store):
    window = _window_with_one_caption(client, store)
    params = {"user_id": "u1", "window_id": window["window_id"]}
    body = client.get("/training/daylog", params=params).json()
    from app.daylog import Block, DayLog, Segment
    rebuilt = DayLog(
        window_id=body["window_id"], user_id=body["user_id"], home_tz=body["home_tz"],
        segments=[Segment(**s) for s in body["segments"]],
        blocks=[Block(**b) for b in body["blocks"]],
    )
    assert daylog_fingerprint(rebuilt) == body["content_fingerprint"]


def test_a_user_without_a_profile_is_refused_loudly(client, store):
    record = _cap(_iso(datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC).timestamp()), "hi",
                  user_id="user-1")
    assert client.post("/context/records", json=record).status_code == 200
    _backdate(store)
    window = client.post("/training/windows", json={"user_id": "user-1"}).json()

    response = client.get("/training/daylog",
                          params={"user_id": "user-1", "window_id": window["window_id"]})
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "no profile"
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
    """The migration idiom: new TABLES and INDEXES ride in _SCHEMA (after the D27
    pre-step renames the axis columns); only new COLUMNS need a _MIGRATIONS entry."""
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
    assert "idx_context_user_updated" in indexes


# =====================================================================================
# PART 4 — the heal×window matrix: all THREE shapes
# =====================================================================================
#
# The DP-side inputs are pinned by DP's own `test_heal_seam.py`: a still-holey heal
# re-POSTs BYTE-IDENTICAL wire bytes; a filling heal changes bytes; and a heal can move
# a hole between slots (byte-different but NOT fuller — the shape a literal
# "fuller-wins" reading would miss). These tests replay exactly those shapes against
# the D27 byte-compare and the v2 renderer, deterministically (injected ledger clock;
# the heal's real-clock bump re-pinned into a known window range).

_HEAL_PV = "asr.v1-mock.v1+diarize.v1-mock.v1+speaker_align.v1-mock.v1"


def _open_window_a(store) -> dict:
    return store.open_training_window(
        "u1", now=datetime(2026, 7, 24, 4, 0, 0, tzinfo=UTC))


def _next_window(store, window_a) -> dict:
    store.close_training_window("u1", window_a["window_id"], "published")
    return store.open_training_window(
        "u1", now=datetime(2026, 7, 24, 6, 1, 0, tzinfo=UTC))


def test_heal_shape_1_a_filling_heal_re_windows_and_renders_the_new_truth(store):
    """GREEN HEAL FILLS A HOLE: byte-different → updated_at bumps → the record leaves
    the rendered window and the NEXT window renders the healed (speaker-aligned)
    truth. Until the heal, the ruled asr fallback carried the speech unlabeled."""
    store.put_profile("u1", "UTC")
    t = "2026-07-24T02:00:00Z"
    holey = _v1(t, {"asr": slot_asr("hello there")}, chunk_id="chunk-A",
                pipeline_version=_HEAL_PV)           # transcript attempted, holed
    _land(store, holey, "2026-07-24T02:00:00Z")
    window_a = _open_window_a(store)
    before = materialize_daylog(store, "u1", window_a["window_id"])
    assert "Heard: hello there" in before["blocks"][0]["text"]   # fallback, spk null

    healed = _v1(t, {"asr": slot_asr("hello there"),
                     "transcript": slot_transcript([
                         {"t_start": t, "t_end": t, "value": "hello there",
                          "speaker": "speaker-0"}])},
                 chunk_id="chunk-A", pipeline_version=_HEAL_PV)
    assert healed["record_id"] == holey["record_id"]  # same identity: an upsert, L3
    store.put_context(healed)                         # byte-different -> bump
    _repin(store, healed["record_id"], "2026-07-24T05:00:00Z")

    dissolved = materialize_daylog(store, "u1", window_a["window_id"])
    assert dissolved["blocks"] == []                  # window A: dissolved out

    window_b = _next_window(store, window_a)
    body = materialize_daylog(store, "u1", window_b["window_id"])
    assert "Heard: speaker-0: hello there" in body["blocks"][0]["text"]


def test_heal_shape_2_a_byte_identical_still_holey_repost_does_not_re_window(store):
    """STILL-HOLEY RE-POST: DP re-POSTs byte-identical bytes, the D27 upsert touches
    nothing, the cached day-log SURVIVES and the record stays
    in its window. No re-render, no fingerprint movement, no cache loss."""
    store.put_profile("u1", "UTC")
    t = "2026-07-24T02:00:00Z"
    holey = _v1(t, {"asr": slot_asr("hello there")}, chunk_id="chunk-A",
                pipeline_version=_HEAL_PV)
    _land(store, holey, "2026-07-24T02:00:00Z")
    window_a = _open_window_a(store)
    before = materialize_daylog(store, "u1", window_a["window_id"])

    store.put_context(holey)                          # the still-holey heal re-POST
    with store._connect() as conn:
        row = conn.execute(
            "SELECT updated_at FROM context_records WHERE record_id = ?",
            (holey["record_id"],)).fetchone()
        cached = conn.execute("SELECT COUNT(*) AS n FROM day_logs").fetchone()["n"]
    assert row["updated_at"] == "2026-07-24T02:00:00Z"   # unmoved
    assert cached == 1                                    # cache NOT invalidated

    again = materialize_daylog(store, "u1", window_a["window_id"])
    assert again == before                                # served, byte-for-byte


def test_heal_shape_3_byte_different_but_not_fuller_re_windows_and_shows_the_new_truth(store):
    """THE SHAPE A LITERAL READING WOULD MISS: the hole MIGRATES between slots — the
    heal's re-run filled ocr while the captioner was out, so caption regressed. Not
    fuller, but byte-different: it bumps, re-windows, and the renderer shows the new
    truth (R3: blind replace; the ledger, not the record, carries hole truth)."""
    store.put_profile("u1", "UTC")
    t = "2026-07-24T02:00:00Z"
    pv = "clipcap.v1-mock.v1+clipprep.v1-mock.v1+screentext.v1-mock.v1"
    first = _v1(t, {"caption": slot_caption("a desk by a window")},
                chunk_id="chunk-A", modality="video", pipeline_version=pv)
    _land(store, first, "2026-07-24T02:00:00Z")
    window_a = _open_window_a(store)
    before = materialize_daylog(store, "u1", window_a["window_id"])
    assert "Scene: a desk by a window" in before["blocks"][0]["text"]
    assert "World text" not in before["blocks"][0]["text"]

    migrated = _v1(t, {"ocr": slot_ocr("EXIT")},
                   chunk_id="chunk-A", modality="video", pipeline_version=pv)
    assert migrated["record_id"] == first["record_id"]
    store.put_context(migrated)                       # byte-different, NOT fuller
    _repin(store, migrated["record_id"], "2026-07-24T05:00:00Z")

    dissolved = materialize_daylog(store, "u1", window_a["window_id"])
    assert dissolved["blocks"] == []

    window_b = _next_window(store, window_a)
    body = materialize_daylog(store, "u1", window_b["window_id"])
    rendered = " ".join(b["text"] for b in body["blocks"])
    assert "World text (OCR): EXIT" in rendered
    assert "a desk by a window" not in rendered       # the regression rendered honestly
