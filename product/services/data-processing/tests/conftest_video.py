"""ffmpeg-lavfi fixture builders for the clip video path (WS-B).

NOT named ``conftest.py`` on purpose — pytest auto-loads only ``conftest.py``, so nothing
here runs unless a test imports it. Every fixture is generated at test time by
``ffmpeg lavfi`` (house rule #5: NO binaries committed, NO GPU, NO network) and cached in
this process so the whole calibration set builds once in a few seconds.

Resolution is pinned to 1440x900: the D-04 area-downscale floor is a content-independent
2/255 across the ~1.15–2.3 Mpx band (measured 1 below 1280x800, 3 above 1920x1200), and
1440x900 sits solidly mid-band, so ``floor == 2`` is a robust CI assertion on this ffmpeg.

The six calibration clips reproduce the D-04 *content classes* (idle / typing / layout /
switch), not the exact real-capture magnitudes (those are O-1 measurements on real screen
footage, explicitly a pending open question). Each clip's measured signature on this ffmpeg
build is recorded beside its builder.
"""
from __future__ import annotations

import shutil
import subprocess
from datetime import datetime, timezone

FIXTURE_W = 1440
FIXTURE_H = 900
FIXTURE_FPS = 30
DEFAULT_DUR = 10

_FFMPEG = shutil.which("ffmpeg")
_CACHE: dict[str, bytes] = {}
_FONT_OK: bool | None = None


def ffmpeg_available() -> bool:
    return _FFMPEG is not None


def font_available() -> bool:
    """Can ``drawtext`` render (libfreetype + a fontconfig default)? The typing/fast-typing
    calibration clips need it; the flat/scroll/switch clips do not, so a font-less box still
    exercises the floor + switch + determinism guarantees."""
    global _FONT_OK
    if _FONT_OK is not None:
        return _FONT_OK
    if not _FFMPEG:
        _FONT_OK = False
        return False
    p = subprocess.run(
        [_FFMPEG, "-v", "error", "-f", "lavfi", "-i", "color=c=white:s=64x64:r=1:d=1",
         "-vf", "drawtext=text='x':x=1:y=1:fontsize=12:fontcolor=black",
         "-frames:v", "1", "-f", "null", "-"],
        capture_output=True,
    )
    _FONT_OK = p.returncode == 0
    return _FONT_OK


