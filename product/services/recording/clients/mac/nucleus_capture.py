#!/usr/bin/env python3
"""Nucleus mac capture CLI (WS-F): ffmpeg screen+mic capture -> segment upload wire.

One stdlib-only python3 (>=3.9) file, no pip deps (D-F1): ffmpeg is the
capture+segmenter (`-f segment`, ~10 s self-contained mp4s cut on forced
keyframes into a spool dir); this process watches the spool and speaks the
exact phone-client wire (handoff/ws-b-phone-web-client.md / ws-c, server side
app/capture_web.py — internal to recording, not a C-contract):

  POST /capture/segments?session_id=&seq=&user_id=&device_id=&t_start=&t_end=
       &mime=&sha256=                (raw mp4 bytes; ack -> spool file deleted)
  POST /capture/sessions/{id}/end    {"last_seq": n}
  GET  /capture/sessions/{id}/report (polled until drained; verdict -> exit code)

Segments are muxed A/V exactly like the phone client — the server demuxes into
two C1 streams. Decisions pinned in handoff/ws-f-mac-cli.md:

- D-F2  wall-clock stamps are duration-chained: anchor = st_birthtime of segment
        0 where the OS provides it (macOS does) else the ffmpeg spawn wall-clock;
        t_start[0] = anchor, t_end[n] = t_start[n] + ffprobe duration[n],
        t_start[n+1] = t_end[n]. Capture IS continuous, so exact adjacency is
        honest (unlike the phone's restart-gap segments). Stated v0
        approximation: the anchor can sit ~1-2 s off true first-frame time.
- D-F3  --source test (lavfi testsrc2+sine) drives the SAME encode/segment/
        upload path: the headless-box verification mode and a mac user's
        no-permissions smoke test. `record` without it refuses on non-darwin —
        avfoundation exists only on macOS; nothing is faked.
- D-F4  a segment is final exactly when a higher-numbered seg file exists (the
        segment muxer opens n+1 only after finalizing n); on ffmpeg exit, every
        remaining file is final. No inotify, no size heuristics.
- Stop: first Ctrl-C -> graceful: 'q' to ffmpeg stdin (clean moov; SIGINT
        fallback), upload the tail, POST end, poll the report until drained,
        print the summary. Exit 0 clean / 2 gaps / 1 error-or-timeout. Second
        Ctrl-C -> abandon politely (the ledger flags the session unterminated).

Upload queue semantics are ws-b's: ONE serialized queue in seq order; retry
forever on network/5xx (backoff 1 s * 2^n, cap 30 s); a 4xx is a client bug —
surfaced, counted, dropped (the file stays in the spool as evidence).
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import http.client
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_SERVER = "http://localhost:8084"
BACKOFF_BASE_S = 1.0
BACKOFF_CAP_S = 30.0
REPORT_POLL_S = 2.0
SPOOL_POLL_S = 0.5

# ------------------------------------------------------------------- identity

# Crockford base32 (no I, L, O, U) — the ULID alphabet. Mirrors app/ids.py /
# the phone client's minting, duplicated because this file must run standalone
# on a mac with nothing but itself + ffmpeg (D-F1).
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _crockford(value: int, length: int) -> str:
    chars = [""] * length
    for i in range(length - 1, -1, -1):
        chars[i] = _CROCKFORD[value & 0x1F]
        value >>= 5
    return "".join(chars)


def new_session_id() -> str:
    """26-char ULID-ish id: 48-bit ms timestamp + 80-bit randomness."""
    ts_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand = int.from_bytes(os.urandom(10), "big")
    return _crockford(ts_ms, 10) + _crockford(rand, 16)


def load_device_id() -> str:
    """`mac-cli-<suffix>`, suffix persisted at ~/.nucleus/device_id (0600, dir 0700)."""
    base = Path.home() / ".nucleus"
    path = base / "device_id"
    suffix = path.read_text().strip() if path.exists() else ""
    if not suffix:
        base.mkdir(mode=0o700, parents=True, exist_ok=True)
        suffix = "".join(_CROCKFORD[b & 31] for b in os.urandom(8))
        path.write_text(suffix + "\n")
        os.chmod(path, 0o600)
    return "mac-cli-" + suffix


# ------------------------------------------------------- duration-chained stamps

def rfc3339_ms(epoch_ms: int) -> str:
    """RFC3339 UTC with fixed millisecond precision (the wire's t_* shape)."""
    sec, ms = divmod(int(epoch_ms), 1000)
    return datetime.fromtimestamp(sec, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S"
    ) + ".%03dZ" % ms


def chain_stamps(anchor_ms: int, durations_s) -> list:
    """D-F2: [(t_start, t_end)] per segment, chained from the anchor.

    Accumulates in integer milliseconds so t_end[n] and t_start[n+1] are
    string-identical — exact adjacency is the continuity signal a server-side
    reader of a continuous source gets to rely on.
    """
    out = []
    t = int(anchor_ms)
    for dur in durations_s:
        end = t + round(float(dur) * 1000)
        out.append((rfc3339_ms(t), rfc3339_ms(end)))
        t = end
    return out


def slot_duration(durations: list, seq: int, dur: float, default_s: float) -> None:
    """Idempotently record ``seq``'s probed duration (gaps padded with the
    nominal segment length).

    SLOTTED, never appended: a Ctrl-C landing inside an in-flight upload aborts
    process_ready before the seq reaches ``done``, and the graceful-stop pass
    re-processes the same seq — an append there would double-count the duration
    and silently shift every later chained stamp (review round, WS-F worklog).
    """
    while len(durations) <= seq:
        durations.append(float(default_s))
    durations[seq] = float(dur)


# ---------------------------------------------------------------- spool scan

# \d{6,}: ffmpeg's %06d WIDENS past seg-999999 rather than wrapping — a
# 6-digit anchor would silently stall the watcher there.
_SEGMENT_NAME = re.compile(r"^seg-(\d{6,})\.mp4$")


def uploadable_segments(listing, ffmpeg_exited: bool) -> list:
    """D-F4: [(seq, name)] sorted by seq, final segments only.

    While ffmpeg runs, the highest-numbered file is the one still being
    written (the muxer opens n+1 only after finalizing n) — held back. On
    ffmpeg exit everything on disk is final. Non-segment names are ignored.
    """
    found = []
    for entry in listing:
        m = _SEGMENT_NAME.match(os.path.basename(str(entry)))
        if m:
            found.append((int(m.group(1)), os.path.basename(str(entry))))
    found.sort()
    return found if ffmpeg_exited else found[:-1]


# -------------------------------------------------------------- ffmpeg shapes

@dataclasses.dataclass
class CaptureConfig:
    """One record run's ffmpeg shape (pinned in ws-f §ffmpeg shapes)."""

    source: str                    # "avfoundation" | "test"
    spool_dir: str
    screen_index: int = 1
    audio_index: int = 0
    framerate: int = 15
    max_width: int = 1728          # retina delivers pixel (2x) resolution
    segment_seconds: int = 10
    duration: float = None         # None = until stopped; -t otherwise
    video_encoder: str = "libx264"
    capture_cursor: bool = True
    ffmpeg_bin: str = "ffmpeg"


def pick_video_encoder(encoders_text: str) -> str:
    """libx264 when this ffmpeg build has it, else mpeg4 (minimal/conda builds)."""
    return "libx264" if " libx264 " in encoders_text else "mpeg4"


def probe_encoders(ffmpeg_bin: str, run=subprocess.run) -> str:
    proc = run([ffmpeg_bin, "-hide_banner", "-encoders"], capture_output=True, text=True)
    return proc.stdout or ""


def build_ffmpeg_argv(cfg: CaptureConfig) -> list:
    argv = [cfg.ffmpeg_bin, "-hide_banner", "-loglevel", "warning"]
    if cfg.source == "avfoundation":
        argv += [
            "-f", "avfoundation",
            "-capture_cursor", "1" if cfg.capture_cursor else "0",
            "-framerate", str(cfg.framerate),
            "-i", "%d:%d" % (cfg.screen_index, cfg.audio_index),
        ]
    else:
        argv += [
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=%d" % cfg.framerate,
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
        ]
    if cfg.duration is not None:
        argv += ["-t", str(cfg.duration)]
    if cfg.source == "avfoundation":
        # Quotes are filtergraph-level (protect the comma in min()), not shell.
        # fps= pins the OUTPUT rate: when the screen device refuses the input
        # -framerate request ("Configuration of video device failed, falling
        # back to default") ffmpeg otherwise derives a garbage rate from the
        # device timebase, duplicates frames endlessly, and the segment muxer
        # (which cuts on MEDIA time) never finalizes a file — zero uploads.
        # Found live in the first real mac run (alpha 2026-07-19).
        argv += ["-vf", "scale='min(%d,iw)':-2,fps=%d" % (cfg.max_width, cfg.framerate)]
    if cfg.video_encoder == "libx264":
        argv += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "28"]
    else:
        argv += ["-c:v", "mpeg4", "-qscale:v", "6"]
    # avfoundation delivers bgra; yuv420p is required for players and the demux.
    argv += ["-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k"]
    seg = cfg.segment_seconds
    argv += [
        "-f", "segment", "-segment_time", str(seg), "-reset_timestamps", "1",
        "-force_key_frames", "expr:gte(t,n_forced*%d)" % seg,
        "-segment_format", "mp4", "-segment_format_options", "movflags=+faststart",
        os.path.join(cfg.spool_dir, "seg-%06d.mp4"),
    ]
    return argv


