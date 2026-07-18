"""Mac CLI capture client behaviour (WS-F): pure pieces, uploader wire against a
stdlib http.server stub, and one real-ffmpeg end-to-end run of the CLI in
--source test mode.

The module under test is loaded via importlib straight from clients/mac/ — the
CLI is a standalone stdlib-only file (D-F1), not an app package member, and the
integration test runs it exactly as a mac user would (a python subprocess).
Server semantics the stub mimics are app/capture_web.py's wire shapes.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.error
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from app import timeutil

CLI_PATH = Path(__file__).resolve().parent.parent / "clients" / "mac" / "nucleus_capture.py"

_spec = importlib.util.spec_from_file_location("nucleus_capture", CLI_PATH)
cap = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = cap  # dataclasses resolves string annotations via sys.modules
_spec.loader.exec_module(cap)

FFMPEG_BIN = shutil.which("ffmpeg")
FFPROBE_BIN = shutil.which("ffprobe")
needs_ffmpeg = pytest.mark.skipif(
    FFMPEG_BIN is None or FFPROBE_BIN is None, reason="ffmpeg/ffprobe not on PATH"
)

ANCHOR = datetime(2026, 7, 18, 12, 0, 0, tzinfo=timezone.utc)
ANCHOR_MS = int(ANCHOR.timestamp() * 1000)
T0 = "2026-07-18T12:00:00.000Z"
T1 = "2026-07-18T12:00:10.000Z"


@pytest.fixture(autouse=True)
def _no_proxies(monkeypatch):
    """urllib honors proxy env vars; the stub lives on 127.0.0.1."""
    for var in ("http_proxy", "https_proxy", "all_proxy",
                "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.delenv(var, raising=False)


# --------------------------------------------------------- D-F2 stamp chaining

def test_chain_stamps_exact_adjacency_and_wire_shape():
    durations = [10.0, 9.987, 10.043, 2.5]
    stamps = cap.chain_stamps(ANCHOR_MS, durations)
    assert len(stamps) == 4
    assert timeutil.parse_wallclock(stamps[0][0]) == ANCHOR
    for (_, t_end), (t_next_start, _) in zip(stamps, stamps[1:]):
        assert t_end == t_next_start  # exact (string-identical) adjacency
    total_ms = sum(round(d * 1000) for d in durations)
    assert timeutil.parse_wallclock(stamps[-1][1]) == ANCHOR + timedelta(
        milliseconds=total_ms
    )
    for t_start, t_end in stamps:
        # RFC3339 UTC ms precision — the exact t_* shape the server parses.
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", t_start)
        assert timeutil.parse_wallclock(t_end) > timeutil.parse_wallclock(t_start)


def test_chain_stamps_empty():
    assert cap.chain_stamps(ANCHOR_MS, []) == []


def test_slot_duration_is_idempotent_on_reprocess():
    """A Ctrl-C aborting an in-flight upload re-enters process_ready for the
    same seq; slotting (not appending) must leave every stamp unchanged."""
    durations = []
    cap.slot_duration(durations, 0, 10.01, 10)
    cap.slot_duration(durations, 1, 9.99, 10)
    first = cap.chain_stamps(ANCHOR_MS, durations)
    cap.slot_duration(durations, 1, 9.99, 10)  # seq 1 re-processed after interrupt
    assert cap.chain_stamps(ANCHOR_MS, durations) == first
    assert len(durations) == 2
    cap.slot_duration(durations, 2, 10.02, 10)  # later segments unaffected
    assert cap.chain_stamps(ANCHOR_MS, durations)[:2] == first


def test_slot_duration_pads_vanished_seqs_with_default():
    durations = []
    cap.slot_duration(durations, 2, 9.5, 10)
    assert durations == [10.0, 10.0, 9.5]


# --------------------------------------------------------- D-F4 spool scanning

def test_uploadable_holds_newest_while_ffmpeg_runs():
    assert cap.uploadable_segments([], False) == []
    assert cap.uploadable_segments(["seg-000000.mp4"], False) == []
    assert cap.uploadable_segments(
        ["seg-000000.mp4", "seg-000001.mp4"], False
    ) == [(0, "seg-000000.mp4")]


def test_uploadable_all_final_on_ffmpeg_exit_ordered_by_seq():
    listing = ["seg-000002.mp4", "seg-000000.mp4", "seg-000001.mp4"]
    assert cap.uploadable_segments(listing, True) == [
        (0, "seg-000000.mp4"), (1, "seg-000001.mp4"), (2, "seg-000002.mp4")
    ]
    assert cap.uploadable_segments(["seg-000000.mp4"], True) == [(0, "seg-000000.mp4")]


def test_uploadable_ignores_non_segment_noise():
    listing = [".DS_Store", "seg-000000.mp4.part", "notes.txt",
               "seg-000001.mp4", "seg-000000.mp4"]
    assert cap.uploadable_segments(listing, False) == [(0, "seg-000000.mp4")]
    assert cap.uploadable_segments(listing, True) == [
        (0, "seg-000000.mp4"), (1, "seg-000001.mp4")
    ]


def test_uploadable_matches_ffmpeg_percent06d_widening():
    """%06d widens past 999999 rather than wrapping; the scan must follow."""
    listing = ["seg-1000000.mp4", "seg-999999.mp4"]
    assert cap.uploadable_segments(listing, True) == [
        (999999, "seg-999999.mp4"), (1000000, "seg-1000000.mp4")
    ]


# ------------------------------------------------------------- ffmpeg shapes

def test_ffmpeg_argv_test_source_with_mpeg4_fallback():
    cfg = cap.CaptureConfig(source="test", spool_dir="/sp", segment_seconds=2,
                            duration=4.0, video_encoder="mpeg4")
    argv = cap.build_ffmpeg_argv(cfg)
    assert argv.count("lavfi") == 2
    assert any(a.startswith("testsrc2=") for a in argv)
    assert any(a.startswith("sine=") for a in argv)
    assert float(argv[argv.index("-t") + 1]) == 4.0
    assert argv[argv.index("-c:v") + 1] == "mpeg4"
    assert "-preset" not in argv and "-crf" not in argv  # libx264-only knobs
    assert argv[argv.index("-pix_fmt") + 1] == "yuv420p"
    assert "segment" in argv
    assert argv[argv.index("-segment_time") + 1] == "2"
    assert argv[argv.index("-reset_timestamps") + 1] == "1"
    assert argv[argv.index("-force_key_frames") + 1] == "expr:gte(t,n_forced*2)"
    assert argv[argv.index("-segment_format_options") + 1] == "movflags=+faststart"
    assert argv[-1] == os.path.join("/sp", "seg-%06d.mp4")
    assert "avfoundation" not in argv and "-capture_cursor" not in argv


def test_ffmpeg_argv_avfoundation_x264():
    cfg = cap.CaptureConfig(source="avfoundation", spool_dir="/sp", screen_index=3,
                            audio_index=1, framerate=30, video_encoder="libx264")
    argv = cap.build_ffmpeg_argv(cfg)
    assert argv[argv.index("-f") + 1] == "avfoundation"
    assert argv[argv.index("-capture_cursor") + 1] == "1"
    assert argv[argv.index("-framerate") + 1] == "30"
    assert argv[argv.index("-i") + 1] == "3:1"
    assert "min(1728,iw)" in argv[argv.index("-vf") + 1]  # retina downscale
    assert argv[argv.index("-c:v") + 1] == "libx264"
    assert argv[argv.index("-preset") + 1] == "veryfast"
    assert argv[argv.index("-crf") + 1] == "28"
    assert argv[argv.index("-c:a") + 1] == "aac"
    assert argv[argv.index("-force_key_frames") + 1] == "expr:gte(t,n_forced*10)"
    assert "-t" not in argv and "lavfi" not in argv


def test_pick_video_encoder_probes_encoder_table():
    with_x264 = " V....D libx264   H.264 / AVC\n V.S... mpeg4   MPEG-4 part 2\n"
    without = " V.S... mpeg4   MPEG-4 part 2\n A....D aac   AAC\n"
    assert cap.pick_video_encoder(with_x264) == "libx264"
    assert cap.pick_video_encoder(without) == "mpeg4"


def test_backoff_delay_doubles_and_caps():
    assert [cap.backoff_delay_s(n) for n in range(6)] == [1, 2, 4, 8, 16, 30]
    assert cap.backoff_delay_s(10) == 30


# ----------------------------------------------------------- http.server stub

class StubState:
    def __init__(self):
        self.lock = threading.Lock()
        self.segments = []       # {"params", "body", "content_type"} per POST
        self.ends = []           # {"session_id", "payload", "content_type"}
        self.fail_statuses = []  # popped per segment POST -> injected status
        self.report_override = None


def _clean_report_locked(state: StubState, session_id: str) -> dict:
    """A canned clean report shaped like app/capture_web.py's (drained state)."""
    n = len([s for s in state.segments
             if s["params"].get("session_id") == session_id])
    def leg(modality, stream_id, codec):
        return {"modality": modality, "stream_id": stream_id, "codec": codec,
                "chunks_emitted": n, "last_sequence": n - 1 if n else None,
                "pending": 0, "failed": 0, "dp": {"checked": False}}
    return {
        "session_id": session_id, "user_id": "beta-user", "device_id": "mac-cli-stub",
        "started_at": "2026-07-18T12:00:00Z", "ended": True,
        "expected_segments": n, "received_segments": n,
        "segment_states": {"received": 0, "emitted": n, "failed": 0},
        "client_leg": {"missing_seqs": [], "missing_count": 0,
                       "duplicate_deliveries": 0, "unterminated": False},
        "emit_leg": [leg("audio", "stub-a", "audio/wav"),
                     leg("video", "stub-v", "video/mp4")],
        "verdict": "clean",
    }


