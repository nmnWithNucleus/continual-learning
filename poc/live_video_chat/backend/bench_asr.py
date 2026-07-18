"""
WS4 — ASR benchmark / validation harness.

Runs the real `transcribe()` from asr.py against browser-style audio blobs
(iOS audio/mp4 AAC and desktop audio/webm Opus), measuring:
  * one-time model load (cold) latency,
  * per-call transcription latency (warm), and
  * a sanity check of the returned text against expected content.

It also generates the sample blobs from the bundled WAVs if they are missing,
so this is self-contained: `python bench_asr.py`.

Sample sources (downloaded once into backend/, known ground-truth text):
  * sample_jfk.wav   — JFK inaugural line (~11 s)
  * sample_short.wav — physics-lecture excerpt (~6 s)
The browser-format derivatives are produced with ffmpeg:
  *.mp4 / *.m4a  -> AAC in MP4  (mimics iOS Safari MediaRecorder)
  *.webm         -> Opus in WebM (mimics Chrome/Firefox MediaRecorder)
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

# (file, mime, substring that MUST appear in the transcription)
CASES = [
    ("sample_jfk.mp4", "audio/mp4", "ask not what your country"),
    ("sample_jfk.webm", "audio/webm", "ask not what your country"),
    ("sample_short.m4a", "audio/mp4", "conservation of mechanical energy"),
    ("sample_short.webm", "audio/webm", "conservation of mechanical energy"),
]

# Remote WAVs with known content, used only to (re)generate the encoded blobs.
WAV_SOURCES = {
    "sample_jfk.wav": "https://github.com/ggerganov/whisper.cpp/raw/master/samples/jfk.wav",
    "sample_speech.wav": "https://github.com/SYSTRAN/faster-whisper/raw/master/tests/data/physicsworks.wav",
}


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def ensure_samples() -> None:
    """Make sure the four browser-format blobs exist; build them if not."""
    if all((HERE / f).exists() for f, _, _ in CASES):
        return

    # Fetch base WAVs if needed.
    for name, url in WAV_SOURCES.items():
        path = HERE / name
        if not path.exists():
            print(f"  downloading {name} ...")
            _run(["curl", "-sSfL", "-o", str(path), url])

    # Trim the long physics clip to a short ~6 s question-length sample.
    short_wav = HERE / "sample_short.wav"
    if not short_wav.exists():
        _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
              "-i", str(HERE / "sample_speech.wav"), "-t", "6", str(short_wav)])

    # Encode browser-style derivatives.
    enc = [
        ("sample_jfk.wav", "sample_jfk.mp4", "aac"),
        ("sample_jfk.wav", "sample_jfk.webm", "libopus"),
        ("sample_short.wav", "sample_short.m4a", "aac"),
        ("sample_short.wav", "sample_short.webm", "libopus"),
    ]
    for src, dst, codec in enc:
        dst_path = HERE / dst
        if dst_path.exists():
            continue
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
               "-i", str(HERE / src), "-c:a", codec]
        if codec == "aac":
            cmd += ["-b:a", "96k", "-movflags", "+faststart"]
        else:
            cmd += ["-b:a", "64k"]
        cmd.append(str(dst_path))
        _run(cmd)
        print(f"  built {dst}")


def main() -> int:
    ensure_samples()

    import asr  # imported here so model env vars set above take effect

    print(f"Model: {asr._MODEL_NAME}")
    dev, ct, idx = asr._resolve_device_and_compute()
    print(f"Device: {dev} (compute={ct}, index={idx})\n")

    # --- cold load (first transcribe pays the load + VAD init) -------------
    first_file, first_mime, _ = CASES[0]
    blob = (HERE / first_file).read_bytes()
    t0 = time.time()
    _ = asr.transcribe(blob, first_mime)
    cold = time.time() - t0
    print(f"Cold first-call (model load + VAD init + transcribe): {cold:.2f}s\n")

    # --- warm per-call latency + accuracy ----------------------------------
    print(f"{'file':18s} {'mime':12s} {'lat(ms)':>8s}  ok  text")
    print("-" * 92)
    all_ok = True
    for fname, mime, expect in CASES:
        blob = (HERE / fname).read_bytes()
        lats = []
        text = ""
        for _ in range(3):
            t = time.time()
            text = asr.transcribe(blob, mime)
            lats.append((time.time() - t) * 1000)
        ok = expect.lower() in text.lower()
        all_ok = all_ok and ok
        flag = "OK" if ok else "!!"
        snippet = (text[:60] + "…") if len(text) > 61 else text
        print(f"{fname:18s} {mime:12s} {min(lats):8.0f}  {flag}  {snippet!r}")

    # --- edge cases: empty / garbage / silence -> "" -----------------------
    print("\nEdge cases (must return ''):")
    for label, data in [
        ("empty bytes", b""),
        ("garbage bytes", b"this is not audio" * 64),
    ]:
        out = asr.transcribe(data, None)
        ok = out == ""
        all_ok = all_ok and ok
        print(f"  {label:16s} -> {out!r:8s} {'OK' if ok else '!!'}")

    print("\nRESULT:", "ALL GREEN" if all_ok else "FAILURES present")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
