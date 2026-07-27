import json

from app.daylog import build_daylog, corpus_blocks
from app.renderer import render_daylog_files
from app.synth import synth_records
from tests._helpers import make_window


def _win():
    return make_window("u-test", 20, "UTC")


def test_records_join_into_segments_and_blocks():
    win = _win()
    daylog = build_daylog(synth_records(win, seed=7, events=30), win)
    assert daylog.segments and daylog.blocks
    # Out-of-window stragglers never appear.
    all_text = " ".join(b.text for b in daylog.blocks)
    assert "OUT-OF-WINDOW" not in all_text
    # Blocks group at most block_segments segments and reference real seg_ids.
    seg_ids = {s.seg_id for s in daylog.segments}
    for blk in daylog.blocks:
        assert 1 <= len(blk.seg_ids) <= 12
        assert set(blk.seg_ids) <= seg_ids


def test_same_span_records_merge_into_one_segment():
    win = _win()
    base = {"contract": "C2", "version": "0", "user_id": "u-test",
            "enrichments": {}, "pipeline_version": "t", "processed_at": "x",
            "source": {"modality": "video"}}
    t0 = win.start_utc.isoformat()
    t1 = win.start_utc.replace(microsecond=1000).isoformat()
    records = [
        {**base, "record_id": "r1", "t_start": t0, "t_end": t1,
         "content": {"kind": "caption", "text": "a red door"}},
        {**base, "record_id": "r2", "t_start": t0, "t_end": t1,
         "content": {"kind": "ocr", "text": "EXIT"}},
        {**base, "record_id": "r3", "t_start": t0, "t_end": t1,
         "content": {"kind": "transcript", "text": "hello there"}},
    ]
    daylog = build_daylog(records, win)
    assert len([s for s in daylog.segments if not s.is_empty()]) == 1
    seg = daylog.segments[0]
    assert seg.caption == ["a red door"]
    assert seg.ocr == ["EXIT"]
    assert seg.asr[0]["text"] == "hello there"


def test_diarized_subspans_land_in_own_buckets():
    win = _win()
    t0 = win.start_utc
    sub1 = {"t_start": t0.isoformat(), "t_end": t0.isoformat(), "text": "first", "speaker": "a"}
    t25 = t0.timestamp() + 25
    from datetime import datetime, timezone
    sub2_t = datetime.fromtimestamp(t25, tz=timezone.utc)
    sub2 = {"t_start": sub2_t.isoformat(), "t_end": sub2_t.isoformat(),
            "text": "second", "speaker": "b"}
    rec = {"contract": "C2", "version": "0", "record_id": "r", "user_id": "u-test",
           "source": {"modality": "audio"}, "t_start": t0.isoformat(),
           "t_end": sub2["t_end"], "enrichments": {}, "pipeline_version": "t",
           "processed_at": "x",
           "content": {"kind": "transcript", "text": "first second",
                       "segments": [sub1, sub2]}}
    daylog = build_daylog([rec], win)
    non_empty = [s for s in daylog.segments if not s.is_empty()]
    # Two buckets, 25 s apart on the GLOBAL 10 s grid -> two indices apart, so the
    # sub-spans cannot share one. (The grid became global in F4; what this test pins is
    # the SEPARATION, which holds under either grid.)
    assert len(non_empty) == 2


def test_renderer_writes_daylog_files(tmp_path):
    win = _win()
    daylog = build_daylog(synth_records(win, seed=7, events=10), win)
    paths = render_daylog_files(daylog, tmp_path)
    seg_rows = [json.loads(l) for l in open(paths["segments"])]
    blk_rows = [json.loads(l) for l in open(paths["blocks"])]
    assert {"seg_id", "t_start", "t_end", "caption", "asr", "ocr", "quality"} \
        <= set(seg_rows[0])
    assert {"block_id", "seg_ids", "text", "anchors", "quality"} <= set(blk_rows[0])
    # Anchors are IN the text, never metadata-only.
    assert blk_rows[0]["text"].startswith("On 2026-07-20")


def test_quality_gate_excludes_scored_low_blocks_keeps_unscored():
    win = _win()
    daylog = build_daylog(synth_records(win, seed=7, events=10), win)
    daylog.blocks[0].quality = 0.2   # scored bad -> excluded from amplification
    daylog.blocks[1].quality = 0.9   # scored good -> kept
    eligible = corpus_blocks(daylog, quality_min=0.5)
    ids = {b.block_id for b in eligible}
    assert daylog.blocks[0].block_id not in ids
    assert daylog.blocks[1].block_id in ids
    # Unscored (None) blocks pass — C2 v0 has no quality field yet.
    assert all(b.block_id in ids for b in daylog.blocks[2:])


# --- D17: per-record device timezone drives the rendered anchor line ----------

