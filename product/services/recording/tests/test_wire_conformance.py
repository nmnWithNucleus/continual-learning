"""Shared-wire conformance: every client shape the fleet produces, one frozen wire.

The `/capture/segments` wire (ws-b §Wire, server side ws-c) is client-agnostic by
design — the extension (ws-e) and the mac CLI (ws-f) were built against it with ZERO
server changes. This suite proves that claim against the REAL ingest app (TestClient,
sync mode, the existing MockTransport fakes), one test matrix over the segment shapes
the fleet now uploads:

  * extension-tab        muxed WebM/vp8+opus   (D-E7 tab capture: video + audio)
  * extension-tab-audio  audio-only WebM/opus  (D-E7 tab capture, video toggle off)
  * mac-cli              muxed MP4 h264+aac    (D-F1, ffmpeg avfoundation shape)

(The phone shape — muxed MP4 mpeg4+aac — is covered by test_capture_web; not repeated.
The pre-D-E7 extension screen-share shape — video-only WebM — is kept in the matrix too,
since the wire still accepts it and the mac CLI could produce it.)

Per shape it checks the whole promise, not just the ack: demux picks exactly the
modalities the container carries (probe decides, never the mime), C1 streams are dense
with the segment's wall-clock span carried through, audio lands as 16 kHz mono s16le
WAV and video is container-copied (magic-checked bytes in storage), and the two-leg
gap report reaches `clean`. Plus two wire invariants: N independent sessions under one
device_id stay isolated (distinct C1 streams, independent ledgers), and the ledger's
gap discipline applies to a brand-new client shape exactly as it did to the phone.

Wiring/helpers are imported from tests.test_capture_web (same fakes, same span math)
so the two suites can never drift apart on the wire they describe. Media fixtures are
session-scoped ~2 s clips; encoder availability is probed (skip, never fail, on a
box whose ffmpeg lacks libvpx/libopus/libx264).
"""
from __future__ import annotations

import functools
import io
import subprocess
import wave

import pytest

from app import contracts
from app.ids import new_ulid
from tests.test_capture_web import FFMPEG_BIN, IngestWiring, _make_ingest_wiring, needs_ffmpeg, span

EBML_MAGIC = b"\x1a\x45\xdf\xa3"  # WebM/Matroska header — container-copy proof


# ------------------------------------------------------------------ encoder probe

@functools.lru_cache(maxsize=None)
def _available_encoders() -> frozenset[str]:
    """Encoder names this box's ffmpeg was built with (column 2 of `-encoders`)."""
    out = subprocess.run(
        [FFMPEG_BIN, "-hide_banner", "-encoders"], capture_output=True, text=True, check=True
    ).stdout
    names = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0][:1] in ("V", "A", "S"):
            names.add(parts[1])
    return frozenset(names)


def _require_encoders(*names: str) -> None:
    if FFMPEG_BIN is None:
        pytest.skip("ffmpeg not on PATH")
    missing = [n for n in names if n not in _available_encoders()]
    if missing:
        pytest.skip(f"ffmpeg lacks encoder(s): {', '.join(missing)}")


# ------------------------------------------------------------------ media fixtures

def _ffmpeg(args: list[str]) -> None:
    subprocess.run([FFMPEG_BIN, "-v", "error", "-y", *args], check=True, capture_output=True)


@pytest.fixture(scope="session")
def screen_webm_bytes(tmp_path_factory) -> bytes:
    """~2s video-only WebM (vp8) — the extension's screen-share segment shape."""
    _require_encoders("libvpx")
    path = tmp_path_factory.mktemp("media") / "screen.webm"
    _ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=192x108:rate=10",
             "-t", "2", "-an", "-c:v", "libvpx", "-f", "webm", str(path)])
    return path.read_bytes()


@pytest.fixture(scope="session")
def tab_audio_webm_bytes(tmp_path_factory) -> bytes:
    """~2s audio-only WebM (opus) — the extension's audio-only tab shape (video off)."""
    _require_encoders("libopus")
    path = tmp_path_factory.mktemp("media") / "tab.webm"
    _ffmpeg(["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000",
             "-t", "2", "-vn", "-c:a", "libopus", "-f", "webm", str(path)])
    return path.read_bytes()


