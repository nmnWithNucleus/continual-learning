"""Node-7 smoke test of the REAL audio backends (pyannote / whisper-translate / AST).

Runs the ACTUAL app.audio backend code (not a reimplementation) against a real
speech chunk (sample_jfk.webm, opus/webm — the extension capture codec), so it
exercises the true decode + model + API-shape paths the seam claims are
"correct-by-inspection". Each backend is isolated in try/except and reports
pass/fail + a short evidence sample. Never fabricates a result: a failure is
printed as FAIL with the exception.

Usage (from the data-processing service root, in an env with the heavy deps):
    HF_TOKEN=... PYTHONPATH=. python scripts/smoke_audio_backends.py <audio.webm> [codec]
"""
from __future__ import annotations

import os
import sys
import time
import traceback

# Real DP code under test.
from app.config import get_settings
from app.audio.config import get_audio_config


def _banner(name: str) -> None:
    print(f"\n{'='*70}\n{name}\n{'='*70}", flush=True)


def main() -> int:
    audio_path = sys.argv[1] if len(sys.argv) > 1 else "sample_jfk.webm"
    codec = sys.argv[2] if len(sys.argv) > 2 else "audio/webm"
    with open(audio_path, "rb") as fh:
        blob = fh.read()
    print(f"audio={audio_path} bytes={len(blob)} codec={codec}", flush=True)

    import torch  # noqa
    print(f"torch={torch.__version__} cuda={torch.cuda.is_available()} "
          f"devices={torch.cuda.device_count()}", flush=True)
    import importlib.metadata as md
    for pkg in ("pyannote.audio", "faster-whisper", "transformers"):
        try:
            print(f"  {pkg}=={md.version(pkg)}", flush=True)
        except Exception:
            print(f"  {pkg}=MISSING", flush=True)

    results: dict[str, str] = {}

    # A shared ASR result is needed by ASR + translate stages; span from the file.
    span = float(os.getenv("SMOKE_SPAN_S", "11.0"))

    # ---- 1. ASR (faster_whisper) — the base transcript the pipeline builds on ----
    _banner("1. ASR (faster_whisper) — base transcript")
    asr_result = None
    try:
        os.environ["ASR_BACKEND"] = "faster_whisper"
        os.environ.setdefault("ASR_LANGUAGE", "en")
        from app.asr import select as select_asr
        settings = get_settings()
        t0 = time.time()
        asr_result = select_asr(settings).transcribe(
            settings, blob, codec, span, "smoke-chunk-jfk"
        )
        dt = time.time() - t0
        print(f"OK  {dt:.1f}s  language={asr_result.language} "
              f"segments={len(asr_result.segments)}", flush=True)
        print(f"    text: {asr_result.text[:200]!r}", flush=True)
        results["asr"] = f"PASS ({dt:.1f}s)"
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        results["asr"] = f"FAIL: {type(exc).__name__}: {exc}"

    # ---- 2. Diarization (pyannote 3.1) ----
    _banner("2. Diarization (pyannote/speaker-diarization-3.1)")
    try:
        os.environ["DIARIZE_BACKEND"] = "pyannote"
        cfg = get_audio_config()
        from app.audio import diarize
        backend = diarize.select(cfg)
        assert backend is not None, "diarize.select returned None under DIARIZE_BACKEND=pyannote"
        t0 = time.time()
        dres = backend.diarize(blob, codec, span, cfg)
        dt = time.time() - t0
        speakers = sorted({t.speaker for t in dres.turns})
        print(f"OK  {dt:.1f}s  turns={len(dres.turns)} speakers={speakers}", flush=True)
        for turn in dres.turns[:6]:
            print(f"    [{turn.start_s:6.2f}, {turn.end_s:6.2f}] {turn.speaker}", flush=True)
        # Exercise the assign step the processor uses.
        if asr_result is not None:
            from app.audio.diarize.assign import assign_speakers
            from app.timeutil import abs_time
            from datetime import datetime, timezone
            base = datetime(2026, 7, 9, 12, 0, 0, tzinfo=timezone.utc)
            abs_segs = [
                {"t_start": abs_time(base, s.start_s), "t_end": abs_time(base, s.end_s),
                 "text": s.text, "speaker": None}
                for s in asr_result.segments
            ]
            spk = assign_speakers(asr_result.segments, abs_segs, dres)
            print(f"    enrichments.speakers={spk}", flush=True)
        results["diarize"] = f"PASS ({dt:.1f}s, {len(dres.turns)} turns, {len(speakers)} spk)"
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        results["diarize"] = f"FAIL: {type(exc).__name__}: {exc}"

    # ---- 3. Translation (faster-whisper task=translate, X->English) ----
    _banner("3. Translation (whisper task=translate)")
    try:
        os.environ["ASR_BACKEND"] = "faster_whisper"
        os.environ["TRANSLATE_BACKEND"] = "whisper"
        os.environ["TRANSLATE_TARGET"] = "en"
        settings = get_settings()
        from app.audio.translate import whisper as wh
        assert asr_result is not None, "need an ASR result to feed the translate stage"
        t0 = time.time()
        tres = wh.translate(settings, blob, span, asr_result, "en")
        dt = time.time() - t0
        print(f"OK  {dt:.1f}s  out-language={tres.language} segments={len(tres.segments)}", flush=True)
        print(f"    text: {tres.text[:200]!r}", flush=True)
        note = "(JFK is English -> X->En translate is ~identity; proves the seam runs)"
        results["translate"] = f"PASS ({dt:.1f}s) {note}"
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        results["translate"] = f"FAIL: {type(exc).__name__}: {exc}"

    # ---- 4. Acoustic events (AST AudioSet tagger) ----
    _banner("4. Acoustic events (MIT/ast-finetuned-audioset)")
    try:
        os.environ["ACOUSTIC_BACKEND"] = "ast"
        cfg = get_audio_config()
        from app.audio import acoustic
        backend = acoustic.select(cfg)
        assert backend is not None, "acoustic.select returned None under ACOUSTIC_BACKEND=ast"
        t0 = time.time()
        ares = backend.caption(blob, codec, span, cfg, "smoke-chunk-jfk")
        dt = time.time() - t0
        print(f"OK  {dt:.1f}s  caption={ares.text!r}", flush=True)
        results["acoustic"] = f"PASS ({dt:.1f}s) caption={ares.text!r}"
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        results["acoustic"] = f"FAIL: {type(exc).__name__}: {exc}"

    # ---- Summary ----
    _banner("SUMMARY")
    for k in ("asr", "diarize", "translate", "acoustic"):
        print(f"  {k:10s} {results.get(k, 'NOT RUN')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