def _encode(vf: str, *, dur: int = DEFAULT_DUR, src: str | None = None) -> bytes:
    """Encode one lavfi clip to h264/mp4 bytes (the shape a real screen chunk arrives in)."""
    if not _FFMPEG:  # pragma: no cover - guarded by ffmpeg_available()
        raise RuntimeError("ffmpeg is not on PATH")
    source = src or f"color=c=black:s={FIXTURE_W}x{FIXTURE_H}:r={FIXTURE_FPS}:d={dur}"
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory(prefix="clipfx-") as tmp:
        out = str(Path(tmp) / "fx.mp4")
        cmd = [_FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", source]
        if vf:
            cmd += ["-vf", vf]
        cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", str(dur), out]
        subprocess.run(cmd, check=True, capture_output=True)
        return Path(out).read_bytes()


def _cached(key: str, build) -> bytes:
    if key not in _CACHE:
        _CACHE[key] = build()
    return _CACHE[key]


# --- The calibration clips (measured signatures @ ffmpeg 7.1, 1440x900, period 2.0) -----

def flat_clip(color: str = "black", dur: int = DEFAULT_DUR) -> bytes:
    """Flat colour -> every delta is the pure floor: peak 2, spread 0."""
    return _cached(f"flat:{color}:{dur}",
                   lambda: _encode("", dur=dur,
                                   src=f"color=c={color}:s={FIXTURE_W}x{FIXTURE_H}:r={FIXTURE_FPS}:d={dur}"))


def caret_clip(dur: int = DEFAULT_DUR) -> bytes:
    """Static gray screen whose only motion is a caret blinking at 1 Hz. Sampled every
    ``VIDEO_ANALYSIS_PERIOD_S`` (2.0 s), consecutive analysis frames catch the caret in the
    SAME phase, so each delta pair is effectively unchanged -> the floor -> IDLE (measured
    peak 2). This is the honest thing the vector proves: a screen with no *between-sample*
    change classifies IDLE. (A caret whose blink did straddle a sample boundary would toggle
    a whole downscaled cell and read far above IDLE_PEAK — i.e. the design's "static + caret
    -> peak 2-4" is itself a same-phase / sub-cell artefact, not area-suppression of motion.)"""
    return _cached(f"caret:{dur}",
                   lambda: _encode(
                       "drawbox=x=200:y=300:w=8:h=30:color=black:t=fill:enable='lt(mod(t\\,1)\\,0.5)'",
                       dur=dur, src=f"color=c=gray:s={FIXTURE_W}x{FIXTURE_H}:r={FIXTURE_FPS}:d={dur}"))


def typing_clip(dur: int = DEFAULT_DUR) -> bytes:
    """~40 wpm typing (a growing prefix over 33 ``between()`` windows) -> TEXT class:
    measured single-delta peak 18–32, spread 1–2, while the WHOLE-FRAME mean abs-diff is 0
    (the D-04 claim: a mean is blind to typing; binarize-then-max recovers it)."""
    def build():
        txt = "the quick brown fox jumps over the lazy dog while typing slowly today"
        n = min(33, len(txt))
        layers = []
        for i in range(1, n + 1):
            a = round((i - 1) * dur / n, 3)
            b = round(i * dur / n, 3)
            pre = txt[:i].replace("'", "").replace(":", "")
            layers.append(
                f"drawtext=text='{pre}':x=100:y=430:fontsize=14:fontcolor=black:"
                f"enable='between(t\\,{a}\\,{b})'")
        return _encode(",".join(layers), dur=dur,
                       src=f"color=c=white:s={FIXTURE_W}x{FIXTURE_H}:r={FIXTURE_FPS}:d={dur}")
    return _cached(f"typing:{dur}", build)


def fasttype_clip(dur: int = DEFAULT_DUR) -> bytes:
    """Fast typing across 3 growing lines -> LAYOUT class: measured peak 51–58, spread
    11–15 (a wider, faster change than slow typing)."""
    def build():
        lines = [
            "def process(items): return [transform(x) for x in items if valid(x)]",
            "SELECT id name email FROM users WHERE active = 1 ORDER BY created",
            "git commit -m fix the parser bug in the tokenizer module today now",
        ]
        N = 45
        layers = []
        for li, tx in enumerate(lines):
            y = 300 + li * 70
            for i in range(1, N + 1):
                a = round((i - 1) * dur / N, 3)
                b = round(i * dur / N, 3)
                pre = tx[:min(i, len(tx))].replace("'", "").replace(":", "")
                layers.append(
                    f"drawtext=text='{pre}':x=60:y={y}:fontsize=20:fontcolor=black:"
                    f"enable='between(t\\,{a}\\,{b})'")
        return _encode(",".join(layers), dur=dur,
                       src=f"color=c=white:s={FIXTURE_W}x{FIXTURE_H}:r={FIXTURE_FPS}:d={dur}")
    return _cached(f"fasttype:{dur}", build)


def scroll_clip(dur: int = DEFAULT_DUR) -> bytes:
    """A fine diagonal pattern scrolled vertically -> LAYOUT, SUSTAINED across every delta:
    measured peak 255, spread 1024 (the whole frame changes each interval). Distinct from
    an app switch, which is a SINGLE saturated delta with floor neighbours."""
    return _cached(f"scroll:{dur}",
                   lambda: _encode("geq=lum='mod(Y*3+X\\,256)',scroll=vertical=0.02",
                                   dur=dur, src=f"color=c=gray:s={FIXTURE_W}x{FIXTURE_H}:r={FIXTURE_FPS}:d={dur}"))


def appswitch_clip(dur: int = DEFAULT_DUR) -> bytes:
    """A hard cut black->white at the midpoint -> ONE saturated delta (peak 255, spread
    1024) at the interval spanning the cut, floor (peak 2) on the others."""
    def build():
        half = dur // 2
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory(prefix="clipfx-") as tmp:
            a = str(Path(tmp) / "a.mp4")
            b = str(Path(tmp) / "b.mp4")
            for path, color, d in ((a, "black", half), (b, "white", dur - half)):
                subprocess.run(
                    [_FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
                     f"color=c={color}:s={FIXTURE_W}x{FIXTURE_H}:r={FIXTURE_FPS}:d={d}",
                     "-c:v", "libx264", "-pix_fmt", "yuv420p", path],
                    check=True, capture_output=True)
            listf = str(Path(tmp) / "list.txt")
            Path(listf).write_text(f"file '{a}'\nfile '{b}'\n")
            out = str(Path(tmp) / "sw.mp4")
            subprocess.run(
                [_FFMPEG, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", listf,
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", out],
                check=True, capture_output=True)
            return Path(out).read_bytes()
    return _cached(f"appswitch:{dur}", build)


def vfr_clip() -> bytes:
    """A VALID variable-frame-rate clip: sparse frames at irregular PTS (0,1,2.52,5,9,10 s),
    like macOS ScreenCaptureKit emitting only on change. mp4 pins a constant timebase, so
    duration_time reports the tick (=1/25) not the real gap — a rate-derived frame index
    (N=round(t*fps)) lands far past the last real frame. Regression guard for the CFR
    poison-pill: keep only 6 of a 25fps grid's frames with ``-fps_mode passthrough`` so the
    output is genuinely VFR; clipprep must decode it, not dead-letter it."""
    def build():
        if not _FFMPEG:  # pragma: no cover
            raise RuntimeError("ffmpeg is not on PATH")
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory(prefix="clipfx-") as tmp:
            out = str(Path(tmp) / "vfr.mp4")
            subprocess.run(
                [_FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
                 f"color=c=black:s={FIXTURE_W}x{FIXTURE_H}:r=25:d=11",
                 "-vf", "select='eq(n,0)+eq(n,25)+eq(n,63)+eq(n,125)+eq(n,225)+eq(n,250)'",
                 "-fps_mode", "passthrough", "-c:v", "libx264", "-pix_fmt", "yuv420p", out],
                check=True, capture_output=True)
            return Path(out).read_bytes()
    return _cached("vfr", build)


# --- C1 envelope helpers ----------------------------------------------------------------

def iso(epoch: float) -> str:
    """Epoch seconds -> an RFC3339 'Z' stamp (the form recording writes)."""
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def make_c1(*, t_start_epoch: float = 1_700_000_000.0, span_seconds: float = 10.0,
            chunk_id: str = "chunk-clip-test-0001") -> dict:
    """A minimal C1 envelope carrying just what ``clipprep`` reads (``t_start``,
    ``chunk_id``) plus the standard provenance fields, so a StageContext looks real."""
    return {
        "contract": "C1", "version": "0",
        "user_id": "pilot-user", "device_id": "screen-1",
        "stream_id": "stream-clip-test", "sequence": 0,
        "chunk_id": chunk_id, "modality": "video", "codec": "video/mp4",
        "t_start": iso(t_start_epoch),
        "t_end": iso(t_start_epoch + span_seconds),
        "blob_ref": f"raw/pilot-user/{chunk_id}.mp4",
    }