@pytest.fixture(scope="session")
def tab_muxed_webm_bytes(tmp_path_factory) -> bytes:
    """~2s muxed WebM (vp8 video + opus audio) — the extension's DEFAULT segment
    shape (D-E7: one tabCapture stream, video + audio, one MediaRecorder)."""
    _require_encoders("libvpx", "libopus")
    path = tmp_path_factory.mktemp("media") / "tabmux.webm"
    _ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=192x108:rate=10",
             "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000",
             "-t", "2", "-c:v", "libvpx", "-c:a", "libopus", "-f", "webm", str(path)])
    return path.read_bytes()


@pytest.fixture(scope="session")
def mac_mp4_bytes(tmp_path_factory) -> bytes:
    """~2s muxed MP4 (h264 yuv420p + aac, +faststart) — the mac CLI's segment shape."""
    _require_encoders("libx264", "aac")
    path = tmp_path_factory.mktemp("media") / "mac.mp4"
    _ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=192x108:rate=10",
             "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000",
             "-t", "2", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-movflags", "+faststart", str(path)])
    return path.read_bytes()


# ------------------------------------------------------------------------- wiring

@pytest.fixture()
def wire(monkeypatch, tmp_path) -> IngestWiring:
    """Sync-mode wiring against the real app (lifespan runs; fakes downstream)."""
    w = _make_ingest_wiring(monkeypatch, tmp_path, sync=True)
    with w.client:
        yield w


# ---------------------------------------------------------------- the shape matrix

# name -> (media fixture, upload mime, device_id, expected emit_leg as
# (modality, codec) pairs in the server's stable audio-first order).
SHAPES: dict[str, dict] = {
    "extension-tab": dict(  # D-E7 default: muxed tab video + audio, one session
        media="tab_muxed_webm_bytes", mime="video/webm", device_id="ext-chrome-test",
        legs=[("audio", "audio/wav"), ("video", "video/webm")],
    ),
    "extension-tab-audio": dict(  # D-E7 with the video toggle off
        media="tab_audio_webm_bytes", mime="audio/webm", device_id="ext-chrome-test",
        legs=[("audio", "audio/wav")],
    ),
    "extension-screen": dict(  # pre-D-E7 video-only shape; wire still accepts it
        media="screen_webm_bytes", mime="video/webm", device_id="ext-chrome-test",
        legs=[("video", "video/webm")],
    ),
    "mac-cli": dict(
        media="mac_mp4_bytes", mime="video/mp4", device_id="mac-cli-test",
        legs=[("audio", "audio/wav"), ("video", "video/mp4")],
    ),
}


def _assert_chunk_bytes(codec: str, blob: bytes) -> None:
    """The emitted bytes really are what the codec label claims."""
    if codec == "audio/wav":
        assert blob[:4] == b"RIFF" and blob[8:12] == b"WAVE"
        with wave.open(io.BytesIO(blob)) as wav:
            assert wav.getframerate() == 16000
            assert wav.getnchannels() == 1
            assert wav.getsampwidth() == 2
    elif codec == "video/webm":
        assert blob[:4] == EBML_MAGIC          # container-copied, not re-wrapped
    elif codec == "video/mp4":
        assert b"ftyp" in blob[:12]            # mp4 brand box leads (+faststart)
    else:
        raise AssertionError(f"unexpected codec {codec}")


# -------------------------------------------------------------- per-shape conformance

