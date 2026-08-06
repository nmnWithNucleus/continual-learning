# DP Rebuild — Stage B worklog (Machinery)

**Stage:** B — Machinery · **Status:** IN_PROGRESS · *Dated:* 2026-08-06
**Branch:** `dp-rebuild-v1` · **Plan:** [refactor_dp_service.md](refactor_dp_service.md) §8 Stage B
**Scope:** WP-B1 (servers/common framework · `app/supervisor.py` · `app/model_client.py`),
WP-B2…B5 (whisper / pyannote / ast / ocr behind the server seam). The running v0 service
(port 8085) and the three existing suites stay byte-identical all stage.

---

## Pre-flight inventory (node-7, 2026-08-06)

- **Host** is `nucla3m-a3meganodeset-7` — the "node-7" the §8 exit criteria name.
- **Live processes, never touched:** storage `:8083` (pid 3356393) · recording `:8084` ·
  **DP v0 `:8085`** (pid 3356422, `platform/deploy/.venv-learn`, `ASR_BACKEND=faster_whisper
  ASR_LANGUAGE=en`, models load lazily — CPU, `base`/int8) · a second storage instance
  `:8099` · an **OCR sidecar `:8097`** running out of a *deleted* worktree directory
  (`~/nmn/vc-worktrees/ws-c/sidecars/ocr`) — extra reason it is untouchable · an
  `http.server` `:8799`. This repo's `sidecars/ocr` has **no venv and has never run
  `ppocr` mode on this box**; nothing launches it (deploy's service table has no OCR row).
- **Ports:** 8120–8159 verified free (`ss -tlnp`) and claimed for the fleet (manifest).
- **GPUs:** 8× H100 80GB, all 0 MiB used, no compute processes. GPUs 0–1 left free for
  v0/ad-hoc; fleet pins to GPUs 2–7. VRAM plan: whisper large-v3 fp16 ≈ 3–5 GB,
  pyannote ≈ 1–2 GB, AST ≈ 0.7 GB — one replica per GPU, each < 6 GB of 80 GB.
- **Stage A "Noticed for later stages"** carries no Stage-B instructions (its notes target
  C/E/F/G); the two uncommitted onboarding files from another session are left untouched.
- **HF cache** already holds faster-whisper-large-v3, pyannote diarization-3.1 +
  segmentation-3.0, and the AST checkpoint (revisions pinned in the manifest);
  pyannote's embedding model (`wespeaker-voxceleb-resnet34-LM`) is not yet cached and
  downloads at first load (network verified up; `HF_TOKEN` present in the ambient env —
  treated as a secret, never logged or committed).

## Port / GPU / VRAM allocation (also in `servers/manifest.json`)

| Server | Replica ports | GPUs | Device | Est. VRAM/replica | Model |
|---|---|---|---|---|---|
| whisper | 8121, 8122 | 4, 5 | cuda fp16 | ~5 GB | Systran/faster-whisper-large-v3 @ `edaa852e` |
| pyannote | 8131, 8132 | 2, 3 | cuda fp32 | ~2 GB | pyannote/speaker-diarization-3.1 @ `84fd2591` (+ segmentation-3.0 @ `e66f3d3b`) |
| ast | 8141, 8142 | 6, 7 | cuda fp32 | ~1 GB | MIT/ast-finetuned-audioset-10-10-0.4593 @ `f826b80d` |
| ocr | 8151, 8152 | — (CPU) | cpu | — | PP-OCRv4 det+rec ONNX (rapidocr bundle, sha256 in identity) |

GPU pinning is `CUDA_VISIBLE_DEVICES` per replica, set by the supervisor; `gpu: null`
sets it empty (an explicit CPU replica). Which physical GPU a replica lands on is
operational, never output-affecting (L4).

---

## WP-B1 — servers/common framework · supervisor · model client

| File | Action | Why |
|---|---|---|
| `servers/common/pyproject.toml` | created | `dp_servers_common` as an installable package so every server venv gets it via `-e ../common` (no sys.path glue in production servers) |
| `servers/common/requirements.txt` | created | framework venv pins — mirror the DP venv's web stack (fastapi 0.139.0 / uvicorn 0.51.0 / pydantic 2.13.4 / httpx 0.28.1 / pytest 9.1.1) |
| `servers/common/dp_servers_common/wire.py` | created | the one wire contract: `InferRequest{input_b64, codec, params}`, ok/error envelopes, health bodies; strict (`extra="forbid"`) |
| `servers/common/dp_servers_common/backend.py` | created | the backend seam (`load / identity / infer`) + the error contract the client keys retries off (`BackendError` deterministic vs `TransientBackendError`) |
| `servers/common/dp_servers_common/app.py` | created | HTTP skeleton: warmup thread, `/health` (ready 200 with identity · warming 503 · load_failed 500), `/infer` (422/503/500 mapping), infer serialized by lock (replicas are the parallelism) |
| `servers/common/dp_servers_common/runner.py` | created | `serve(backend)` process entrypoint; operational env only (`DP_SERVER_HOST/PORT/LOG_LEVEL`); load failure exits 3 — fail loud, supervisor owns restarts |
| `app/supervisor.py` | created (NEW app/ file, imported by nothing — v0 untouched) | manifest → spawn (own venv, own process group, `CUDA_VISIBLE_DEVICES` pin, per-replica log) · health-check loop · restart-on-crash/unhealth with capped backoff ladder · `python -m app.supervisor` standalone runner for Stage B drills |
| `app/model_client.py` | created (NEW app/ file, imported by nothing) | replica round-robin, per-call timeout, bounded transient retry on other replicas, deterministic failures raised immediately; identity subset-verify on connect and before a replica's first use (L4: never silently the wrong model) |
| `servers/manifest.json` | created | the L9 manifest: ports/GPUs/timeouts + expected identity (model name + pinned weights revision) per server |
| `servers/.gitignore` | created | `logs/` (replica logs); `.venv/` already ignored repo-wide |
| `servers/common/tests/{test_framework,test_model_client,test_supervisor}.py`, `conftest.py`, `fake_server.py` | created | the framework tests (TDD, red→green); supervisor/model-client tests spawn REAL framework servers over loopback HTTP |
| `docs/refactor_stage_B.md` | created | this worklog |

### In-session decisions (things the plan left open)

- **Wire format**: JSON + base64 input bytes on `POST /infer`, identity on `GET /health` —
  the OCR sidecar's proven posture generalized. Loopback-only default host.
- **Per-call operation params ride the request** (`params{}`), pinned by the CALLING
  stage's code at Stage C (beam size, language hints, speaker bounds…). Server-side
  behavior (model id, weights revision, device, compute type) is pinned in server code.
  Both are "behavior lives in code" (L4); env stays operational.
- **Unexpected backend exceptions map to transient (500)**: `/infer` is side-effect-free,
  so a bounded cross-replica retry of a genuinely deterministic crash just fails fast on
  another replica; marking them deterministic would forfeit recovery from replica-local
  CUDA/state corruption. `BackendError` (422) is the explicit deterministic channel.
- **Inference is serialized per replica** (lock): model thread-safety is not assumed
  (ctranslate2's internal safety notwithstanding); parallelism comes from replicas.
- **Two replicas per server** in the manifest: the §8 kill-one drill requires a surviving
  replica for "client retries succeed" mid-restart.
- **Supervisor restarts forever with a capped ladder** (1→30 s, reset after 60 s stable):
  a crash-looping optional server must stay visible-but-contained, not take DP down;
  L7/L8 own the record-level consequences.
- **`gpu: null` ⇒ `CUDA_VISIBLE_DEVICES=""`** — a CPU replica explicitly sees no GPU, so
  a CPU-pinned server can never silently drift onto cuda (device is part of identity).
- **Supervisor/model-client tests live in `servers/common/tests`**, run by the framework
  venv (they import `app.supervisor`/`app.model_client` via conftest path insert — both
  modules are stdlib+httpx by design). Keeps the DP suite count byte-identical this
  stage; Stage C decides their final home when main.py wiring lands.
- **Weights revisions pinned from today's cache state** (`edaa852e…`, `84fd2591…`,
  `e66f3d3b…`, `f826b80d…`): v0 loads float-to-`main` (a standing gap the survey
  flagged); the servers close it — load pinned, report resolved, manifest asserts.

### Test evidence (WP-B1)

```
$ cd product/services/data-processing/servers/common && ./.venv/bin/python -m pytest tests/ -q
24 passed, 1 warning in 15.44s
```

(9 framework wire-contract + 9 model-client + 6 supervisor tests; supervisor tests spawn
real framework servers and include kill→respawn, crash-loop backoff, load-failure
fail-loud, GPU-pin env, and log-file checks. The warning is starlette's TestClient
deprecation shim, pre-existing upstream.)

## WP-B2 — servers/whisper (faster-whisper behind the seam)

Built by a parallel subagent inside the WP boundaries (only `servers/whisper/`
touched); verified independently by the orchestrating session (test re-run below).

| File | Action | Why |
|---|---|---|
| `servers/whisper/requirements.txt` | created | validated ASR stack (faster-whisper 1.2.1 / ctranslate2 4.8.1 / av 18.0.0) + nvidia-cublas-cu12 12.9.2.10 + nvidia-cudnn-cu12 9.24.0.43 (ctranslate2 GPU needs cuBLAS/cuDNN 9) + framework stack + `-e ../common` |
| `servers/whisper/server.py` | created | `WhisperBackend`: pinned Systran/faster-whisper-large-v3 @ `edaa852e…` (snapshot path via huggingface_hub so the revision pin is airtight), cuda/float16, fail-loud if CUDA absent; v0 decode semantics kept (BytesIO→av, vad min_silence 500 ms, chunk-relative segments); serves `task=transcribe|translate` from ONE loaded model (v0 translate parity) |
| `servers/whisper/README.md` | created | setup, identity, determinism verdict |
| `servers/whisper/tests/test_server.py` | created | 6 tests: warming-503→ready-200, identity (manifest subset + shape), golden exact, translate smoke, garbage→422, unknown-param→422 |
| `servers/whisper/tests/fixtures/{golden_transcribe.json,PROVENANCE.md}` | created | golden + provenance (identity verbatim, run hashes, no-tolerance policy) |

In-session decisions (agent's, endorsed): nvidia libs preloaded via ctypes RTLD_GLOBAL
from the venv before any CUDA import (no LD_LIBRARY_PATH games); `av.FFmpegError`/
`ValueError` → deterministic 422, `RuntimeError` → transient 503 (ctranslate2 surfaces
CUDA faults as RuntimeError); translate result language hardcoded `en` (v0 design
point — detected language is the SOURCE); beam_size rejects bools; params fail loud on
unknowns. Golden output transcribes both synthesized sentences verbatim.

**Determinism: bit-stable.** 4 fresh-process runs — 3 on GPU 4, 1 on GPU 5 (replica
equivalence) — canonical sha256 identical in all 4, zero delta in text/language/segment
floats. Exact compare, zero tolerance. Measured VRAM ≈ 3.2 GB/replica (under the plan's
~5 GB estimate).

Test evidence (agent run, then re-run independently by the orchestrator):

```
$ cd servers/whisper && CUDA_VISIBLE_DEVICES=4 ./.venv/bin/python -m pytest tests/ -q
6 passed, 1 warning in 5.72s
```

## WP-B5 — servers/ocr (PP-OCR relocated into the framework)

Built by a parallel subagent inside the WP boundaries (only `servers/ocr/` touched);
verified independently by the orchestrating session (test re-run below).

| File | Action | Why |
|---|---|---|
| `servers/ocr/requirements.txt` | created | sidecar's validated engine pins verbatim (rapidocr-onnxruntime 1.4.4 / onnxruntime 1.27.0 / pillow 12.3.0 / numpy 2.5.1 / opencv 5.0.0.93 / shapely 2.1.2 / pyclipper 1.4.0 / PyYAML 6.0.3) + framework stack + `-e ../common` |
| `servers/ocr/server.py` | created | `OcrBackend`: PPOCREngine ported from `sidecars/ocr/app.py` — same RapidOCR kwargs (4 threads, cls off, cuda off), same model discovery, same decode posture (b64 `validate=True`, data-URI tolerated), det/rec sha256 in identity; sidecar env knobs became code pins (L4) |
| `servers/ocr/README.md` | created | relation to the retiring sidecar, setup, identity, determinism verdict |
| `servers/ocr/tests/test_server.py` | created | 8 tests: health identity (manifest subset-match, 64-hex shas), golden smoke, verbatim-text majority sanity, empty-image honesty (`regions: []`), bad-b64/garbage → 422, params/codec strictness |
| `servers/ocr/tests/fixtures/{golden_regions.json,PROVENANCE.md}` | created | golden + provenance (identity verbatim, run hashes, policy) |
| `servers/manifest.json` | edited (orchestrator) | ocr `expected_identity.weights` now pins det/rec sha256 (agent-flagged gap) |

In-session decisions (agent's, endorsed): framework `infer_lock` replaces the sidecar's
per-call lock; `params` strictly empty and `codec`, if present, must be `image/jpeg`
(else 422 — behavior fails loud); rapidocr version in identity read from installed
metadata, test pins 1.4.4; PIL decode failures → deterministic 422, unexpected engine
crashes → framework 500-transient. Measured quirk recorded: PP-OCRv4 rec drops
inter-word spaces on the title line (`"QuarterlyPlanningNotes"`) — golden pins the
verbatim measured text; the known-lines sanity check compares whitespace-stripped.

**Determinism: bit-stable.** 4 separate fresh CPU processes through the real wire path;
canonical result sha256 identical in all 4; det/rec shas equal the sidecar README's
(same bundled ch_PP-OCRv4 pair). Policy: exact compare, zero tolerance — a future
mismatch means the stack changed and must be re-ratified, not tolerated.

Test evidence (agent run, then re-run independently by the orchestrator):

```
$ cd servers/ocr && CUDA_VISIBLE_DEVICES= ./.venv/bin/python -m pytest tests/ -q
8 passed, 1 warning in 2.61s
```

## Noticed for later stages

- **Stage C** — `app.state.vlm_pool` is read but never set (`ingest_core.py:150`); the
  natural hook for handing stages their ModelClients. Thin clients should be `run_async`
  (loop-native), freeing the threadpool tokens ASR/diarize/acoustic/OCR hold today.
- **Stage C** — the AST server returns raw `[label, score]` tags; caption folding
  (`caption_from_tags`) is pure and stays client-side, so `ACOUSTIC_TOP_K/THRESHOLD`
  become stage-code pins (they are env knobs today — L4 debt to clear with the stage).
- **Stage C** — v0's ASR knobs (`ASR_MODEL/DEVICE/COMPUTE_TYPE/BEAM_SIZE/LANGUAGE/VAD`)
  are output-affecting env vars that die when the asr stage becomes a thin client; the
  whisper server pins large-v3/cuda/fp16 in code, a **deliberate dialect change from
  v0's base/cpu/int8** that Stage C must reflect in the stage's `vB`.
- **Stage C (ocr rewire)** — `app/vision/ocr/ppocr.py` speaks the old sidecar wire
  (`/ocr`, top-level `model_sha_det/rec`); the thin-client rewrite maps its pins onto
  `identity.weights.det_sha256/rec_sha256` and the `/infer` envelope. Its
  `_normalize_bbox` pass-through heuristic stays valid (server returns pixel coords).
  The old 48 MB body cap has no framework equivalent — decide there if wanted.