@pytest.fixture()
def stub():
    """Threaded stdlib HTTP stub of the ingest wire on an ephemeral port."""
    state = StubState()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # keep pytest output clean
            pass

        def _json(self, code, payload):
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            url = urlsplit(self.path)
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if url.path == "/capture/segments":
                with state.lock:
                    if state.fail_statuses:
                        self._json(state.fail_statuses.pop(0), {"detail": "injected"})
                        return
                    params = {k: v[0] for k, v in
                              parse_qs(url.query, keep_blank_values=True).items()}
                    state.segments.append({
                        "params": params, "body": body,
                        "content_type": self.headers.get("Content-Type"),
                    })
                self._json(200, {"ok": True, "session_id": params["session_id"],
                                 "seq": int(params["seq"]), "status": "received"})
                return
            m = re.fullmatch(r"/capture/sessions/([^/]+)/end", url.path)
            if m:
                with state.lock:
                    state.ends.append({
                        "session_id": m.group(1), "payload": json.loads(body),
                        "content_type": self.headers.get("Content-Type"),
                    })
                self._json(200, {"ok": True})
                return
            self._json(404, {"detail": "unknown path"})

        def do_GET(self):
            m = re.fullmatch(r"/capture/sessions/([^/]+)/report", urlsplit(self.path).path)
            if not m:
                self._json(404, {"detail": "unknown path"})
                return
            with state.lock:
                report = state.report_override or _clean_report_locked(state, m.group(1))
            self._json(200, report)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield "http://127.0.0.1:%d" % server.server_address[1], state
    finally:
        server.shutdown()
        server.server_close()


