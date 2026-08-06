# DP Rebuild — Stage B worklog (Machinery)

**Stage:** B — Machinery · **Status:** DONE 2026-08-06 · *Dated:* 2026-08-06
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

## WP-B3 — servers/pyannote (diarization behind the seam)

Built by a parallel subagent inside the WP boundaries (only `servers/pyannote/`
touched); verified independently by the orchestrating session (test re-run below).

| File | Action | Why |
|---|---|---|
| `servers/pyannote/requirements.txt` | created | torch==2.8.0 / torchaudio==2.8.0 / pyannote.audio==3.3.2 (the trio validated on this node 2026-07-19) + framework stack + `-e ../common` + **two deviation pins, flagged below** |
| `servers/pyannote/server.py` | created | `PyannoteBackend`: pipeline pinned `pyannote/speaker-diarization-3.1@84fd2591…`, cuda fail-loud; v0 semantics preserved (scoped `torch.load weights_only=False` for the Lightning checkpoints, ffmpeg pre-decode to 16 kHz mono WAV, spk_N first-onset normalization, clamping, overlap allowed); sub-model revisions resolved post-load into identity |
| `servers/pyannote/README.md` | created | setup, gated-model note, identity, determinism verdict |
| `servers/pyannote/tests/test_server.py` | created | 5 tests: identity (manifest subset + shape), golden exact, garbage→422, missing span_seconds→422, unknown param→422 |
| `servers/pyannote/tests/fixtures/{golden_diarize.json,PROVENANCE.md}` | created | golden + provenance (identity verbatim, run hashes, policy) |

**DEVIATIONS (loud), both endorsed:**
- **`huggingface_hub==0.36.2` added**: the resolver picks hub 1.26.0, which removed the
  `use_auth_token` kwarg pyannote 3.3.2 still passes → guaranteed TypeError at load.
  0.36.2 is the node's validated 2026-07-19 stack.
- **`matplotlib==3.10.8` added**: pyannote.audio 3.3.2 imports matplotlib at import time
  without declaring it; pinned to the validated stack's version.

