# servers/whisper — faster-whisper behind the model-server seam

Serves `Systran/faster-whisper-large-v3` (CTranslate2, float16, CUDA) over the
`dp_servers_common` HTTP skeleton: `/health` (identity after warmup) and
`/infer`. ONE loaded model serves BOTH tasks — `task="transcribe"` (ASR) and
`task="translate"` (X→English) — mirroring the v0 semantics of
`app/asr/faster_whisper.py` + `app/audio/translate/whisper.py`: bytes decode via
av from the raw container, `vad_parameters={"min_silence_duration_ms": 500}`,
chunk-relative segment seconds, text = joined stripped segment texts.

## Identity (pinned IN CODE, L4 — no output-affecting env vars)
- model: `Systran/faster-whisper-large-v3`
- weights revision: `edaa852ec7e145841d8ffdb056a99866b5f0a478` (resolved via
  `snapshot_download` and passed as a local path — the revision pin holds
  regardless of WhisperModel kwargs)
- device: `cuda` (fail-loud if unavailable; no CPU fallback), `device_index=0`
  with `CUDA_VISIBLE_DEVICES` doing the physical pinning (manifest: GPUs 4/5,
  ports 8121/8122)
- compute type: `float16`
- frameworks: faster-whisper 1.2.1, ctranslate2 4.8.1, av 18.0.0

## /infer params (all optional; defaults pinned in code)
`task` ("transcribe"|"translate", default transcribe) · `beam_size` (int ≥ 1,
default 1) · `language` (str|null, default null = auto-detect) · `vad` (bool,
default true). Unknown params → deterministic 422 (params are behavior; typos
fail loud). `input_b64` = raw audio container bytes; `codec` informational.

Result: `{"text": str, "language": str, "segments": [{"start_s", "end_s",
"text"}]}` — chunk-relative seconds, mirroring `AsrResult`.

## Venv setup
```
cd servers/whisper
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```
The venv's nvidia cuBLAS/cuDNN-9 wheels are preloaded by `server.py` via
`ctypes.CDLL(..., RTLD_GLOBAL)` — no `LD_LIBRARY_PATH` needed.

## Determinism verdict
Golden transcribe output is **bit-stable across 4 fresh-process runs, GPUs 4
and 5** (beam_size=1, language=en, vad on). The golden test is an exact
compare; see `tests/fixtures/PROVENANCE.md`.

## Tests (in-process TestClient; no ports bound)
```
cd servers/whisper
CUDA_VISIBLE_DEVICES=4 ./.venv/bin/python -m pytest tests/ -q
```
Covers: warming 503 → ready 200 conformance, identity subset-match against
`servers/manifest.json` `expected_identity` + full shape, golden exact compare,
translate-task smoke from the same loaded model, garbage input → 422,
unknown param → 422.

## Run (the supervisor does this; operational env only)
```
CUDA_VISIBLE_DEVICES=4 DP_SERVER_PORT=8121 ./.venv/bin/python server.py
```