def _mk_uploader(url, **kw):
    sleeps = []
    kw.setdefault("sleep", sleeps.append)
    kw.setdefault("log", lambda msg: None)
    up = cap.SegmentUploader(url, cap.new_session_id(), "beta-user", "mac-cli-test", **kw)
    return up, sleeps


def _seg_file(tmp_path, seq, data=None):
    path = tmp_path / ("seg-%06d.mp4" % seq)
    path.write_bytes(data if data is not None else b"segment-%d-bytes" % seq)
    return path


# ------------------------------------------------------------- uploader wire

def test_uploader_in_order_sha256_and_delete_after_ack(stub, tmp_path):
    url, state = stub
    up, _sleeps = _mk_uploader(url)
    for seq in range(3):
        path = _seg_file(tmp_path, seq)
        assert up.upload(path, seq, T0, T1) is True
        assert not path.exists()  # acked -> deleted from the spool
    assert [int(s["params"]["seq"]) for s in state.segments] == [0, 1, 2]
    for s in state.segments:
        assert s["content_type"] == "application/octet-stream"
        assert s["params"]["sha256"] == hashlib.sha256(s["body"]).hexdigest()
        assert s["params"]["session_id"] == up.session_id
        assert s["params"]["device_id"] == "mac-cli-test"
        assert s["params"]["mime"] == "video/mp4"
        assert (s["params"]["t_start"], s["params"]["t_end"]) == (T0, T1)
    assert up.uploaded == 3 and up.dropped == 0


def test_uploader_retries_5xx_then_succeeds_with_backoff(stub, tmp_path):
    url, state = stub
    with state.lock:
        state.fail_statuses[:] = [503, 503]
    up, sleeps = _mk_uploader(url)
    path = _seg_file(tmp_path, 0)
    assert up.upload(path, 0, T0, T1) is True
    assert sleeps == [1.0, 2.0]  # 1s * 2^n
    assert len(state.segments) == 1 and up.uploaded == 1
    assert not path.exists()