@needs_ffmpeg
@pytest.mark.parametrize("shape_name", sorted(SHAPES))
def test_shape_session_clean_end_to_end(wire, request, shape_name):
    """3 segments + end -> clean report, exact modalities, dense spans, honest bytes."""
    shape = SHAPES[shape_name]
    data = request.getfixturevalue(shape["media"])
    sid = new_ulid()

    for seq in range(3):
        resp = wire.post_segment(
            sid, seq, data, mime=shape["mime"], device_id=shape["device_id"]
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "received"
    assert wire.end(sid, last_seq=2).json() == {"ok": True}

    report = wire.report(sid)
    assert report["verdict"] == "clean"
    assert report["device_id"] == shape["device_id"]
    assert report["received_segments"] == 3
    assert report["segment_states"] == {"received": 0, "emitted": 3, "failed": 0}
    assert report["client_leg"]["missing_seqs"] == []
    # EXACTLY the modalities the container carries — probe decides, never the mime.
    assert [(leg["modality"], leg["codec"]) for leg in report["emit_leg"]] == shape["legs"]
    for leg in report["emit_leg"]:
        assert leg["chunks_emitted"] == 3
        assert leg["last_sequence"] == 2
        assert leg["pending"] == 0 and leg["failed"] == 0
        assert leg["dp"]["checked"] is True and leg["dp"]["missing_unacked"] == []

    # C1 side: schema-valid envelopes, one dense stream per modality, segment spans
    # preserved onto chunks (the posted t_start/t_end land verbatim on the C1).
    by_stream: dict[str, list[dict]] = {}
    for env in wire.dp.unique_envelopes():
        assert contracts.c1_errors(env) == [], env
        assert env["device_id"] == shape["device_id"]
        by_stream.setdefault(env["stream_id"], []).append(env)
    codec_by_stream = {leg["stream_id"]: leg["codec"] for leg in report["emit_leg"]}
    assert set(by_stream) == set(codec_by_stream)
    for stream_id, envs in by_stream.items():
        assert [e["sequence"] for e in envs] == [0, 1, 2]
        assert [(e["t_start"], e["t_end"]) for e in envs] == [span(0), span(1), span(2)]
        for env in envs:
            assert env["codec"] == codec_by_stream[stream_id]
            _assert_chunk_bytes(env["codec"], wire.storage.contents[env["chunk_id"]])


# ---------------------------------------- wire property: N sessions, one device

@needs_ffmpeg
def test_two_sessions_same_device_stay_isolated(wire, screen_webm_bytes, tab_audio_webm_bytes):
    """The ledger keeps independent sessions under one device_id fully isolated:
    distinct seq domains/ledgers, distinct C1 streams, zero cross-talk — ending one
    must not touch the other's verdict. (A general wire guarantee; two single-modality
    shapes stand in for any two concurrent sessions from the same device.)"""
    device = "ext-chrome-test"
    screen_sid, tab_sid = new_ulid(), new_ulid()
    for seq in range(2):  # interleaved
        assert wire.post_segment(
            screen_sid, seq, screen_webm_bytes, mime="video/webm", device_id=device
        ).status_code == 200
        assert wire.post_segment(
            tab_sid, seq, tab_audio_webm_bytes, mime="audio/webm", device_id=device
        ).status_code == 200

    # One session ends; the other keeps recording. Each client leg independent.
    wire.end(screen_sid, last_seq=1)
    screen_report = wire.report(screen_sid)
    tab_report = wire.report(tab_sid)
    assert screen_report["verdict"] == "clean"
    assert tab_report["verdict"] == "recording"
    assert tab_report["client_leg"]["unterminated"] is True
    assert tab_report["client_leg"]["missing_seqs"] == []      # no borrowed gaps
    assert tab_report["received_segments"] == 2
    assert tab_report["segment_states"]["emitted"] == 2        # emits kept flowing

    wire.end(tab_sid, last_seq=1)
    tab_report = wire.report(tab_sid)
    assert tab_report["verdict"] == "clean"

    # Two C1 streams, DISTINCT stream_ids, the SAME device_id on every envelope.
    assert [leg["modality"] for leg in screen_report["emit_leg"]] == ["video"]
    assert [leg["modality"] for leg in tab_report["emit_leg"]] == ["audio"]
    screen_streams = {leg["stream_id"] for leg in screen_report["emit_leg"]}
    tab_streams = {leg["stream_id"] for leg in tab_report["emit_leg"]}
    assert screen_streams.isdisjoint(tab_streams)
    envs = wire.dp.unique_envelopes()
    assert {e["stream_id"] for e in envs} == screen_streams | tab_streams
    assert {e["device_id"] for e in envs} == {device}


# ------------------------------------------------- gap discipline, new client shape

@needs_ffmpeg
def test_gap_detection_is_client_agnostic(wire, screen_webm_bytes):
    """A video-only webm session that loses seq 1 client-side gets the same verdict
    machinery the phone shape got: `gaps` + the exact missing seq, while the C1
    stream stays dense (a client-leg loss is NEVER fabricated as a DP gap)."""
    sid = new_ulid()
    for seq in (0, 2):  # seq 1 never arrives
        assert wire.post_segment(
            sid, seq, screen_webm_bytes, mime="video/webm", device_id="ext-chrome-test"
        ).status_code == 200
    wire.end(sid, last_seq=2)

    report = wire.report(sid)
    assert report["verdict"] == "gaps"
    assert report["client_leg"]["missing_seqs"] == [1]
    assert report["client_leg"]["missing_count"] == 1
    (leg,) = report["emit_leg"]
    assert leg["modality"] == "video"
    assert leg["chunks_emitted"] == 2 and leg["last_sequence"] == 1
    assert leg["dp"]["missing"] == [] and leg["dp"]["missing_unacked"] == []
    assert [e["sequence"] for e in wire.dp.unique_envelopes()] == [0, 1]