def probe_duration_s(ffprobe_bin: str, path, run=subprocess.run) -> float:
    proc = run(
        [ffprobe_bin, "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    return float(proc.stdout.strip())


# ------------------------------------------------------------------ the wire

def backoff_delay_s(attempt: int) -> float:
    return min(BACKOFF_BASE_S * (2 ** attempt), BACKOFF_CAP_S)


def _http_post(url: str, body: bytes, content_type: str):
    """(status_code, body_text); network failures raise (URLError/OSError)."""
    req = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": content_type}
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:  # a non-2xx IS a response, not transport
        return exc.code, exc.read().decode("utf-8", "replace")


def _http_get(url: str):
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def _stderr(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def poll_report(server: str, session_id: str, get=None) -> dict:
    status, text = (get or _http_get)(
        "%s/capture/sessions/%s/report" % (server.rstrip("/"), session_id)
    )
    if status != 200:
        raise RuntimeError("report: HTTP %s: %s" % (status, text[:200]))
    return json.loads(text)


class SegmentUploader:
    """Serialized wire speaker: one segment at a time, arrival order == seq order.

    ws-b queue semantics: retry forever on network error / 5xx with exponential
    backoff; a 4xx is a client bug — surfaced, counted, dropped, and the queue
    keeps moving. sha256 always computed. Acked files are deleted from the
    spool unless keep_segments; a 4xx-dropped file is kept as evidence.
    """

    def __init__(self, server, session_id, user_id, device_id, *,
                 keep_segments=False, post=None, get=None,
                 sleep=time.sleep, log=_stderr):
        self.server = server.rstrip("/")
        self.session_id = session_id
        self.user_id = user_id
        self.device_id = device_id
        self.keep_segments = keep_segments
        self._post = post or _http_post
        self._get = get or _http_get
        self._sleep = sleep
        self._log = log
        self.uploaded = 0
        self.dropped = 0

    def _post_with_retry(self, url, body, content_type, what):
        attempt = 0
        while True:
            try:
                status, text = self._post(url, body, content_type)
            except (OSError, http.client.HTTPException) as exc:
                # OSError covers URLError/timeouts; HTTPException (BadStatusLine,
                # IncompleteRead, ...) is NOT an OSError but is the same transient
                # a mid-response server death produces — retry, don't crash.
                status, text = None, str(exc)
            if status is not None and 200 <= status < 300:
                return True
            if status is not None and 400 <= status < 500:
                self._log("%s: rejected with %d: %s — dropped (a 4xx is a client "
                          "bug, not retried)" % (what, status, text[:200]))
                return False
            delay = backoff_delay_s(attempt)
            reason = "HTTP %d" % status if status is not None else text
            self._log("%s: %s — retrying in %.0fs" % (what, reason, delay))
            self._sleep(delay)
            attempt += 1

    def upload(self, path, seq, t_start, t_end, mime="video/mp4") -> bool:
        path = Path(path)
        body = path.read_bytes()
        params = urllib.parse.urlencode({
            "session_id": self.session_id, "seq": seq,
            "user_id": self.user_id, "device_id": self.device_id,
            "t_start": t_start, "t_end": t_end, "mime": mime,
            "sha256": hashlib.sha256(body).hexdigest(),
        })
        ok = self._post_with_retry(
            "%s/capture/segments?%s" % (self.server, params),
            body, "application/octet-stream", "segment %d" % seq,
        )
        if not ok:
            self.dropped += 1
            return False
        self.uploaded += 1
        if not self.keep_segments:
            path.unlink(missing_ok=True)
        return True

    def end(self, last_seq: int) -> bool:
        return self._post_with_retry(
            "%s/capture/sessions/%s/end" % (self.server, self.session_id),
            json.dumps({"last_seq": last_seq}).encode(), "application/json",
            "end marker",
        )

    def poll_report(self) -> dict:
        return poll_report(self.server, self.session_id, get=self._get)


# ------------------------------------------------------------ report -> exit

def print_report_summary(report: dict, out=print) -> None:
    expected = report.get("expected_segments")
    out("session %s:" % report.get("session_id"))
    out("  segments: %s received / %s expected"
        % (report.get("received_segments"), "?" if expected is None else expected))
    for leg in report.get("emit_leg", []):
        out("  %s: %s chunks emitted, %s failed"
            % (leg.get("modality"), leg.get("chunks_emitted"), leg.get("failed")))
    out("  client-leg missing: %s" % report.get("client_leg", {}).get("missing_count", 0))
    out("  verdict: %s" % report.get("verdict"))


def await_final_report(server, session_id, *, timeout_s=120.0, get=None,
                       sleep=time.sleep, clock=time.monotonic, out=print) -> int:
    """Poll every 2 s until the verdict is terminal AND the server has drained
    (ended, verdict != recording, segment_states.received == 0), or timeout.
    Returns the process exit code: 0 clean / 2 gaps / 1 error-or-timeout."""
    deadline = clock() + timeout_s
    last = None
    while True:
        try:
            last = poll_report(server, session_id, get=get)
        except Exception as exc:
            out("report poll failed (%s); retrying" % exc)
        else:
            if (last.get("ended") and last.get("verdict") != "recording"
                    and last.get("segment_states", {}).get("received", 1) == 0):
                break
        if clock() >= deadline:
            out("report did not settle within %.0fs — check GET "
                "%s/capture/sessions/%s/report" % (timeout_s, server.rstrip("/"), session_id))
            if last is not None:
                print_report_summary(last, out=out)
            return 1
        sleep(REPORT_POLL_S)
    print_report_summary(last, out=out)
    return {"clean": 0, "gaps": 2}.get(last.get("verdict"), 1)


# ---------------------------------------------------------------- record cmd

def _stop_ffmpeg(proc) -> None:
    """Graceful close: 'q' on stdin lets ffmpeg finalize the open mp4 (clean
    moov); SIGINT is the fallback, kill the last resort."""
    try:
        proc.stdin.write(b"q")
        proc.stdin.flush()
    except (OSError, ValueError, AttributeError):
        pass
    try:
        proc.wait(timeout=10)
        return
    except subprocess.TimeoutExpired:
        pass
    proc.send_signal(signal.SIGINT)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def cmd_record(args) -> int:
    if sys.platform != "darwin" and args.source != "test":
        _stderr("record: the default source is avfoundation, which exists only on "
                "macOS.\nOn this OS use `record --source test` (synthetic A/V through "
                "the same segment/upload\npath) — the real screen+mic leg needs a mac.")
        return 2
    ffmpeg_bin = shutil.which(os.environ.get("FFMPEG_BIN", "ffmpeg"))
    ffprobe_bin = shutil.which(os.environ.get("FFPROBE_BIN", "ffprobe"))
    if not ffmpeg_bin or not ffprobe_bin:
        _stderr("record: ffmpeg/ffprobe not on PATH (macOS: brew install ffmpeg)")
        return 1

    spool_minted = args.spool is None
    spool = Path(tempfile.mkdtemp(prefix="nucleus-spool-") if spool_minted else args.spool)
    spool.mkdir(parents=True, exist_ok=True)
    if not spool_minted:
        stale = uploadable_segments(os.listdir(spool), True)
        if stale:
            _stderr("record: spool %s already holds %d seg-*.mp4 file(s) from a "
                    "previous run — the watcher cannot tell them from THIS run's "
                    "output and would upload them into the new session. Empty the "
                    "directory (or omit --spool for a fresh temp dir) and re-run."
                    % (spool, len(stale)))
            return 1

    session_id = new_session_id()
    device_id = load_device_id()
    cfg = CaptureConfig(
        source=args.source, spool_dir=str(spool),
        screen_index=args.screen_index, audio_index=args.audio_index,
        framerate=args.framerate, max_width=args.max_width,
        segment_seconds=args.segment_seconds, duration=args.duration,
        video_encoder=pick_video_encoder(probe_encoders(ffmpeg_bin)),
        capture_cursor=not args.no_cursor, ffmpeg_bin=ffmpeg_bin,
    )
    uploader = SegmentUploader(args.server, session_id, args.user, device_id,
                               keep_segments=args.keep_segments)
    print("session %s" % session_id)  # full id: it IS the "new session" signal
    print("  device %s  user %s" % (device_id, args.user))
    print("  spool  %s" % spool)
    print("  server %s  (~%ds segments; Ctrl-C to stop)"
          % (uploader.server, args.segment_seconds), flush=True)

    spawn_ms = int(time.time() * 1000)
    proc = subprocess.Popen(build_ffmpeg_argv(cfg), stdin=subprocess.PIPE,
                            start_new_session=True)  # SIGINT stays ours to route

    # Per-run chaining state (D-F2): the anchor plus per-seq ffprobe durations;
    # seq n's stamps derive from anchor + durations[0..n].
    anchor_ms = None
    durations = []
    done = set()

    def process_ready(ffmpeg_exited: bool) -> None:
        nonlocal anchor_ms
        for seq, name in uploadable_segments(os.listdir(spool), ffmpeg_exited):
            if seq in done:
                continue
            path = spool / name
            if anchor_ms is None:
                st = os.stat(path)
                # macOS provides file birth time (capture start); elsewhere the
                # ffmpeg spawn wall-clock is the honest fallback.
                anchor_ms = (int(st.st_birthtime * 1000)
                             if hasattr(st, "st_birthtime") else spawn_ms)
            try:
                dur = probe_duration_s(ffprobe_bin, path)
            except (ValueError, OSError):
                dur = float(cfg.segment_seconds)
            slot_duration(durations, seq, dur, cfg.segment_seconds)
            t_start, t_end = chain_stamps(anchor_ms, durations)[seq]
            size = path.stat().st_size
            if uploader.upload(path, seq, t_start, t_end, mime="video/mp4"):
                print("  seg %d  %s -> %s  (%d bytes)" % (seq, t_start, t_end, size),
                      flush=True)
            done.add(seq)

    def abandon(signum=None, frame=None):
        try:
            proc.kill()
        except OSError:
            pass
        _stderr("\nabandoned: session %s was NOT terminated cleanly — the server "
                "ledger will flag it unterminated.\n  spool (any unsent segments): %s\n"
                "  report: GET %s/capture/sessions/%s/report"
                % (session_id, spool, uploader.server, session_id))
        os._exit(1)

    try:
        try:
            while proc.poll() is None:
                process_ready(ffmpeg_exited=False)
                time.sleep(SPOOL_POLL_S)
        except KeyboardInterrupt:
            # First Ctrl-C: graceful stop. From here a second Ctrl-C abandons.
            signal.signal(signal.SIGINT, abandon)
            print("\nstopping capture (Ctrl-C again to abandon)...", flush=True)
            _stop_ffmpeg(proc)
        else:
            signal.signal(signal.SIGINT, abandon)  # drain window: Ctrl-C = abandon

        process_ready(ffmpeg_exited=True)  # the tail: all on-disk files are final
        if not done:
            # No end marker, no report poll: zero segments means the server never
            # opened this session — the end POST would 404 and the report poll
            # would spin against a session that does not exist.
            if proc.returncode != 0:
                _stderr("ffmpeg exited with %d before producing any segment — nothing "
                        "captured.\n(macOS: check the Screen Recording permission and the "
                        "indices via list-devices;\nif the error names the framerate, retry "
                        "with --framerate 30)" % proc.returncode)
            else:
                _stderr("ffmpeg exited cleanly but produced no segments — nothing was "
                        "captured, so session %s never reached the server (nothing to "
                        "report)." % session_id)
            return 1

        last_seq = max(done) if done else -1
        uploader.end(last_seq)
        print("uploaded %d, dropped %d; waiting for the server's continuity report..."
              % (uploader.uploaded, uploader.dropped), flush=True)

        def poll_interrupted(signum=None, frame=None):
            _stderr("\nreport poll interrupted (session ended cleanly) — check GET "
                    "%s/capture/sessions/%s/report" % (uploader.server, session_id))
            os._exit(1)
        signal.signal(signal.SIGINT, poll_interrupted)

        code = await_final_report(uploader.server, session_id,
                                  timeout_s=args.report_timeout)
        if spool_minted:
            try:
                spool.rmdir()  # only when empty — never delete unsent evidence
            except OSError:
                pass
        return code
    finally:
        if proc.poll() is None:
            proc.kill()


# ----------------------------------------------------------- list-devices cmd

def cmd_list_devices(args) -> int:
    if sys.platform != "darwin":
        print('list-devices is a doc command for macOS: it wraps\n'
              '  ffmpeg -f avfoundation -list_devices true -i ""\n'
              "and prints the AVFoundation device table (the indices for "
              "--screen-index /\n--audio-index). avfoundation does not exist on this "
              "OS — run it on the mac.")
        return 0
    ffmpeg_bin = shutil.which(os.environ.get("FFMPEG_BIN", "ffmpeg"))
    if not ffmpeg_bin:
        _stderr("list-devices: ffmpeg not on PATH (brew install ffmpeg)")
        return 1
    proc = subprocess.run(
        [ffmpeg_bin, "-hide_banner", "-f", "avfoundation",
         "-list_devices", "true", "-i", ""],
        capture_output=True, text=True,
    )
    sys.stdout.write(proc.stderr)  # the device table IS ffmpeg's stderr; verbatim
    return 0


# ----------------------------------------------------------------------- main

_EPILOG = """\
macOS one-time setup:
  brew install ffmpeg
  Screen Recording permission goes to whatever LAUNCHES ffmpeg — your terminal:
  System Settings > Privacy & Security > Screen Recording > enable Terminal /
  iTerm2 / VS Code, then QUIT AND REOPEN the terminal. Until then avfoundation
  captures black frames or fails with 'Operation not permitted'. Microphone
  permission prompts inline on first run.

devices:
  nucleus_capture.py list-devices   # the avfoundation device table; indices
                                    # shift as cameras/mics attach. Defaults
                                    # assume the common laptop layout
                                    # (--screen-index 1 --audio-index 0).
flags of note:
  --max-width 1728    retina screens deliver PIXEL (2x) resolution; the
                      downscale keeps bitrate sane
  --framerate 15      if ffmpeg refuses ('Selected framerate ... is not
                      supported'), retry with --framerate 30

smoke test (no permissions, works off-mac too):
  nucleus_capture.py record --source test --duration 25 --server http://localhost:8084

Exit codes: 0 verdict clean / 2 verdict gaps / 1 error or report timeout.
Full runbook: handoff/ws-f-mac-cli.md
"""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="nucleus_capture.py",
        description="Nucleus mac capture CLI: screen+mic -> ~10s segments -> "
                    "recording server (WS-F)",
        epilog=_EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    rec = sub.add_parser(
        "record", help="capture and upload until Ctrl-C (or --duration)",
        epilog=_EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    rec.add_argument("--server", default=DEFAULT_SERVER,
                     help="recording server base URL (default %(default)s)")
    rec.add_argument("--user", default="beta-user", help="user_id on the wire")
    rec.add_argument("--source", choices=("avfoundation", "test"),
                     default="avfoundation",
                     help="test = lavfi testsrc2+sine through the same path (D-F3)")
    rec.add_argument("--screen-index", type=int, default=1,
                     help="avfoundation screen device index (default %(default)s)")
    rec.add_argument("--audio-index", type=int, default=0,
                     help="avfoundation audio device index (default %(default)s)")
    rec.add_argument("--framerate", type=int, default=15,
                     help="capture framerate; retry 30 if refused (default %(default)s)")
    rec.add_argument("--max-width", type=int, default=1728,
                     help="downscale bound for retina captures (default %(default)s)")
    rec.add_argument("--segment-seconds", type=int, default=10,
                     help="segment length (default %(default)s)")
    rec.add_argument("--duration", type=float, default=None,
                     help="stop after N seconds (mainly test mode); ends gracefully")
    rec.add_argument("--spool", default=None,
                     help="segment spool dir (default: a fresh temp dir)")
    rec.add_argument("--keep-segments", action="store_true",
                     help="keep spooled segment files after their upload ack")
    rec.add_argument("--no-cursor", action="store_true",
                     help="omit the cursor from the screen capture")
    rec.add_argument("--report-timeout", type=float, default=120.0, metavar="S",
                     help="report-poll bound at stop (default %(default)ss)")
    rec.set_defaults(func=cmd_record)

    ld = sub.add_parser("list-devices",
                        help="print avfoundation's device table (find the indices)")
    ld.set_defaults(func=cmd_list_devices)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