def test_uploader_retries_network_error(tmp_path):
    calls = []

    def flaky_post(url, body, content_type):
        calls.append(url)
        if len(calls) < 3:
            raise urllib.error.URLError("connection refused")
        return 200, "{}"

    sleeps = []
    up = cap.SegmentUploader("http://unreachable.invalid", "S", "u", "d",
                             post=flaky_post, sleep=sleeps.append, log=lambda m: None)
    assert up.upload(_seg_file(tmp_path, 0), 0, T0, T1) is True
    assert sleeps == [1.0, 2.0]
    assert len(calls) == 3


def test_uploader_retries_http_exception_mid_response(tmp_path):
    """BadStatusLine/IncompleteRead are HTTPException, NOT OSError — a server
    dying mid-response must be retried, never crash the capture run."""
    import http.client as hc

    calls = []

    def dying_post(url, body, content_type):
        calls.append(url)
        if len(calls) == 1:
            raise hc.BadStatusLine("garbage")
        if len(calls) == 2:
            raise hc.IncompleteRead(b"partial")
        return 200, "{}"

    sleeps = []
    up = cap.SegmentUploader("http://flaky.invalid", "S", "u", "d",
                             post=dying_post, sleep=sleeps.append, log=lambda m: None)
    assert up.upload(_seg_file(tmp_path, 0), 0, T0, T1) is True
    assert len(calls) == 3
    assert sleeps == [1.0, 2.0]


def test_uploader_4xx_surfaced_counted_dropped_queue_continues(stub, tmp_path):
    url, state = stub
    with state.lock:
        state.fail_statuses[:] = [400]
    logs = []
    up, _sleeps = _mk_uploader(url, log=logs.append)
    p0 = _seg_file(tmp_path, 0)
    assert up.upload(p0, 0, T0, T1) is False
    assert up.dropped == 1
    assert p0.exists()  # dropped file stays in the spool as evidence
    assert any("400" in msg for msg in logs)  # surfaced
    p1 = _seg_file(tmp_path, 1)
    assert up.upload(p1, 1, T0, T1) is True  # the queue keeps moving
    assert [int(s["params"]["seq"]) for s in state.segments] == [1]


def test_uploader_keep_segments_keeps_the_file(stub, tmp_path):
    url, _state = stub
    up, _sleeps = _mk_uploader(url, keep_segments=True)
    path = _seg_file(tmp_path, 0)
    assert up.upload(path, 0, T0, T1) is True
    assert path.exists()


def test_end_marker_posts_last_seq_json(stub):
    url, state = stub
    up, _sleeps = _mk_uploader(url)
    assert up.end(6) is True
    assert state.ends == [{"session_id": up.session_id, "payload": {"last_seq": 6},
                           "content_type": "application/json"}]


def test_poll_report_round_trip(stub, tmp_path):
    url, _state = stub
    up, _sleeps = _mk_uploader(url)
    up.upload(_seg_file(tmp_path, 0), 0, T0, T1)
    report = up.poll_report()
    assert report["verdict"] == "clean" and report["received_segments"] == 1
    assert cap.poll_report(url, up.session_id)["session_id"] == up.session_id


# ------------------------------------------------------- report -> exit codes

def _report(verdict, *, ended=True, received=0):
    return {"session_id": "S", "ended": ended, "verdict": verdict,
            "expected_segments": 2, "received_segments": 2,
            "segment_states": {"received": received, "emitted": 2, "failed": 0},
            "client_leg": {"missing_count": 0}, "emit_leg": []}


def test_await_report_maps_verdict_to_exit_code():
    for verdict, code in (("clean", 0), ("gaps", 2)):
        get = lambda url, v=verdict: (200, json.dumps(_report(v)))
        out = []
        assert cap.await_final_report("http://x", "S", timeout_s=5, get=get,
                                      sleep=lambda s: None, out=out.append) == code
        assert any("verdict: %s" % verdict in line for line in out)


def test_await_report_times_out_while_draining():
    get = lambda url: (200, json.dumps(_report("recording", ended=False, received=2)))
    clock_now = [0.0]
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        clock_now[0] += s

    out = []
    code = cap.await_final_report("http://x", "S", timeout_s=10, get=get, sleep=sleep,
                                  clock=lambda: clock_now[0], out=out.append)
    assert code == 1
    assert sleeps and set(sleeps) == {2.0}  # the pinned 2s poll cadence
    assert any("did not settle" in line for line in out)


# ------------------------------------------------------- CLI process behaviour

