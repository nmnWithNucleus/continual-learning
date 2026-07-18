# WS4 — ASR module (faster-whisper)

Status: **done** · Owner/agent: WS4 build agent · Last updated: 2026-06-30

> **Start here:** read the global [`../HANDOFF.md`](../HANDOFF.md) in full, then this file. You produce
> a small module that WS2 imports. You can build and benchmark **fully independently** with sample audio.
> Keep the Worklog current; flip your status row when done.

## Goal
A drop-in **speech-to-text** function: given an audio blob recorded in the browser, return the
transcribed text, fast enough that it doesn't bottleneck the turn. "Basic version is okay."

## Deliverables (in `backend/`)
- `asr.py` exposing **`transcribe(audio_bytes: bytes, mime: str | None = None) -> str`** (sync is fine;
  WS2 will call it off the event loop). Loads the model once (module-level singleton).
- A tiny `bench_asr.py` (or notes in this file) with the chosen model size + measured latency on a few
  sample clips, and why you chose that size.
- Install notes (faster-whisper is in the `moe` env; confirm versions / model download path).

## Interface contract (WS2 depends on exactly this)
```python
# backend/asr.py
def transcribe(audio_bytes: bytes, mime: str | None = None) -> str:
    """Browser audio blob -> text. Handles iOS audio/mp4 (AAC) and webm/opus."""
```
- Input is whatever `MediaRecorder` produces: on **iOS Safari it's `audio/mp4` (AAC)**; elsewhere it may
  be `audio/webm` (Opus). Your function must accept both — decode with ffmpeg if needed (ffmpeg 7.1 is in
  the env) into 16 kHz mono PCM/WAV before feeding faster-whisper.
- Return plain text (strip leading/trailing whitespace). Empty/garbled audio → return `""`.

## Suggested steps
1. Pick a model size for **latency**: start with `small` or `medium` (English) on GPU; the question
   audio is short (a few seconds), so transcription should be well under a second. Use `int8_float16` or
   `float16` compute type on H100. (You can share WS1's node or run CPU/another GPU — it's lightweight.)
2. Implement the ffmpeg decode → faster-whisper transcribe path; handle both mp4/aac and webm/opus.
3. Record a few sample question clips (or synthesize), measure latency + sanity-check accuracy.
4. Hand WS2 the import + a one-line usage example; they wire it into `/api/transcribe`.

## Key files & paths
- `backend/asr.py`, `backend/bench_asr.py`. Sample audio under `backend/` or scratch.
- faster-whisper model cache: reuse `/mnt/localssd/.hf-home/` or its default cache.

## Gotchas / decisions
- **Decode first.** Don't assume faster-whisper can read an iOS `audio/mp4` blob directly — pipe through
  ffmpeg to 16 kHz mono WAV.
- Keep the model **loaded once** (cold load is seconds; per-call load would kill latency).
- Default to English for V0 unless told otherwise; expose a `language` kwarg if trivial.
- This module is GPU-light — it can run on the same node as WS1/WS2 without its own allocation.

## Definition of done
`transcribe()` returns correct text for both iOS `audio/mp4` and `audio/webm` sample blobs, model loads
once, latency measured and acceptable (sub-second target for short clips), WS2 has wired it into
`/api/transcribe`.

## ✅ Delivered — summary for WS2 / WS6

**Built (all in `backend/`):**
- `asr.py` — exposes `transcribe(audio_bytes: bytes, mime: str | None = None) -> str` (the exact
  contract) plus an optional `warmup()`. ffmpeg-decode-first → faster-whisper. Model is a
  thread-safe module-level singleton.
- `bench_asr.py` — self-contained validation harness (regenerates sample blobs if missing).
- `requirements-asr.txt` — `faster-whisper==1.1.0`, `ctranslate2==4.7.2`, `numpy`. (System dep:
  ffmpeg ≥7, already in `moe`.)
- Sample fixtures `sample_jfk.{mp4,webm}`, `sample_short.{m4a,webm}` (~300 KB total) — AAC-in-mp4
  (iOS) and Opus-in-webm (desktop) blobs that mimic the browser's MediaRecorder output.

