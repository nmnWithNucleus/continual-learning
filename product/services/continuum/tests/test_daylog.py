import json
from datetime import date

from app.daylog import build_daylog, corpus_blocks
from app.renderer import render_daylog_files
from app.synth import synth_records
from app.window import window_for


def _win():
    return window_for("u-test", date(2026, 7, 20), "UTC")


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
    assert len(non_empty) == 2  # bucket 0 and bucket 2 (25s -> index 2)


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
    win = window_for("u-test", date(2026, 7, 20), "UTC")
    # 06:00 UTC on the window's day -> 15:00 in Tokyo.
    rec = _tz_record(win, "r1", 2 * 3600, "a shrine", device_tz="Asia/Tokyo")
    blocks = build_daylog([rec], win).blocks
    assert len(blocks) == 1
    assert "15:00" in blocks[0].text, blocks[0].text
    assert "06:00" not in blocks[0].text  # the UTC reading must NOT appear


def test_anchor_line_falls_back_to_window_tz_when_device_is_silent():
    """Pre-D17 records (and devices that can't report a zone) carry no
    device_tz; they must render in the user's profile home_tz, not crash."""
    win = window_for("u-test", date(2026, 7, 20), "Asia/Tokyo")
    rec = _tz_record(win, "r1", 2 * 3600, "a shrine")  # no device_tz
    blocks = build_daylog([rec], win).blocks
    # Window starts 04:00 Tokyo; +2 h -> 06:00 Tokyo.
    assert "06:00" in blocks[0].text, blocks[0].text
    assert blocks[0].seg_ids


def test_unresolvable_device_tz_degrades_instead_of_killing_the_night():
    """A garbage zone id from one device must never sink a whole training run."""
    win = window_for("u-test", date(2026, 7, 20), "UTC")
    rec = _tz_record(win, "r1", 2 * 3600, "a shrine", device_tz="Mars/Olympus_Mons")
    blocks = build_daylog([rec], win).blocks
    assert "06:00" in blocks[0].text  # fell back to the window's UTC


def test_travel_two_zones_in_one_window_render_independently():
    """Two blocks captured in different zones each render in their OWN zone —
    the case a single per-window tz structurally cannot express."""
    win = window_for("u-test", date(2026, 7, 20), "UTC")
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