@pytest.mark.skipif(sys.platform == "darwin", reason="refusal is the non-mac path")
def test_record_refuses_avfoundation_off_mac():
    proc = subprocess.run(
        [sys.executable, str(CLI_PATH), "record", "--server", "http://127.0.0.1:1"],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 2
    assert "macOS" in proc.stderr and "--source test" in proc.stderr


@pytest.mark.skipif(sys.platform == "darwin", reason="doc-command path is non-mac")
def test_list_devices_off_mac_is_a_doc_command():
    proc = subprocess.run(
        [sys.executable, str(CLI_PATH), "list-devices"],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0
    assert "avfoundation" in proc.stdout


@needs_ffmpeg
def test_record_refuses_a_stale_spool_dir(stub, tmp_path):
    """seg-*.mp4 left in a reused --spool would upload into the NEW session as
    its first segments (the watcher cannot tell the runs apart) — refuse."""
    url, state = stub
    spool = tmp_path / "spool"
    spool.mkdir()
    (spool / "seg-000000.mp4").write_bytes(b"stale-prior-run-bytes")
    proc = subprocess.run(
        [sys.executable, str(CLI_PATH), "record", "--source", "test",
         "--duration", "2", "--server", url, "--spool", str(spool)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 1
    assert "previous run" in proc.stderr
    assert state.segments == [] and state.ends == []  # nothing was uploaded
    assert (spool / "seg-000000.mp4").exists()        # and nothing was deleted


def test_record_zero_segments_skips_end_and_report(stub, tmp_path):
    """ffmpeg exiting CLEANLY with no segments: the session never existed
    server-side, so posting an end marker / polling the report would 404 and
    burn the whole --report-timeout. Exit 1 fast instead."""
    url, state = stub
    fake = tmp_path / "fake-ffmpeg"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    # Both binaries faked: this test must not depend on real ffmpeg/ffprobe.
    env = dict(os.environ, FFMPEG_BIN=str(fake), FFPROBE_BIN=str(fake), HOME=str(tmp_path))
    proc = subprocess.run(
        [sys.executable, str(CLI_PATH), "record", "--source", "test",
         "--duration", "2", "--server", url, "--spool", str(tmp_path / "sp"),
         "--report-timeout", "30"],
        capture_output=True, text=True, timeout=30, env=env,
    )
    assert proc.returncode == 1
    assert "no segments" in proc.stderr
    assert state.segments == [] and state.ends == []  # no end marker, no report hit


@needs_ffmpeg
def test_cli_record_test_source_end_to_end(stub, tmp_path):
    """The whole client as a subprocess: real ffmpeg lavfi capture -> segment
    spool -> serialized upload -> end marker -> report poll -> exit code."""
    url, state = stub
    spool = tmp_path / "spool"
    home = tmp_path / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items()
           if k.lower() not in ("http_proxy", "https_proxy", "all_proxy")}
    env["HOME"] = str(home)  # device_id file must not touch the real ~/.nucleus

    proc = subprocess.run(
        [sys.executable, str(CLI_PATH), "record", "--source", "test",
         "--duration", "4", "--segment-seconds", "2",
         "--server", url, "--spool", str(spool), "--report-timeout", "30"],
        capture_output=True, text=True, timeout=180, env=env,
    )
    assert proc.returncode == 0, "stdout:\n%s\nstderr:\n%s" % (proc.stdout, proc.stderr)

    segs = state.segments
    assert len(segs) >= 2
    assert [int(s["params"]["seq"]) for s in segs] == list(range(len(segs)))  # dense, in order

    sids = {s["params"]["session_id"] for s in segs}
    assert len(sids) == 1
    sid = sids.pop()
    assert re.fullmatch(r"[0-9ABCDEFGHJKMNPQRSTVWXYZ]{26}", sid)  # ULID-ish

    for s in segs:
        assert b"ftyp" in s["body"][:12]  # a real, self-contained mp4
        assert s["params"]["sha256"] == hashlib.sha256(s["body"]).hexdigest()
        assert s["params"]["device_id"].startswith("mac-cli-")
        assert s["params"]["user_id"] == "beta-user"
        assert s["content_type"] == "application/octet-stream"
        assert timeutil.parse_wallclock(s["params"]["t_end"]) > timeutil.parse_wallclock(
            s["params"]["t_start"]
        )
    for prev, nxt in zip(segs, segs[1:]):  # D-F2: exact adjacency across the wire
        assert prev["params"]["t_end"] == nxt["params"]["t_start"]

    assert state.ends == [{"session_id": sid, "payload": {"last_seq": len(segs) - 1},
                           "content_type": "application/json"}]
    assert "verdict: clean" in proc.stdout          # the stub's canned answer
    assert not list(spool.glob("seg-*.mp4"))        # acked segments were deleted
    assert (home / ".nucleus" / "device_id").exists()