**Chosen model: `small.en`** (faster-whisper / Systran CT2 weights). Lowest latency + smallest
footprint while transcribing short English questions perfectly. float16 on CUDA, int8 on CPU
fallback. Override via env: `ASR_MODEL` (e.g. `medium.en`, `distil-large-v3`, `large-v3-turbo` for
multilingual/accents), `ASR_DEVICE`, `ASR_DEVICE_INDEX`, `ASR_COMPUTE_TYPE`, `ASR_LANGUAGE`.

**Measured (one idle H100, `small.en` float16, after warmup):**
| clip | format | warm latency |
|---|---|---|
| 11 s JFK | audio/mp4 (AAC) | ~236 ms |
| 11 s JFK | audio/webm (Opus) | ~241 ms |
| 6 s | audio/mp4 (AAC) | ~184 ms |
| 6 s | audio/webm (Opus) | ~189 ms |

Per call ≈ ffmpeg decode (~130–200 ms) + transcribe (~50–130 ms) → **sub-300 ms warm, well under
the sub-second target**. One-time model load ≈ 2–10 s (amortized; call `asr.warmup()` at startup).
Accuracy: exact on both samples. Empty/garbage/silence → `""` (ffmpeg yields no PCM, or VAD finds
no speech). All formats + edge cases: **ALL GREEN**.

### WS2 usage (one-liner)
```python
from asr import transcribe          # module lives in backend/, same dir as app.py

# in POST /api/transcribe, after reading the uploaded blob:
text = transcribe(audio_bytes, mime)   # mime e.g. "audio/mp4" (iOS) / "audio/webm"; advisory only
# -> "what is this thing"   (or ""  for empty/garbled audio)
```
- `transcribe()` is **sync + blocking** (and CPU/GPU-bound) → call it off the event loop, e.g.
  `text = await run_in_threadpool(transcribe, audio_bytes, mime)`. It is thread-safe (verified
  with 4 concurrent threads sharing the one model).
- Optional but recommended: `import asr; asr.warmup()` at FastAPI startup so the first real request
  doesn't pay the model-load cost.
- The HF model cache used in validation was `HF_HOME=/mnt/localssd/.hf-home` (per node-local
  convention). Default `~/.cache/huggingface` also works; first run downloads ~250 MB once.

### How to run / re-validate
```bash
conda activate moe
cd backend
export HF_HOME=/mnt/localssd/.hf-home   # optional, node-local cache
export ASR_DEVICE_INDEX=7               # optional: pin to last GPU, don't fight WS1
python bench_asr.py                     # prints latency table + ALL GREEN
```

### Notes / risks
- **Decode-first is load-bearing:** asr.py always pipes the raw blob through ffmpeg (stdin→stdout,
  no temp files) to 16 kHz mono f32 PCM before faster-whisper — this is what makes iOS `audio/mp4`
  (AAC) work reliably (faster-whisper's own av path is not trusted to demux the iOS container).
- GPU-light: pinned to GPU 7 in validation; `ASR_DEVICE=cpu` also works (int8) if WS1 wants every
  GPU. Auto-detects CUDA vs CPU at load.
- VAD (`vad_filter=True`, Silero) is on → silence/noise → `""`. First call lazy-loads the VAD model
  (~1 s); `warmup()` primes it.

## Worklog
- 2026-06-30 — file created (scaffolding). Not started.
- 2026-06-30 — Built asr.py / bench_asr.py / requirements-asr.txt. Verified ffmpeg 7.1 decodes
  AAC-in-mp4 and Opus-in-webm from stdin (~130–200 ms). Benchmarked small.en / medium.en /
  distil-large-v3 / large-v3-turbo — all sub-130 ms transcribe, all accurate; chose **small.en**
  for lowest latency + smallest footprint. Validated full `transcribe()` on iOS mp4/AAC + webm/Opus
  + empty/garbage: ALL GREEN, ~184–241 ms warm. Confirmed singleton (same model across calls +
  4 concurrent threads) and `warmup()`. Status → done.