def _tz_record(win, record_id, offset_s, text, device_tz=None):
    """One caption record `offset_s` into the window, optionally device-stamped."""
    from datetime import datetime, timedelta, timezone as _tz
    t = win.start_utc + timedelta(seconds=offset_s)
    source = {"modality": "video", "device_id": "d1", "stream_id": "s1",
              "chunk_id": f"c-{record_id}", "blob_ref": "b1"}
    if device_tz:
        source["device_tz"] = device_tz
    return {"contract": "C2", "version": "0", "record_id": record_id,
            "user_id": win.user_id, "enrichments": {}, "pipeline_version": "t",
            "processed_at": "x", "source": source,
            "t_start": t.isoformat(),
            "t_end": (t + timedelta(seconds=1)).isoformat(),
            "content": {"kind": "caption", "text": text}}


def test_anchor_line_uses_the_devices_reported_timezone():
    """The whole point of D17: a block renders in the zone the CAPTURING DEVICE
    reported, not the window's fallback — so a wearer who flew east yesterday
    still gets an honest 'local time' line."""
    # Window fallback is UTC; the device says Tokyo (UTC+9).
    win = make_window("u-test", 20, "UTC")
    # 06:00 UTC on the window's day -> 15:00 in Tokyo.
    rec = _tz_record(win, "r1", 2 * 3600, "a shrine", device_tz="Asia/Tokyo")
    blocks = build_daylog([rec], win).blocks
    assert len(blocks) == 1
    assert "15:00" in blocks[0].text, blocks[0].text
    assert "06:00" not in blocks[0].text  # the UTC reading must NOT appear


def test_anchor_line_falls_back_to_window_tz_when_device_is_silent():
    """Pre-D17 records (and devices that can't report a zone) carry no
    device_tz; they must render in the user's profile home_tz, not crash."""
    win = make_window("u-test", 20, "Asia/Tokyo")
    rec = _tz_record(win, "r1", 2 * 3600, "a shrine")  # no device_tz
    blocks = build_daylog([rec], win).blocks
    # Window starts 04:00 Tokyo; +2 h -> 06:00 Tokyo.
    assert "06:00" in blocks[0].text, blocks[0].text
    assert blocks[0].seg_ids


def test_unresolvable_device_tz_degrades_instead_of_killing_the_night():
    """A garbage zone id from one device must never sink a whole training run."""
    win = make_window("u-test", 20, "UTC")
    rec = _tz_record(win, "r1", 2 * 3600, "a shrine", device_tz="Mars/Olympus_Mons")
    blocks = build_daylog([rec], win).blocks
    assert "06:00" in blocks[0].text  # fell back to the window's UTC


def test_travel_two_zones_in_one_window_render_independently():
    """Two blocks captured in different zones each render in their OWN zone —
    the case a single per-window tz structurally cannot express."""
    win = make_window("u-test", 20, "UTC")
    early = _tz_record(win, "r1", 2 * 3600, "boarding", device_tz="America/Los_Angeles")
    # +10 h, far past the 60 s gap rule, so it lands in its own block.
    later = _tz_record(win, "r2", 12 * 3600, "landed", device_tz="Asia/Tokyo")
    blocks = build_daylog([early, later], win).blocks
    assert len(blocks) == 2, [b.text for b in blocks]
    # 06:00 UTC -> 23:00 previous day in LA; 16:00 UTC -> 01:00 next day in Tokyo.
    assert "23:00" in blocks[0].text, blocks[0].text
    assert "01:00" in blocks[1].text, blocks[1].text
    # ...and the DATES differ too, which is the failure a wrong tz hides.
    assert blocks[0].anchors["date"] != blocks[1].anchors["date"]


# --- F4: the segment grid is GLOBAL, so the window ORIGIN changes nothing ------
#
# This module's `make_window` helper always produces an origin on a whole local
# minute, which is on the 10 s segment grid. Every test above therefore ran against
# an ALIGNED origin — and so did storage's M9 differential proof, which is how a
# window-relative bucket grid survived here for as long as it did. Real windows are
# `[watermark, now−δ)` at SECOND granularity (storage's `open_training_window`), so
# nine origins in ten are off the grid. These two tests are the continuum-side pin:
# without them the only thing that catches a regression is storage's suite.


def _daylog_rows(daylog):
    """The day-log as plain comparable rows — everything a consumer can see."""
    from dataclasses import asdict
    return ([asdict(s) for s in daylog.segments], [asdict(b) for b in daylog.blocks])


def _shifted(win, seconds: int):
    """The same window span, moved `seconds` off the segment grid."""
    from dataclasses import replace
    from datetime import timedelta
    return replace(win,
                   start_utc=win.start_utc + timedelta(seconds=seconds),
                   end_utc=win.end_utc + timedelta(seconds=seconds))


def test_segment_bounds_sit_on_the_global_epoch_grid():
    """Every bucket boundary is a multiple of `segment_seconds` since the epoch.

    This is the property, stated without reference to any window: storage buckets on
    `floor(t / segment_seconds)` (D18 rule 2, because a window-relative index goes
    NEGATIVE on the ingest axis) and this renderer is the parity reference measured
    against it. It is checked on a MISALIGNED window precisely because an aligned one
    cannot tell the two rules apart.
    """
    from datetime import datetime
    win = _shifted(make_window("u-test", 20, "UTC"), 3)
    daylog = build_daylog(synth_records(win, seed=7, events=30), win)
    assert daylog.segments
    for seg in daylog.segments:
        for bound in (seg.t_start, seg.t_end):
            epoch = datetime.fromisoformat(bound).timestamp()
            assert epoch % 10 == 0, (bound, epoch % 10, seg.seg_id)