In-session decisions (agent's, endorsed): pyannote 3.3.2 has no `revision=` kwarg — the
pin is the supported `repo@revision` checkpoint string (same in-code pin, different
spelling); sub-models land in pyannote's own cache (`~/.cache/torch/pyannote`), NOT the
HF hub cache — found empirically; the revision resolver checks pyannote's cache first,
HF hub second, fails loud on ambiguity. Identity carries measured sub-model commits
(segmentation `e66f3d3b…` — equal to the manifest's pre-resolved pin — and embedding
`837717dd…`), so a silent upstream sub-model bump surfaces as an identity change.

**Determinism: bit-stable.** 4 fresh-process runs — 3 on GPU 2, 1 on GPU 3 — canonical
sha256 identical on all four, no torch determinism flags forced. Output: 2 turns /
2 speakers, split exactly at the fixture's 0.8 s silence gap. Exact compare, zero
tolerance.

Test evidence (agent run, then re-run independently by the orchestrator):

```
$ cd servers/pyannote && CUDA_VISIBLE_DEVICES=2 ./.venv/bin/python -m pytest tests/ -q
5 passed, 9 warnings in 19.85s
```

## WP-B4 — servers/ast (acoustic tagging behind the seam)

Built by a parallel subagent inside the WP boundaries (only `servers/ast/` touched);
verified independently by the orchestrating session (test re-run below).

| File | Action | Why |
|---|---|---|
| `servers/ast/requirements.txt` | created | torch==2.8.0 (+cu128) + **transformers==5.14.1** (pip-resolved latest, verified against the pinned model before pinning) + framework stack + `-e ../common`; torchaudio NOT needed (transformers 5.x computes AST fbank via its numpy spectrogram path) |
| `servers/ast/server.py` | created | `AstBackend`: MIT/ast-finetuned-audioset-10-10-0.4593 @ `f826b80d…` pinned in code, cuda fail-loud; raw container bytes → `ffmpeg_read`; returns RAW descending-score tags (`top_k` param, default 20) — caption folding stays client-side (Stage C) |
| `servers/ast/README.md` | created | setup, identity, determinism verdict |
| `servers/ast/tests/{conftest.py,test_server.py}` | created | 5 tests: health identity (manifest subset + shape), golden exact, top-label sanity, garbage→422, param strictness |
| `servers/ast/tests/fixtures/{golden_tags.json,PROVENANCE.md}` | created | golden + provenance (identity verbatim, run hashes, escalation-only tolerance policy) |

In-session decisions (agent's, endorsed): pipeline `ValueError` (malformed audio) →
deterministic 422, CUDA/OOM falls through to framework 500-transient; `codec` advisory
(ffmpeg sniffs — v0 parity); `top_k` per-call instead of at construction (equivalent,
parameterizable); ffmpeg version captured at load into identity (it does the
demux/resample — genuinely part of identity). Golden's top tags are honest: "Speech"
0.663, then "Speech synthesizer" 0.279 — correct, the fixture IS synthesized speech.

**Determinism: bit-stable.** 4 fresh-process runs — 3 on GPU 6, 1 on GPU 7 — canonical
result sha256 identical on all four. Exact compare; PROVENANCE.md prescribes a measured
per-label tolerance only if jitter ever appears (re-ratify, don't loosen silently).

Test evidence (agent run, then re-run independently by the orchestrator):

```
$ cd servers/ast && CUDA_VISIBLE_DEVICES=6 ./.venv/bin/python -m pytest tests/ -q
5 passed, 2 warnings in 22.24s
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

## Integration drill (the §8 exit run, on node-7 under the supervisor)

`servers/drill_stage_b.py` (committed with this worklog) drives the REAL manifest —
real ports 8121–8152, real GPUs 2–7 — through four phases: supervisor spawn → identity
verification via `app/model_client.py` → golden smoke through the ModelClient →
kill-one-replica per server. Run 2026-08-06:

```
$ ./.venv/bin/python servers/drill_stage_b.py    # exit code 0
"identity":     ok — all 8 replicas subset-match their manifest expected_identity
"golden_smoke": ok — whisper 0.59s · pyannote 0.70s · ast 0.38s · ocr 0.45s,
                all compare "exact", problem: null
"kill_one_replica": ok for all four —
  whisper:  killed 3756333, retry during outage 0.59s ok, respawned, restarts=1
  pyannote: killed 3756337, retry during outage 1.09s ok, respawned, restarts=1
  ast:      killed 3756341, retry during outage 0.40s ok, respawned, restarts=1
  ocr:      killed 3756345, retry during outage 0.48s ok, respawned, restarts=1
"ok": true
```

Post-drill teardown verified: zero fleet ports listening, all 8 GPUs back to 0 MiB,
v0 `/health` still 200. Replica logs under `servers/logs/` (gitignored).

## Golden-output provenance (consolidated; per-server detail in each `tests/fixtures/PROVENANCE.md`)

| Server | Input (sha256 prefix) | Params | Model + weights | Frameworks | Determinism (all fresh-process, full wire path) |
|---|---|---|---|---|---|
| whisper | `speech_two_speakers.webm` (`a2e29465`) | task=transcribe, beam 1, lang en, vad on | faster-whisper-large-v3 @ `edaa852e`, cuda/fp16 | faster-whisper 1.2.1 · ctranslate2 4.8.1 · av 18.0.0 | **bit-stable** — 4 runs, GPUs 4+5, exact compare |
| pyannote | same clip | span_seconds=6.496 | speaker-diarization-3.1 @ `84fd2591` (segm `e66f3d3b`, embed `837717dd`), cuda | pyannote.audio 3.3.2 · torch 2.8.0+cu128 · ffmpeg 7.1 | **bit-stable** — 4 runs, GPUs 2+3, exact compare |
| ast | same clip | top_k=20 | ast-finetuned-audioset @ `f826b80d`, cuda | transformers 5.14.1 · torch 2.8.0+cu128 · ffmpeg 7.1 | **bit-stable** — 4 runs, GPUs 6+7, exact compare |
| ocr | `screen_planning_notes.jpg` (`18803e4b`) | none | PP-OCRv4 det `d2a7720d…`/rec `48fc40f2…`, cpu, 4 threads | onnxruntime 1.27.0 · rapidocr 1.4.4 | **bit-stable** — 4 runs, CPU, exact compare |

Inputs are committed synthesized binaries (piper-TTS speech, pillow screenshot —
`INPUT_PROVENANCE.md` beside each); no captured user data is committed. Tolerance
policy everywhere: exact, zero tolerance — future drift is an identity change to
re-ratify, never a tolerance to widen. This is the baseline Stage C's T-1 matrix
builds on.

## Old-suites proof (nothing running changed)

`git diff --name-status main...HEAD -- app/` shows exactly two ADDED files
(`model_client.py`, `supervisor.py`), zero modifications. All three service suites
re-run 2026-08-06 after the last code commit, counts identical to the Stage A baseline:

```
$ cd product/services/storage         && ./.venv/bin/python -m pytest -q
310 passed, 1 warning in 14.77s
$ cd product/services/data-processing && ASR_BACKEND=mock ./.venv/bin/python -m pytest -q
788 passed, 21 skipped, 1 warning in 67.97s (0:01:07)
$ cd product/services/continuum       && ./.venv/bin/python -m pytest -q
262 passed, 7 skipped in 11.69s
```

Live v0 checked before and after every fleet operation: `GET :8085/health` → 200.

## Exit criteria (§8 Stage B)

| Criterion | Status | Evidence |
|---|---|---|
| All four servers pass health + golden-output smoke on node-7 | done | per-server suites green in their own venvs (whisper 6 · pyannote 5 · ast 5 · ocr 8) AND the drill's golden_smoke phase, exact-compare, under the supervisor |
| …under the supervisor | done | drill phases spawn_ready + identity + golden_smoke, exit 0 |
| Kill-one-replica drill: supervisor restarts it, client retries succeed | done | drill kill_one_replica phase: 4/4 servers, retry-during-outage ok, respawn ok |
| Old service suites green and untouched | done | 310 · 788+21s · 262+7s — identical to the Stage A baseline; suites' files untouched |
| Zero modifications to existing app/ files | done | `git diff --name-status main...HEAD -- app/` → 2 added files only |
| One commit per WP, worklog in same commit | done | `3b70b68` B1 · `0e296f0` B2 · `9cd9394` B3 · `77b6bad` B4 · `d7315b4` B5 · final commit (this edit + drill script) |

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
- **Stage C (pyannote identity)** — only `pipeline_revision` is manifest-pinned; the
  segmentation/embedding sub-models resolve at `main` at load and land in pyannote's
  OWN cache (`~/.cache/torch/pyannote`, env `PYANNOTE_CACHE`), not the HF hub cache.
  Identity reports the measured commits, so an upstream bump surfaces as an identity
  mismatch at connect — consider pinning all three in the manifest once T-1 exists.
- **Stage C (ast)** — transformers 5.x computes AST features via its numpy fbank path;
  adding torchaudio to that venv later could shift features and the golden — re-measure
  if it ever lands.
- **Stage F (deploy)** — `platform/deploy/run_learn.sh` has no restart-on-crash and no
  OCR row; cutover must wire the supervisor into the deploy story (main.py owns it from
  Stage C, but the deploy table + env passthrough happen at F). The fleet's HF_TOKEN
  passthrough is ambient today; deploy should own it explicitly.
- **Stage G** — when `sidecars/ocr` is demolished, the stray live sidecar on :8097
  (running from a deleted worktree) needs an owner to stop it; it is not this repo's
  process to kill.

## 2026-08-06 — Cleanup round (independent verification, 6 lenses)

> cleanup · applied on `dp-rebuild-v1`, two commits (fixes `689fcd8`, then this worklog)
> · triggered by an independent 6-lens verification that confirmed Stage B's substance
> (including a full re-run of the drill: exit 0, clean teardown) and found the items
> below. Everything above this section stands as written; corrections amend, never
> rewrite. **Hard rule honored: no golden changed** — all four pre-existing goldens
> re-verified byte-identical after every edit (hashes below).

**Determinism hardening**

- `servers/manifest.json` pyannote `expected_identity.weights` now asserts
  `segmentation_revision` (`e66f3d3b…`) and `embedding_revision` (`837717dd…`) beside
  the pipeline pin. Before this, the subset-match checked only `pipeline_revision`, so
  an upstream sub-model push on a cold cache would have changed bytes and still passed
  identity verification.
- **Correction to WP-B3 and "Noticed" above:** the lines "equal to the manifest's
  pre-resolved pin" and "an upstream bump surfaces as an identity mismatch at connect"
  were inaccurate as committed — the manifest never pinned the sub-model revisions and
  subset-matching ignored them; identity merely *reported* the measured commits. True
  as of this round: all three revisions are asserted.
- `servers/whisper/requirements.txt` pins its formerly floating transitives:
  **onnxruntime==1.28.0** (executes the Silero VAD gate — output-affecting),
  huggingface_hub==1.26.0, tokenizers==0.23.1 (all equal to what the venv already
  ran — no reinstall, no behavior change). onnxruntime is now also reported in
  whisper's `/health` `identity.frameworks`; dated note appended to whisper
  PROVENANCE.md. This also closes the WP-B2 report's "hub is now 1.x as a transitive —
  may want pinning" note, which never made it into "Noticed" above: recorded here, and
  moot — the pin landed.
- Torch wheel provenance: `servers/{pyannote,ast}/requirements.txt` now carry
  `--extra-index-url https://download.pytorch.org/whl/cu128`, so a rebuild reproduces
  torch **2.8.0+cu128** (the flavor the venvs verifiably hold) instead of whatever CUDA
  build PyPI serves for the bare version; whisper's file documents that its stack is
  torch-free and the index is not applicable there.

**Supervisor hardening** (`app/supervisor.py` — still imported by nothing; v0 untouched)

- SIGTERM/SIGINT handler in the CLI path: replicas run in their own sessions (so the
  supervisor can `killpg` them), which meant plain `kill <supervisor>` orphaned the
  whole fleet. The handler now drives `stop()`; the misleading "children die with it"
  comment at `_spawn` is rewritten to state the real contract.
- `_monitor` now enforces `startup_timeout_s` for replicas in state `starting`: a
  crash-restart into a hung warmup (health 503 forever) was previously never recovered;
  it is now killed and respawned at the deadline.
- Both behaviors covered by new framework tests (`test_sigterm_reaps_all_replicas`
  drives the real `python -m app.supervisor` CLI; `test_crash_restart_into_hung_warmup_
  is_recycled` uses a new crash-then-hang knob in the fake server). TDD: both watched
  red against the old code (orphaned replica; restarts stuck at 1), then green.

**Baseline breadth — real-speech golden input** (the synthetic piper clip stays)

- New committed fixture `speech_real_dialog.webm` (17.808 s, webm/opus 16 kHz mono,
  sha256 `8b190553…ef00b24b`) in whisper/pyannote/ast fixture dirs: the Scrooge/nephew
  "Bah! Humbug!" exchange from the LibriVox **group dramatic reading** of *A Christmas
  Carol*, Stave 1 — real multi-speaker speech with natural turn-taking (narrator + two
  character readers; solo-reader "dramatic" versions were rejected). Source URL,
  public-domain license (CC PD mark 1.0), source-file sha256, exact ffmpeg cut and the
  window-selection method are in each INPUT_PROVENANCE.md.
- Goldens cut under the exact WP protocol — 4 fresh processes per server, 3 on the
  primary GPU + 1 on the replica GPU, through the real wire path:
  `golden_transcribe_real.json` `f5da3b6e…` (GPUs 4+5) · `golden_diarize_real.json`
  `cc8cec79…` (GPUs 2+3; 10 turns, 3 speakers) · `golden_tags_real.json` `009e2c73…`
  (GPUs 6+7). **All three bit-stable** — exact compare, zero tolerance, same policy.
  Added as a second exact test in each suite and as second smokes in the drill.

**Drill + inventory corrections**

- **Correction to "Integration drill" above:** `drill_stage_b.py` did NOT check v0
  `:8085` — that was session practice outside the script. The preferred fix landed:
  the drill now records v0's health before the fleet starts and after teardown as a
  scored `v0_untouched` phase.
- The unused `ast_tags` tolerance branch in the drill is deleted (ast's PROVENANCE
  prescribes exact compare; the branch misstated it). Comparison is exact-only.
- `servers/manifest.json` `_comment` no longer lists 8091 (not a live port on this
  node). `servers/.gitignore` is self-sufficient (`logs/`, `.venv/`, `__pycache__/`,
  `.pytest_cache/`, `*.egg-info/`).
- **VRAM correction to WP-B2:** whisper's measured peak is **4271 MiB** per replica,
  not "≈ 3.2 GB" (still under the ~5 GB plan estimate; allocation table unchanged).
- **File-coverage completions** (present in the WP commits, missing from the tables
  above): the committed golden-input binaries (`speech_two_speakers.webm` ×3,
  `screen_planning_notes.jpg`), the four `tests/fixtures/INPUT_PROVENANCE.md` files,
  and `servers/common/dp_servers_common/__init__.py`.

**Verification re-run (2026-08-06, after all edits)**

```
$ servers/common   ./.venv/bin/python -m pytest tests/ -q → 26 passed
$ servers/whisper  CUDA_VISIBLE_DEVICES=4 …pytest -q     →  7 passed
$ servers/pyannote CUDA_VISIBLE_DEVICES=2 …pytest -q     →  6 passed
$ servers/ast      CUDA_VISIBLE_DEVICES=6 …pytest -q     →  6 passed
$ servers/ocr      CUDA_VISIBLE_DEVICES=  …pytest -q     →  8 passed
$ ./.venv/bin/python servers/drill_stage_b.py            → exit 0, "ok": true
    spawn_ready 18.9s · identity 8/8 ok · golden_smoke 7/7 exact ok
    (whisper 0.58/0.71s · pyannote 0.70/0.89s · ast 0.39/0.41s · ocr 0.44s)
    kill_one_replica 4/4 (retries 0.22–0.50s, all respawned)
    v0_untouched: before "200" → after "200"
```

Pre-existing goldens byte-identical after every change (sha256, re-verified against the
pre-cleanup baseline): `golden_transcribe.json` `ccda989f…8d61376` ·
`golden_diarize.json` `fef8b89c…f81a11b` · `golden_tags.json` `8905b4a1…0605022` ·
`golden_regions.json` `1802f5e9…b2a52d6`. Status stays **DONE**; Stage C not started.
