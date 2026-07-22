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
