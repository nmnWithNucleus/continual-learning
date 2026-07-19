"""Real VIDEO keyframe pipeline — extraction, per-keyframe timing, OCR, backends.

Hermetic + no GPU: the DEFAULT mock captioner captions real keyframes extracted
from a committed multi-scene fixture (via ffmpeg), storage is faked via the
shared conftest ``client``, and the ``vlm`` backend is exercised against a fake
OpenAI-compatible VL server (httpx MockTransport) so its wire format + OCR weave
are covered with no model.

These tests are ADD-ONLY: they use a NEW fixture (``video_scenes.*``) and never
touch the existing seam fixture/test — those 38 stay green as the byte-identical
proof of the additive timing hook.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest

from app import schemas
from app.pipeline import build_c2, compute_record_id
from app.processing.base import ProcessedContent, ProcessedUnit
from app.processing.processors import video as video_proc
from app.timeutil import parse_rfc3339
from app.vision import frames as vframes
from app.vision.config import get_vision_settings
from app.vision.result import Keyframe

_FX = Path(__file__).resolve().parent / "fixtures"


def _load_video():
    c1 = json.loads((_FX / "video_scenes.c1.json").read_text())
    blob = (_FX / "video_scenes.mp4").read_bytes()
    return c1, blob


def _post_video(client, c1, blob):
    client.fake_storage.add_blob(c1["blob_ref"], blob)
    return client.post("/ingest", json=c1)


requires_decoder = pytest.mark.skipif(
    not vframes.ffmpeg_available(),
    reason="needs ffmpeg (the single canonical decoder) to decode the real video fixture",
)


# ---- The new fixture is itself valid C1 --------------------------------------

def test_video_scenes_fixture_is_valid_c1():
    c1, blob = _load_video()
    assert schemas.validate_c1(c1) == []
    import hashlib
    assert hashlib.sha256(blob).hexdigest() == c1["blob_sha256"]
    assert len(blob) == c1["blob_bytes"]


# ---- The additive timing hook (the ONE sanctioned core edit) -----------------

def test_build_c2_defaults_to_chunk_span_when_unit_has_no_subspan():
    """Byte-identical guarantee: a unit that sets no sub-span carries the C1 span."""
    c1, _ = _load_video()
    unit = ProcessedUnit(content=ProcessedContent(kind="caption", text="x"))
    c2 = build_c2(c1, unit, "pv", "2026-07-19T10:00:09Z")
    assert c2["t_start"] == c1["t_start"]
    assert c2["t_end"] == c1["t_end"]


def test_build_c2_honors_unit_subspan():
    """A unit that knows its own timing overrides the chunk span (OQ14a)."""
    c1, _ = _load_video()
    unit = ProcessedUnit(
        content=ProcessedContent(kind="caption", text="x"),
        t_start="2026-07-19T10:00:02Z",
        t_end="2026-07-19T10:00:04Z",
    )
    c2 = build_c2(c1, unit, "pv", "2026-07-19T10:00:09Z")
    assert c2["t_start"] == "2026-07-19T10:00:02Z"
    assert c2["t_end"] == "2026-07-19T10:00:04Z"
    assert schemas.validate_c2(c2) == []  # still C2-valid; no schema change


# ---- Frame extraction: deterministic, partitions the span --------------------

@requires_decoder
def test_extract_keyframes_deterministic_and_partitions():
    c1, blob = _load_video()
    vs = get_vision_settings()
    span = (parse_rfc3339(c1["t_end"]) - parse_rfc3339(c1["t_start"])).total_seconds()
    kfs = vframes.extract_keyframes(blob, c1["codec"], span, vs)

    assert len(kfs) >= 2, "a multi-scene clip should yield several keyframes"
    # strictly increasing, contiguous, covering [0, span]
    assert kfs[0].t_offset_s == 0.0
    assert abs(kfs[-1].t_end_offset_s - span) < 1e-6
    for a, b in zip(kfs, kfs[1:]):
        assert a.t_offset_s < b.t_offset_s
        assert a.t_end_offset_s == b.t_offset_s
    for kf in kfs:
        assert kf.image_jpeg and not kf.synthetic
        assert 0.0 <= kf.t_offset_s < kf.t_end_offset_s <= span

    # Deterministic: same bytes -> same offsets + same JPEG bytes (idempotency).
    import hashlib
    def sig(ks):
        return [(k.index, round(k.t_offset_s, 3), round(k.t_end_offset_s, 3),
                 hashlib.sha256(k.image_jpeg).hexdigest()) for k in ks]
    assert sig(kfs) == sig(vframes.extract_keyframes(blob, c1["codec"], span, vs))


# ---- Stable keyframe identity under a transient extraction drop (idempotency) --

def test_keyframe_index_is_stable_under_dropped_extraction():
    """A dropped/failed per-frame extraction must NOT renumber the survivors: their
    index (-> discriminator -> record_id) is their position in the deterministic
    selected-times list, so reprocessing is an idempotent upsert, not a renumber."""
    from app.vision.frames import _keyframes_from_times

    times = [0.0, 2.0, 4.0, 6.0]
    # Run 1: the t=2.0 extraction transiently fails (returns None).
    dropped = _keyframes_from_times(
        times, 8.0, lambda t: None if abs(t - 2.0) < 1e-9 else f"jpeg@{t}".encode()
    )
    # Survivors keep their ORIGINAL selected-times index — no dense renumber.
    assert [k.index for k in dropped] == [0, 2, 3]
    # Still a contiguous partition of [0, 8]; the dropped slice folds into index 0.
    assert dropped[0].t_offset_s == 0.0 and dropped[-1].t_end_offset_s == 8.0
    assert dropped[0].t_end_offset_s == 4.0
    for a, b in zip(dropped, dropped[1:]):
        assert a.t_end_offset_s == b.t_offset_s

    # Run 2: no drop. Survivors from run 1 keep the SAME indices (stable identity);
    # the recovered frame simply reappears under its own index (an added upsert).
    full = _keyframes_from_times(times, 8.0, lambda t: f"jpeg@{t}".encode())
    assert [k.index for k in full] == [0, 1, 2, 3]
    assert {k.index for k in dropped}.issubset({k.index for k in full})


# ---- E2E ingest (mock captioner): many records, DISTINCT per-keyframe spans ---

@requires_decoder
def test_real_video_fans_out_to_timed_keyframe_records(client):
    c1, blob = _load_video()
    resp = _post_video(client, c1, blob)
    assert resp.status_code == 200, resp.text
    record_ids = resp.json()["record_ids"]

    n = len(record_ids)
    assert n >= 2
    assert len(set(record_ids)) == n  # all distinct

    posts = client.fake_storage.record_posts
    assert len(posts) == n
    pv = video_proc.PIPELINE_VERSION  # mock dialect (default backend)

    for i, c2 in enumerate(posts):
        assert schemas.validate_c2(c2) == []
        assert c2["content"]["kind"] == "caption"
        assert c2["record_id"] == compute_record_id(c1["chunk_id"], pv, str(i))
        assert c2["source"]["chunk_id"] == c1["chunk_id"]

    # The headline of the timing hook: keyframe sub-spans are DISTINCT (they no
    # longer all collide on the chunk span) and partition [c1.t_start, c1.t_end].
    # Compare as instants (Z vs +00:00 are the same time).
    starts = [parse_rfc3339(c2["t_start"]) for c2 in posts]
    ends = [parse_rfc3339(c2["t_end"]) for c2 in posts]
    ct_start, ct_end = parse_rfc3339(c1["t_start"]), parse_rfc3339(c1["t_end"])
    assert len({c2["t_start"] for c2 in posts}) == n, "each keyframe has its own t_start (OQ14a)"
    assert starts[0] == ct_start           # opening keyframe starts at the chunk start
    assert ends[-1] == ct_end              # last keyframe ends at the chunk end
    for i in range(n - 1):
        assert ends[i] == starts[i + 1]    # contiguous partition
        assert starts[i] < starts[i + 1]   # ordered, distinct
    for st, en in zip(starts, ends):       # every sub-span sits inside the chunk span
        assert ct_start <= st < en <= ct_end

    # Captions are distinct per keyframe and weave OCR in (D8).
    captions = [c2["content"]["text"] for c2 in posts]
    assert len(set(captions)) == n
    assert all("On-screen text:" in cap for cap in captions)


@requires_decoder
def test_real_video_reingest_is_idempotent(client):
    c1, blob = _load_video()
    r1 = _post_video(client, c1, blob)
    r2 = _post_video(client, c1, blob)
    assert r1.status_code == r2.status_code == 200
    assert r1.json()["record_ids"] == r2.json()["record_ids"]
    n = len(r1.json()["record_ids"])
    assert len(client.fake_storage.record_posts) == n     # no duplicate writes
    assert len(client.fake_storage.blob_gets) == 1         # blob pulled once


# ---- Synthetic fallback (undecodable blob) preserves the mock shape ----------

def test_synthetic_fallback_shares_chunk_span_and_count():
    """Undecodable bytes -> SYNTHETIC_KEYFRAMES timing-less units carrying the chunk
    span verbatim: the exact backward-compatible shape the seam tests pin."""
    c1, _ = _load_video()
    settings = None  # video processor doesn't read shared Settings
    units = video_proc.VideoProcessor().process(
        c1, b"not-a-real-video-blob-\x00\x01\x02", settings, 8.0
    )
    assert len(units) == video_proc.SYNTHETIC_KEYFRAMES
    assert [u.discriminator for u in units] == [str(i) for i in range(len(units))]
    for u in units:
        assert u.t_start is None and u.t_end is None   # -> build_c2 carries C1 span
        assert u.content.kind == "caption"
    assert len({u.content.text for u in units}) == len(units)  # distinct captions


# ---- OCR-as-records option (D8, default OFF) ---------------------------------

@requires_decoder
def test_ocr_records_option_emits_ocr_units(client, monkeypatch):
    monkeypatch.setenv("VIDEO_OCR_RECORDS", "1")
    c1, blob = _load_video()
    resp = _post_video(client, c1, blob)
    assert resp.status_code == 200
    posts = client.fake_storage.record_posts

    caption_recs = [c for c in posts if c["content"]["kind"] == "caption"]
    ocr_recs = [c for c in posts if c["content"]["kind"] == "ocr"]
    assert ocr_recs, "VIDEO_OCR_RECORDS=1 must emit kind='ocr' records"
    assert len(ocr_recs) == len(caption_recs)  # one ocr record per captioned keyframe

    for i, c2 in enumerate(ocr_recs):
        assert schemas.validate_c2(c2) == []
        assert c2["record_id"] == compute_record_id(c1["chunk_id"], video_proc.PIPELINE_VERSION, f"{i}:ocr")
        assert c2["content"]["text"]  # the OCR text stands alone in its own record
    # An ocr record shares its caption keyframe's sub-span.
    assert {c["t_start"] for c in ocr_recs} == {c["t_start"] for c in caption_recs}


# ---- The real vlm backend: wire format + OCR weave (no GPU, fake VL server) ---

def _fake_vl_server(seen: list[dict]):
    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        return httpx.Response(200, json={
            "choices": [{"message": {"content":
                "Caption: A colored screen showing an application.\n"
                "On-screen text: DESK laptop and coffee"}}]
        })
    return handle


def test_vlm_malformed_response_raises_clear_error(monkeypatch):
    """A 200 with no choices must surface a clear ValueError (chunk retried), not an
    opaque KeyError/IndexError, and never a silently-degraded caption in /context."""
    from app.vision import vlm
    from app.vision.config import get_vision_settings
    from app.vision.result import Keyframe

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "x", "choices": []})  # malformed

    orig_client = httpx.Client
    monkeypatch.setattr(vlm.httpx, "Client", lambda *a, **k: orig_client(
        *a, **{**k, "transport": httpx.MockTransport(handle)}))

    kf = Keyframe(index=0, t_offset_s=0.0, t_end_offset_s=1.0, image_jpeg=b"\xff\xd8\xff\xd9")
    with pytest.raises(ValueError):
        vlm.caption(get_vision_settings(), [kf], {"chunk_id": "c"})


@requires_decoder
def test_vlm_backend_sends_images_and_weaves_ocr(client, monkeypatch):
    monkeypatch.setenv("VIDEO_BACKEND", "vlm")
    monkeypatch.setenv("VIDEO_VLM_URL", "http://vl.test")
    monkeypatch.setenv("VIDEO_VLM_MODEL", "test-vl")

    seen: list[dict] = []
    from app.vision import vlm
    orig_client = httpx.Client

    def fake_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(_fake_vl_server(seen))
        return orig_client(*args, **kwargs)

    monkeypatch.setattr(vlm.httpx, "Client", fake_client)

    # pipeline_version switches to the vlm dialect (forks records vs mock).
    assert video_proc.VideoProcessor().pipeline_version(None) == "vidproc-vlm-v0"

    c1, blob = _load_video()
    resp = _post_video(client, c1, blob)
    assert resp.status_code == 200, resp.text
    posts = client.fake_storage.record_posts
    assert posts and len(posts) == len(seen)  # one VL call per keyframe

    # Each request carried the keyframe as a base64 JPEG image_url data part.
    for body in seen:
        parts = body["messages"][-1]["content"]
        img = next(p for p in parts if p["type"] == "image_url")
        url = img["image_url"]["url"]
        assert url.startswith("data:image/jpeg;base64,")
        base64.b64decode(url.split(",", 1)[1])  # decodes cleanly
        assert body["temperature"] == 0  # greedy -> stable across reprocessings

    # The parsed caption + OCR are woven into the C2 caption target (D8).
    for c2 in posts:
        assert c2["pipeline_version"] == "vidproc-vlm-v0"
        assert "A colored screen showing an application" in c2["content"]["text"]
        assert "On-screen text: 'DESK laptop and coffee'" in c2["content"]["text"]


# ---- Independent-verification fixes (2026-07-19 round) ------------------------

def test_vlm_backend_refuses_placeholder_emission(monkeypatch):
    """Zero decodable keyframes under the REAL dialect must raise (-> 5xx -> the
    chunk is redelivered), never persist '[no decodable frame]' placeholders as
    processed truth under vidproc-vlm-v0. Mock (dev) keeps the synthetic fallback."""
    monkeypatch.setenv("VIDEO_BACKEND", "vlm")
    proc = video_proc.VideoProcessor()
    c1 = json.loads((_FX / "video_scenes.c1.json").read_text())
    with pytest.raises(RuntimeError, match="no decodable keyframes"):
        proc.process(c1, b"not-a-video", None, 8.0)


def test_head_drop_pins_first_record_to_chunk_start(monkeypatch):
    """A failed t=0 extraction must not orphan the chunk's head slice: the first
    emitted record is pinned to the C1 t_start (partition invariant)."""
    c1 = json.loads((_FX / "video_scenes.c1.json").read_text())
    span = 8.0

    def fake_extract(blob, codec, span_seconds, vs):
        # First keyframe (t=0) dropped; survivors start at 3.0.
        return [
            Keyframe(index=1, t_offset_s=3.0, t_end_offset_s=6.0, image_jpeg=b"j1"),
            Keyframe(index=2, t_offset_s=6.0, t_end_offset_s=8.0, image_jpeg=b"j2"),
        ]

    monkeypatch.setattr(video_proc, "extract_keyframes", fake_extract)
    units = video_proc.VideoProcessor().process(c1, b"x", None, span)
    assert units[0].t_start == c1["t_start"]          # head pinned, no gap
    assert units[-1].t_end == c1["t_end"]             # tail pinned (verbatim C1)


def test_short_media_pins_last_record_to_chunk_end(monkeypatch):
    """Decoded media shorter than the declared C1 span (encoder tail loss) must not
    leave a tail gap: the last record stretches to the C1 t_end verbatim."""
    c1 = json.loads((_FX / "video_scenes.c1.json").read_text())
    span = 8.0

    def fake_extract(blob, codec, span_seconds, vs):
        # Media decoded only 6.0s of the declared 8.0s span.
        return [
            Keyframe(index=0, t_offset_s=0.0, t_end_offset_s=3.0, image_jpeg=b"j0"),
            Keyframe(index=1, t_offset_s=3.0, t_end_offset_s=6.0, image_jpeg=b"j1"),
        ]

    monkeypatch.setattr(video_proc, "extract_keyframes", fake_extract)
    units = video_proc.VideoProcessor().process(c1, b"x", None, span)
    assert units[0].t_start == c1["t_start"]
    assert units[-1].t_end == c1["t_end"]             # 6.0s media, 8.0s span: no gap
    # Interior boundary is still the real keyframe time, not stretched.
    assert units[0].t_end == units[1].t_start


def test_vision_config_lenient_on_malformed_numerics(monkeypatch, caplog):
    """A numeric typo in the fleet env (e.g. locale comma) must degrade to the
    default with a warning — not 500 every video ingest until an operator notices."""
    monkeypatch.setenv("VIDEO_SCENE_THRESHOLD", "0,30")
    monkeypatch.setenv("VIDEO_MAX_KEYFRAMES", "8k")
    vs = get_vision_settings()
    assert vs.scene_threshold == 0.30
    assert vs.max_keyframes == 8