def test_the_render_does_not_depend_on_the_window_origin():
    """The same records render to the SAME segments and blocks whatever the origin.

    Under the old window-relative grid this failed: an origin 3 s off the grid merged
    records 3 s / 5 s / 12 s into one bucket where the global grid puts them in two,
    which changed the segment count, the per-block membership AND the block text —
    every anchor line moved by a minute ("around 10:59–11:00" against "around
    11:00–11:01"). Different block text is different TRAINING text, silently.

    The records sit an hour into the window so that shifting the bounds by a few
    seconds cannot change MEMBERSHIP; membership is the event-time filter's job (D18
    rule 1) and this test is about the grid, not about it.
    """
    aligned = make_window("u-test", 20, "UTC")
    records = [_tz_record(aligned, "r1", 3600 + 3, "a red door"),
               _tz_record(aligned, "r2", 3600 + 5, "a brass handle"),
               _tz_record(aligned, "r3", 3600 + 12, "a hallway"),
               _tz_record(aligned, "r4", 7200, "a window")]
    baseline = _daylog_rows(build_daylog(records, aligned))
    segments, blocks = baseline
    assert segments and blocks           # anti-vacuity: empty lists compare equal
    for offset in (1, 3, 7, 9):
        win = _shifted(aligned, offset)
        assert _daylog_rows(build_daylog(records, win)) == baseline, (
            f"the render changed when the window origin moved {offset}s off the "
            "segment grid — the bucket grid is window-relative again")


def test_seg_id_is_the_ordinal_in_the_day_log():
    """`seg_id` is `{window_id}_s{ordinal:05d}`, numbered from 0 with no gaps.

    Not the bucket index: on the global grid that is a nine-digit epoch number, which
    voids the fixed `{:05d}` width. Storage labels segments by the same rule for the
    same reason, so this also keeps the M9 tier-B relabelling trivial rather than
    merely provable. Nothing PARSES a seg_id — the only reader is a histogram's
    `len(b.seg_ids)` — so the label is a representation choice; this test pins which
    choice was made.
    """
    win = make_window("u-test", 20, "UTC")
    daylog = build_daylog(synth_records(win, seed=7, events=30), win)
    assert [s.seg_id for s in daylog.segments] == [
        f"{win.window_id}_s{i:05d}" for i in range(len(daylog.segments))]


def test_the_synthetic_day_lands_inside_whatever_window_it_is_given():
    """REGRESSION (F5). The synthetic day used to assume a 24 h local-day window.

    It hard-coded a 4 h lead-in before the first event — correct for
    `window_for(date, tz)`, which D18 deleted. Storage's windows are
    `[last_trained_t, now-delta)` on the INGEST clock and are routinely MINUTES long,
    so every event landed past `t_end`, `build_daylog`'s event-time filter dropped all
    of them, and `nightly --synthetic` reported `skipped_no_data` and exit 0: a night
    that trained on nothing and called it success. MEASURED against real storage
    before the fix — a 66 s first window produced 0 segments and 0 blocks.

    The property, stated for any window storage can mint rather than for the one
    length that used to work.
    """
    from datetime import timedelta

    from app.window import Window

    base = make_window("u-synth", 20, "UTC")
    for span_s in (66, 15 * 60, 4 * 3600, 24 * 3600, 47 * 3600):
        win = Window(window_id=base.window_id, user_id=base.user_id, tz=base.tz,
                     start_utc=base.end_utc - timedelta(seconds=span_s),
                     end_utc=base.end_utc)
        daylog = build_daylog(synth_records(win, seed=7, events=30), win)
        assert daylog.segments, f"no segments for a {span_s}s window"
        assert daylog.blocks, f"no blocks for a {span_s}s window"
        text = " ".join(b.text for b in daylog.blocks)
        # The deliberate stragglers stay out: the fix widens nothing.
        assert "OUT-OF-WINDOW" not in text


def test_the_synthetic_day_is_writable_to_the_c2_store():
    """REGRESSION (F5). These records claim `contract: C2`, and the demo now seeds
    them into storage — so they must satisfy the two things the C2 store enforces
    that an in-process day-log never notices: `blob_ref` is required non-empty
    (`POST /context/records` 422'd on the empty string every one of them carried),
    and ids are the store's primary key (`put_context` UPSERTS on `record_id`, and
    the day-log materializer keeps one record per `(chunk_id, kind, discriminator)`,
    so ids that collided on a short window silently collapsed the seed to a handful
    of rows).
    """
    win = make_window("u-synth", 20, "UTC")
    records = synth_records(win, seed=7, events=30)
    assert all(r["source"]["blob_ref"] for r in records)
    ids = [r["record_id"] for r in records]
    chunks = [r["source"]["chunk_id"] for r in records]
    assert len(set(ids)) == len(ids)
    assert len(set(chunks)) == len(chunks)
