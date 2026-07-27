# Founders' thread — Engineering

> Running canvas for founders' engineering sessions (launch: [../PROMPTS.md](../PROMPTS.md) §D).
> Cross-service build sequencing, integration plans, infra calls. Service-internal
> engineering lives in each service's canvas, not here.

**Status:** active · **Last updated:** 2026-07-26 (**D18 — storage/C10 board: the day-log moves to
storage, the cycle window becomes an ingest-time watermark, `window_id` becomes opaque; C12/C13/C14
minted. ALL DECIDED, NOT BUILT.** Same day, earlier: **D17 — timezone ownership decided AND built**,
review item O-1 closed; then review items **O-2 / O-3 / O-4** closed as three *different* doc
shapes. All three dated entries are at the end of the worklog)

---

## Serve-loop MVP slice (v0.0) — the walking skeleton

**Goal.** One text turn, end to end: a user types in a computer chat box → gets a streamed
answer from the **base** Qwen3-VL-32B → the turn is persisted. This proves the serve-loop spine
(input → QueryBuilder → inference → output → storage) with the *minimum* of every service.
Everything else (personalization, capture, mentors, extra modalities/surfaces) hangs off this
later. Deliberately un-personalized: inference serves the base model, no adapter yet.

**In this slice**

| WS | Service | M0 deliverable | Contracts it must honor |
|---|---|---|---|
| A | **input** | Computer text chat surface → request envelope → **QueryBuilder text path** → emit a **C3 UserPrompt** (text-only). Mint `session_id` / `turn_id`. | produces C3; C8 is a **pass-through** for text (no heavy normalization yet) |
| B | **inference** | vLLM up with base **Qwen3-VL-32B** (TP=8, one node); accept C3, prepend system prompt, **single-shot** generate (no harness/tools/mentors yet), **stream out via C9**; write the turn via C4. C6 resolves to "base model, no adapter". | consumes C3, resolves C6 (trivial), produces C9 + C4 |
| C | **output** | Relay the **C9** token stream to the computer surface; markdown render; per-turn delivery ack. | consumes C9 |
| D | **storage** | Minimal **/sessions**: persist a C4 turn record keyed by `session_id`/`turn_id`; trivial **model directory** entry ("base, no adapter") that C6 reads. | serves C4 write + C6 read |
| E | **platform** | One a3mega node hosting vLLM + the three app services; basic HTTPS reachability; a shared dev secret/env. Thin — just enough to run the loop. | none (enables A–D) |

**Out of this slice (later slices):** recording + data-processing + `/context` (capture);
continuum + per-user adapter (personalization); mentors/C7 + agentic harness; C11 recent-context;
image/video/speech modalities; mobile / extension / wearable surfaces. Each is its own slice once
the skeleton walks.

**Gate — interface freeze (do this first, jointly).** Before A–D fan out, the input + inference +
output leads pin the **MVP-minimal shapes** of C3, C9, and the C4 turn record in
[../ARCHITECTURE.md](../ARCHITECTURE.md) §Contracts:
- **C3 (text v0):** `{user_id, session_id, turn_id, messages:[{role, text}], client_capabilities, template_version}`.
- **C9 (text v0):** `{turn_id, model_id, text chunks…, end-of-turn: {usage}}`. *Mid-turn frames deferred* (no mentors yet).
- **C4 (turn v0):** `{user_id, session_id, turn_id, user_prompt_ref, response_text, model_id, adapter:"base", t_created, t_completed, traces:[]}`.

**Launch order.** (1) Interface-freeze session (input+inference+output). (2) Then WS-A/B/C/D fan
out in parallel against the frozen shapes; WS-E runs alongside. (3) An **integrator** session
([../PROMPTS.md](../PROMPTS.md) §E) wires them.

**Integrator exit criterion (v0.0 done):** a pilot user types a question in the computer surface
and receives a streamed base-model answer; the turn is persisted in `/sessions` and re-readable by
`session_id`/`turn_id`; no personalization, no capture — just the spine, proven.

**Recommended first launch:** the **interface-freeze session** (Prompt A framing, but joint across
input+inference+output leads) — nothing safely parallelizes until C3/C9/C4 v0 are locked.
**Status: freeze DONE (2026-07-09)** — shapes locked in [../ARCHITECTURE.md](../ARCHITECTURE.md)
§Contracts + machine-readable in [../contracts/](../contracts/). Fan-out is unblocked.

### MVP build conventions (v0.0) — so the 5 workstreams interoperate

Pinned so WS A–E produce compatible pieces; the integrator may finalize process topology.

- **Stack:** Python 3.11, **FastAPI + uvicorn** per backend service; `httpx` for inter-service
  calls; **pydantic** models mirroring the JSON Schemas in [../contracts/](../contracts/);
  `pytest`. Surface = static HTML/CSS/JS, **no build step**, served by input.
- **Model backend switch (critical):** env `MODEL_BACKEND=mock|vllm`. **`mock` is the default**
  — a canned, streamed answer, **no GPU needed**, so the whole loop runs on any box. `vllm` =
  OpenAI-compatible client to a vLLM server (real Qwen3-VL-32B, needs the a3mega node). Ship
  BOTH; only `mock` is expected to run tonight.
- **Ports (localhost dev):** input `8081`, inference `8010`, output `8082`, storage `8083`
  (vLLM `8000` when real).
- **Storage:** SQLite file DB for dev — a `/sessions` turns table (C4) + a model-directory
  table (C6). No external DB tonight.
- **Contracts are tested:** each service validates the payloads it produces/consumes against
  `../contracts/*.json` in its tests.
- **Layout per service:** `product/services/<key>/{app/, tests/, run.sh, requirements.txt}`;
  keep the worklog in `handoff/wsN-*.md`, status in the service `HANDOFF.md`.
- **Recommended flow (integrator finalizes):** browser → input `:8081 /api/turn` (JSON `{text}`)
  → QueryBuilder builds C3 → inference `:8010 /infer` (streams C9; resolves C6 + writes C4 to
  storage `:8083`) → input relays the C9 stream to the browser; **output** owns the browser-side
  C9 reader + markdown render (served with the surface) **and** a standalone relay service for
  future non-web surfaces.
- **No agent commits.** Workstreams write files; the founders' session commits after integration.
- **Honesty rule:** the `mock` loop must actually run end-to-end; the `vllm` path is
  scripted-but-unrun until the node — never report a real-model run that didn't happen.

---

## Serve-loop MVP — v0.0 build result (2026-07-09)

**Integrator session.** Wired the five workstreams, brought the mock loop up with
`services/platform/deploy/run_all.sh`, and drove real turns end to end. Honest result below.

### What runs (executed here, not claimed)
- `run_all.sh` built a fresh shared venv, pip-installed all four services' requirements
  (PyPI reachable), and started **storage:8083 → inference:8010 (MODEL_BACKEND=mock) →
  output:8082 → input:8081**, `/health`-gated, all four healthy.
- **A real turn, streamed:** `POST http://localhost:8081/api/turn {"text":"What is 2+2?"}`
  → the answer streamed back as the **C9 wire format** (mock answer text, **exactly one**
  `U+001E` (0x1e) separator byte, then one JSON end frame
  `{contract:"C9",version:"0",turn_id,model_id:"Qwen/Qwen3-VL-32B-Instruct",adapter:"base",
  usage:{prompt_tokens:25,output_tokens:20},finished:true}`). `X-Session-Id`/`X-Turn-Id` ride
  in response headers.
- **Persistence proven:** the C4 turn was re-read via `GET /sessions/turns/{turn_id}` (full
  nested C3 `user_prompt`, `response_text`, `model_id`, `adapter:"base"`, empty trace arrays)
  **and** listed via `GET /sessions/{session_id}/turns`. A second turn on the same
  `session_id` grew the session list to 2. C6 `GET /model-directory/resolve?user_id=dev-user`
  → base model.
- **Both output roles exercised:** the browser reader (`c9_reader.js`, now wired into the
  input surface) **and** the standalone `POST /deliver` relay (pulled a live C9 stream from
  inference, echoed `X-Delivery-*` ack headers, relayed the body byte-for-byte).
- Browser surface serves: `GET /` (200 text/html), `/static/app.js` + `/static/c9_reader.js`
  (200) — `index.html` loads `app.js` as `type="module"`; `app.js` imports the reader.

### Test results (ran each service's pytest, real counts)
| Service | Result |
|---|---|
| storage | **10 passed** |
| inference | **6 passed** (2 deprecation warnings, websockets — cosmetic) |
| input | **19 passed** |
| output | **46 passed** |
| **total** | **81 passed, 0 failed** |

### Integration deltas (seam fixes applied)
1. **Render seam wired (primary).** Input's surface rendered answers as **plain text** with a
   TODO to adopt output's renderer. Fixed: **vendored** `output/app/static/c9_reader.js` →
   `input/app/static/c9_reader.js` (same-origin so the browser ES-module import needs no CORS
   to `:8082`), rewrote `input/app/static/app.js` to `import { renderC9Stream }` and hand it the
   `fetch()` response (streams + SAFE-markdown-renders into `#answer`, surfaces usage via
   `onEndFrame`), and updated `index.html` (`<pre>`→`<div id="answer">`, `<script type="module">`,
   markdown/code/error CSS). Canonical source stays output's copy — re-copy on change (a
   build-time copy step is the future fix to kill the duplication).
2. **inference `run.sh` now honors `PORT`/`HOST`.** It hardcoded `--host 0.0.0.0 --port 8010`,
   ignoring the platform↔service contract (read `HOST`/`PORT` from env). Values matched the
   defaults so nothing broke, but it now binds what `run_all.sh` passes.
3. **Storage test-DB hygiene.** The live run created `storage/app/dev.db` (a real SQLite file
   with test turns) inside an untracked dir; removed it and added `storage/.gitignore`
   (`*.db`, `__pycache__/`, `.pytest_cache/`) so it never gets committed.

Ports/URLs were already consistent (8081→8010→8083, output 8082); the ``+end-frame C9
format is produced by inference and consumed identically by input's relay, output's relay,
`c9_reader.js`, and `c9_parse.py` — verified byte-for-byte (single 0x1e in the stream).

### Blockers / not done here
- HTTPS / remote reach (cloudflared), CI, observability: later platform work (unchanged).
- `c9_reader.js` is duplicated (input vendors output's copy); acceptable for v0.0, but a
  copy-on-build step should replace the manual vendoring.

**Exit criterion (v0.0 done): MET for the mock loop.** A turn typed at the computer surface
returns a streamed base-*mock* answer and the turn is persisted + re-readable by
`session_id`/`turn_id`.

### REAL model — v0.0 closed on Qwen3-VL-32B (2026-07-09, node-7)

The mock ceiling is lifted — the loop now runs on the **real base model**, verified on
`nucla3m-a3meganodeset-7` (8× H100, driver 580 / CUDA-13):
- Launched **Qwen3-VL-32B-Instruct on vLLM 0.19.1** (the `vllm-vlm` conda env), TP=8, from the
  existing HF cache (~63 GB, already downloaded — no pull). Came up in a few minutes, ~75 GB/GPU
  at util 0.90. Recipe verified + recorded in [`../services/inference/serve_vllm.sh`](../services/inference/serve_vllm.sh).
- Direct `/v1/chat/completions` sanity: *"2+2 equals 4, and the capital of France is Paris."* in ~1.9 s.
- **Full serve-loop turn on real weights:** `POST :8081/api/turn {"text":"…Eiffel Tower…"}` →
  streamed C9 (real answer *"The Eiffel Tower is a wrought-iron lattice tower located in Paris,
  France."* + single `U+001E` + end frame, `model_id:"Qwen/Qwen3-VL-32B-Instruct"`, real usage
  62→19) → C4 persisted with the real answer, re-readable by turn id. Flip was just
  `MODEL_BACKEND=vllm` in `deploy/.env` + `run_all.sh --restart` (inference `/health` reports
  `backend:"vllm"`).

**Exit criterion for v0.0 is now MET on the real base model, not just mock.** One variable was
changed vs. the mock loop (the backend) — everything else (contracts, wiring, persistence) was
already proven, so the real turn worked first try.

**Follow-up DONE (2026-07-09):** upgraded the serving stack to **vLLM 0.24.0 / torch 2.11 /
transformers 5.13 / CUDA-13 (cu13) wheels + flashinfer** in a fresh `vllm-cu13` env, and
validated it end to end (direct completion + a real loop turn) — swapped in as primary with the
0.19.1 `vllm-vlm` env kept as fallback. Done as its own step *after* v0.0 closed, so the version
bump was isolated from app wiring; it validated first try. Recipe: `serve_vllm.sh` (now defaults
to `vllm-cu13`); stack in [../STACK.md](../STACK.md). Still open: the D6 OCR spot-check on real
screen-capture data (model is serving).

---

## Learn-loop MVP slice — the capture skeleton (2026-07-09)

**Goal.** One audio chunk, end to end: the **computer microphone** captures a chunk → recording
lands the bytes in `/raw` and emits a **C1** envelope → data-processing runs **ASR** and writes a
**C2** processed record to storage `/context`. This proves the learn-loop spine (recording →
data-processing → storage `/context`) with the *minimum* of every service — it starts the data
compounding the whole thesis rests on. Deliberately **audio-only, no enrichment**: ASR transcript
+ segment timestamps, no diarization, no world-data, no vision.

**Capture model (hold this — it's the user→recording reality).** The user→recording feed is a
**continuous, always-on** life stream (body-cam / always-on computer mic + screen), **not** a
press-to-record clip. Recording **carves** that live stream into dense, sequential,
wall-clock-stamped chunks (C1's `(stream_id, sequence)` + `t_start/t_end`). So downstream — to
data-processing — data arrives as **bounded chunks with start/end times**, but those boundaries are
**recording's artifact, not semantic units**: an utterance or word can straddle a chunk edge. For
the M0 skeleton, ASR each chunk independently; cross-chunk **boundary stitching** is a later
refinement, **not** an M0 gate — but build data-processing *knowing* the stream is continuous
underneath. This is also exactly why consent + delete-last-N (recording's M2) are load-bearing:
capture is always-on, so there is no natural "stop" the user leans on.

**Skeleton scope (decided in-session, D10).** ONE device+modality first: **computer mic → ASR-only
→ a `/context` record** — the simplest capture path. It reuses the POC Phase-1 audio machinery
(faster-whisper/WhisperX) and dodges the GPU-heavy vision/OCR path. Screen-frames→OCR and wearable
A/V are later slices.

**In this slice**

| WS | Service | M0 deliverable | Contracts it must honor |
|---|---|---|---|
| A | **recording** | Computer-mic capture → chunker → **`PUT` bytes to storage `/raw`** (get `blob_ref`) → emit a **C1** envelope to data-processing. Mint globally-unique `stream_id`/`chunk_id`; dense zero-based `sequence`; device auth deferred. | produces C1 (both legs); push/at-least-once, dedup on `chunk_id` |
| B | **data-processing** | Consume C1; **pull bytes by `blob_ref`**; run **ASR** (transcript + segment times); stamp `pipeline_version`; write a **C2** record to `/context`; idempotent on `record_id`. | consumes C1, produces C2; C8 not in this slice |
| C | **storage** | Extend the running `:8083` service: **`/raw`** blob write (`PUT`, mints opaque `blob_ref`, idempotent on `chunk_id`) + read-by-ref; **`/context`** C2 write (idempotent on `record_id`), time-indexed on `(user_id, t_start)`. | serves the C1 blob leg + C2 write |
| E | **platform** | One box hosting the three services + an ASR runtime (GPU optional at M0 — faster-whisper runs on CPU for the skeleton); a shared dev env. Thin — just enough to run the loop. | none (enables A–C) |

**Out of this slice (later slices):** diarization + translation + full audio pipeline; text +
image + video pipelines (OCR-specialist pass, dense captioning); world-data enrichment (speakers,
faces, geo/place, objects); the cross-source time spine (multi-device skew); C8 synchronous API;
C10 training-window read; C11 recency index; wearable + browser-extension + mobile capture; consent
enforcement (recording's M2 — no always-on capture ships without it; the mic-only *dev* skeleton
predates that gate). Each is its own slice.

**Gate — interface freeze: DONE (2026-07-09).** C1 + C2 v0 frozen in
[../ARCHITECTURE.md](../ARCHITECTURE.md) §Contracts (learn-loop block) + machine-readable in
[../contracts/](../contracts/) (`c1_raw_stream_envelope.v0.json`, `c2_processed_record.v0.json`),
stress-tested by a 5-lens adversarial critic pass before freeze (2 blockers + 7 fixes applied). The
frozen forks:
- **Skeleton (D10):** computer mic → ASR(+segment times) → `/context`; no diarization/enrichment.
- **C1 delivery (D11):** push, at-least-once, dedup on `chunk_id`, order/gaps via dense zero-based
  `(stream_id, sequence)`; blob-first write invariant.
- **/raw write (D11):** recording `PUT`s bytes → storage mints an opaque `blob_ref` → recording
  emits C1 carrying it; data-processing pulls bytes by ref. Blob leg pinned as prose (not a new
  C-number), like C9's wire format.
- **C2 (D10):** `record_id` deterministic on `(chunk_id, pipeline_version)` (idempotent upsert,
  version-forward reprocess); `enrichments` present-but-empty (mirrors C4 trace arrays).

**Build order + fan-out.** Storage is **not** chartered-cold — it is the running serve-loop service
on `:8083`. So:
1. **storage M0 lands first/ahead** — add `/raw` (blob write+read) and `/context` (C2 write) to the
   existing service. It is the shared dependency both A and B write to.
2. **recording M0** (mic → `/raw` PUT → C1 emit) and **data-processing M0** (C1 → ASR → C2 →
   `/context`) **fan out in parallel** against the frozen C1/C2, both targeting storage's dev
   endpoints. Shared **C1/C2 conformance fixtures** (recording ⇄ data-processing) from day one, as
   the recording charter's C1-churn mitigation requires.
3. **platform** provides the box + ASR runtime alongside.
4. An **integrator** session wires them and drives one chunk end to end.

**Integrator exit criterion (capture v0 done):** a real audio chunk captured at the computer mic
lands in `/raw`; a C1 envelope reaches data-processing; ASR produces a C2 record that persists in
`/context` and is re-readable by `record_id` and by `(user_id, time)` range; re-delivering the same
`chunk_id` is a no-op (no dup blob, no dup record). No enrichment, no vision — just the capture
spine, proven.

### Learn-loop build conventions (v0) — so recording / data-processing / storage interoperate
- **Stack:** same as serve loop — Python 3.11, FastAPI + uvicorn per service, `httpx` inter-service,
  **pydantic** models mirroring `../contracts/*.json`, `pytest`. ASR = **faster-whisper** (POC
  Phase-1 stack), CPU-capable for the skeleton so it runs on any box; GPU is an optimization.
- **Storage endpoints (new, integrator finalizes exact paths):** `PUT /raw/blobs` (bytes +
  `chunk_id`/`user_id`/codec/sha256 → `{blob_ref, bytes, sha256}`, idempotent on `chunk_id`);
  `GET /raw/blobs/{blob_ref}` → bytes; `POST /context/records` (validates C2, idempotent on
  `record_id`); `GET /context/records/{id}` + `GET /context?user_id=&from=&to=` (time-range). Mirror
  the existing `/sessions` write style.
- **`/raw` dev layout:** local blob dir (like storage's SQLite dev DB); `blob_ref` an opaque
  storage-owned key; GCS is the production target (POC "GCS is source of truth").
- **Contracts are tested:** recording validates the C1 it emits; data-processing validates C1 it
  consumes + C2 it emits; storage validates C2 on write — all against `../contracts/*.json`.
- **No agent commits;** founders' session commits after integration. Honesty rule holds — the
  capture loop must actually run end-to-end before it is reported as run.

---

## Learn-loop capture M0 — build result (2026-07-09)

**Fan-out + integrator + adversarial verify.** The four workstreams (storage / data-processing /
recording / platform) built M0 in parallel against the frozen C1/C2; an integrator wired them and
drove one continuous-capture chunk end to end on live ports; an **independent verifier** re-ran the
suites and re-drove the loop itself (proving idempotency with its own `chunk_id`, reproducing the
real-ASR transcript byte-for-byte). Honest result below.

### What runs (executed, not claimed — independently re-verified)
- **The mock capture loop runs end to end on real uvicorn ports** (`run_learn.sh` health-gates
  storage:8083 → data-processing:8085 → recording:8084 — **first try, zero seam fixes**). One
  `/capture/run` carved a 12 s sample WAV into **3 dense, zero-based, wall-clock-stamped chunks**
  (`sequence=[0,1,2]`, one `stream_id`), each going **blob-first**: `PUT /raw/blobs` (storage mints
  the opaque `blob_ref`) → **push C1** to data-processing `/ingest` → C1 schema-validated → **pull
  bytes by `blob_ref`** → mock ASR → **C2** → `POST /context/records`.
- **Persistence + reads proven:** every C2 re-read by `record_id` **and** by `(user_id, time)`
  range (half-open `[from,to)`, matching C10), each provably sourced from a re-pullable `/raw` blob
  whose sha256 matches; per-user isolation holds (another user sees zero).
- **Idempotency proven on both legs:** re-delivering the same `chunk_id` returned the identical
  `blob_ref` and identical `record_id`, DB row counts unchanged (no dup blob, no dup record) —
  exactly-once under at-least-once. `record_id` verified deterministic: `sha256(chunk_id \x00 pipeline_version)`.
- **Contracts validated end-to-end** against the frozen JSON Schemas: the exact on-wire C1
  (captured via a validating tee) and all stored C2s validate with zero errors.
- **Bonus — real ASR genuinely ran:** the optional `faster_whisper` leg (base/int8/CPU) was
  installed + run live, producing a real transcript persisted as a schema-valid C2
  (`pipeline_version=asr-fw-v0`); the verifier reproduced it byte-for-byte. Standing backend
  restored to **mock**.

### Tests (re-run independently by the verifier, real counts)
| Service | Result |
|---|---|
| storage | **26 passed** (10 serve-loop unregressed + 16 capture-M0) |
| data-processing | **9 passed** |
| recording | **27 passed** |
| **total** | **62 passed, 0 failed** |

### Residual risks / explicitly NOT in M0 (feed the next slices)
- **Gap-detection is emit-side only, NOT enforced.** `(stream_id, sequence)` is emitted densely +
  schema-min-validated, but **no consumer detects a gap / lost chunk / duplicate sequence** at
  runtime. "Zero silent loss" is currently an affordance, not a check — closing it (a gap-detector
  on data-processing ingest feeding recording's continuity report) is the **top M1 item**: it is
  recording's headline mission guarantee.
- **Consent / authz: none.** Anyone can drive `/capture/run` + `/ingest`; delete-last-N /
  right-to-be-forgotten unimplemented. Recording's M2 (consent enforcement) must land **before any
  real always-on capture** — load-bearing precisely because capture is continuous.
- **Mock + file-source + single-stream:** mock ASR is the standing backend; capture reads a sample
  WAV (no real mic on this box); single device, single modality (audio), single process. Real mic
  (recording M1), diarization/enrichment, multi-device time-spine, vision/text pipelines,
  C8/C10/C11 are later slices.
- **Cross-chunk boundary stitching: out of M0 by design** — each chunk is ASR'd independently, so an
  utterance straddling a chunk edge is split (per the capture-model note). Later refinement.
- **Single-process in-memory dedup:** data-processing dedup is a per-process dict; cross-restart /
  multi-replica idempotency leans on `record_id` determinism → storage `/context` upsert (the
  durable backstop, exercised). A shared dedup store is later hardening.

**Exit criterion (capture v0 done): MET for the mock loop**, independently verified. Ports: storage
8083 · recording 8084 · data-processing 8085. Run guide: `services/platform/deploy/run_learn.sh`
(`--smoke` / `--status` / `--stop`).

---

## Modality seam — data-processing goes multi-modal (2026-07-10)

**Why.** The audio path was built; the C2 contract is modality-agnostic. So we refactored DP to a
**modality-agnostic core + a `Processor` plugin seam** so future sessions can each own one modality
(video / image / text) as a **disjoint, self-registering plugin** — zero shared-core edits. Two
parallel skeleton agents (DP seam + recording `ChunkSource` seam) + an adversarial verifier.

**What's built + proven (verified live + adversarially, 84 tests: storage 26 · DP 24 · recording 34).**
- **DP core** (`app/main.py` `/ingest` + `app/pipeline.py` + `app/dedup.py`): validate C1 → dedup on
  `chunk_id` (now caches `chunk_id → [record_id,…]`) → pull blob → dispatch by `modality` to a
  registered `Processor` → **for each returned unit** assemble+validate a C2 and POST `/context` →
  return `{ok, record_ids:[…]}`. Audio moved behind the seam **unchanged** (its `record_id` is
  byte-identical to the pre-seam value — backward compatible).
- **`Processor` seam** (`app/processing/`): a plugin sets `modality`+`content_kind` and implements
  `process(c1, blob, …) -> list[ProcessedUnit]` (a **list**, so *one chunk → many records* is native).
  Self-registering via `@register` + package auto-import — **adding a modality is one new file + a
  fixture, no core edit.** `record_id = sha256(chunk_id ∥ pipeline_version [∥ discriminator])`.
- **Stubs (mock transforms):** image→1 `caption` (OCR woven in per D8), **video→3 keyframe
  `caption`s (one-chunk-many-records, discriminator=index)**, text→1 `text`. All four `content.kind`s
  proven E2E to `/context` on live services against real storage-minted `blob_ref`s; every C2
  schema-valid; `record_id`s deterministic (recomputed + idempotent on re-POST).
- **Recording `ChunkSource` seam** (`app/sources/`): the carver generalized so future capturers plug
  in; the WAV source is one impl; C1 emit path unchanged; **no new real capturers**. C1 absorbed a
  non-audio (`image`) modality with **no additive field**.

**Regression caught + fixed (the verifier's honesty audit earned its keep).** DP's `/ingest` reshape
(`record_id` → `record_ids:[…]`) **broke recording's `/capture/run` live (HTTP 500** — `capturer.py`
still read the singular field, feeding `None`s into a `list[str]` model); green unit tests **masked**
it because recording's fake still returned the old shape. Fixed: capturer reads + **flattens**
`record_ids` across chunks; the fake returns the new shape (with a `fanout` knob); added a
**fan-out regression test** (3 chunks × 3 records → 9 flattened); **re-verified `/capture/run` → 200
live** with populated `record_ids`, C2 re-readable. Data was never lost (C2s always landed) — only
the API envelope was broken.

**Two C2-additive gaps surfaced by the pressure-test — both DEFERRED, both NON-blocking, neither
needs a version bump now** (recorded as DP charter OQs; the frozen C2 was NOT touched):
- **Video per-keyframe timing:** N keyframe records share the chunk's `t_start/t_end` → they collide
  on storage's `(user_id, t_start)` index. Fix is an **internal seam hook** (optional per-`ProcessedUnit`
  `t_start/t_end`; C2 already has per-record timestamps) — **no schema change.** Defer to the video session.
- **Image / keyframe OCR frame-location (bbox):** C2 `content` has no home for structured region
  geometry (OCR *text* survives, woven into the caption; only the bbox is lost). Fix = an **additive
  optional** field (`content.regions` / `enrichments.text_regions`) — touches the schema additively
  (old records still validate). Freeze-additive **when a real OCR pass lands.** Defer to the image session.

**Launch a modality session (the seam handoff).** To bolster video / image / text end-to-end:
1. DP: drop `app/processing/processors/<modality>.py` (a `Processor` subclass, `@register`) + a
   `tests/fixtures/<modality>.*` C1+blob — **no core edit**. Build the real pipeline (video: VidProc +
   keyframe captioning, wire per-keyframe timing via the seam hook; image: ImgProc + OCR-specialist +
   dense caption, add the bbox field additively; text: real normalization).
2. Recording (when its real capturer is wanted): drop `app/sources/<modality>_source.py` + one
   `SOURCE_BUILDERS` entry — no `capturer.py` edit, no C1 change.
3. Both write to the same frozen C1/C2 + the running storage `/raw`+`/context`; verify against
   `contracts/*.json` and drive via `run_learn.sh`.

---

## Post-capture-alpha sequencing — the DP deep session + what follows (2026-07-19)

**In flight — a DP-led deep-work session** (branch `svc/dp-async-observability`, worktree
`~/nmn/cl-dp-async`), launched in parallel with this founders' session. Its charge is work the
canvases already pin, bundled because it shares one service pair (DP + recording) and one node:

1. **Async `/ingest`** — DP charter **M7 territory arriving early** (the charter allows M4–M7
   to interleave after M3; video/M3 landed 2026-07-19). ACK `202` fast + process on a worker so
   capture cadence decouples from pipeline latency; retry safety rides the existing `chunk_id`
   dedup + deterministic `record_id`. Motivated by the verification-round finding that a fully
   loaded chunk (real ASR + diarization + VLM captions) can lawfully outlive recording's
   delivery timeout (fleet-mitigated today via `RECORDING_HTTP_TIMEOUT=120`). Resolves DP OQ13.
   **Scope discipline: this is the ACK+queue half of M7** — backpressure policy, dead-letter +
   backfill stay M7-proper; the queue lands observable by construction (queue depth is a
   chartered M8 metric in the same slice).
2. **D9 metrics emission** (DP **M8** + recording **M6**): `/metrics` Prometheus text + each
   service's Grafana dashboard JSON. **Emission half only** — Platform's shared
   Prometheus/Grafana backbone is the follow-on small slice (D15 below), so recording M6's
   "scraped by the shared Prometheus" exit criterion closes only when that backbone lands.
3. **node-7 smokes of the real audio backends** (pyannote diarization / whisper translation /
   AST acoustic events — built 2026-07-19 as correct-by-inspection seams, explicitly unrun):
   run each genuinely (GPU + HF-gated pyannote) before anyone trusts a switch-flip.
4. **The OQs the work naturally answers** — headline **recording OQ3, the codec/bitrate ladder
   (joint recording × DP)**: real pipelines + smokes say what fidelity each modality actually
   needs (alpha datapoint: CRF-28 mac screen video is readable but soft on fine text). Also
   informed: DP OQ3 (GPU placement for pipeline models vs continuum's future nightly window).

**Founders' ratification posture — the async `/ingest` reply shape.** It is an **inter-service
wire change, not a C-number**: C1 governs the envelope, not the reply, so the accepted shape
gets pinned as prose in the DP canvas exactly as the `/raw` blob leg rides D11. The deep
session proposes; this standing founders' session ratifies. The bar the proposal must clear:

- **At-least-once safety intact end-to-end.** Re-pushing a queued/in-flight `chunk_id` must be
  a cheap idempotent ACK, not a second enqueue. And the new loss window is named honestly:
  today, inline processing means a mid-processing DP crash fails recording's push → un-acked →
  recording retries → covered. **A `202` ACK closes that coverage** — once acked, recording
  never re-pushes, and DP's continuity detector notes "seen" at accept time, so a crash between
  ACK and processing would today be **silent** record-level loss. The proposal must either make
  the queue survive restart (durable spool, DP re-drains — the recording-side spool precedent)
  or make the loss *detected* (an accepted-vs-processed split visible to the gap report) with a
  re-drive path. "Accepted-risk + named mitigation" is not enough here — this is the zero-
  silent-loss guarantee itself.
- **Recording's consumer side moves in the same slice.** The capturer reads `/ingest` replies
  (`record_ids` today) and the gap report cross-checks DP `/continuity`; "accepted" and
  "processed" split under async, and the report must not read async lag as loss **nor claim
  `clean` while chunks are still pending** — verdict semantics need an explicit pending/drain
  signal, as `segment_states` already provides on the client leg.
- **Terminal outcomes stay discoverable.** `record_ids` reachable for whatever needs them
  (status poll, `/continuity`, or equivalent); worker failures land somewhere visible, not in a
  dropped task.
- **Mock-default + all three suites stay green; C1/C2 schemas untouched.**

**RATIFIED — 2026-07-19, same session (→ D16).** The deep session's FINAL design memo
(worktree `scratch/design_memo.md`, five-reviewer verified; its two load-bearing code claims
spot-checked here against `ledger.py:405` / `capturer.py:172` / `capture_web.py:297`) **clears
the bar and strengthens it**: it located the exact mechanism of the silent-loss clause —
recording's `_dp_missing_unacked` reconciliation trusts `dp_acked=1` to mean "C2 exists"; a
202-at-accept would redefine that to "merely accepted," so any accepted-then-lost chunk reads
`clean` — and made the fix **non-negotiable: preserve the invariant `dp_acked` == "C2 durably
written"**, dropping the original "zero recording change" claim as unsound (recording moves
in-slice, as the bar required). Ratified wire, pinned as prose in the DP canvas at merge:
- `INGEST_ASYNC=0` default, inline path byte-unchanged. Async: **202**
  `{ok, accepted, chunk_id}` on accept (+`duplicate:true` on a queued/in-flight dedup hit);
  **200 + record_ids** on a done-dedup-hit; deterministic rejections (400/422/501) resolve
  **synchronously pre-claim**, never deferred into a dead-letter; **503** = honest
  backpressure on a bounded queue (finite `INGEST_QUEUE_MAX` default — unbounded+volatile
  would OOM-lose-all and read `clean`).
- `/continuity/{stream_id}` gains **additive** `processed` (C2-durably-written runs) +
  `dead_lettered`; "covered ≠ processed". C1/C2 schemas untouched.
- Recording in-slice: `finalize_chunk(accepted=)` + additive `dp_state='accepted'` column;
  report reconciliation — accepted-unconfirmed → verdict **`recording`** (never `clean`),
  dead-lettered → **`gaps`**; `clean` now means "DP confirmed C2 for every chunk."
- **Honest loss boundary accepted:** this slice guarantees *never falsely `clean`* — all loss
  visible, none auto-recovered; auto-recovery (a durable DP pending journal) is explicitly
  M7-proper, and an in-memory DLQ-with-recovery illusion is explicitly rejected.

**One ratification condition:** the **re-drive path for accepted-unconfirmed chunks must be
named + drilled once in-slice** — emitter re-push keyed on `dp_state='accepted'` (on restart
or periodic), or a documented manual re-POST; either satisfies (DP idempotency makes re-push
safe: done-claim short-circuits to 200+record_ids). Without it, a `recording` verdict after
queue loss is visible but has no documented way back to confirmed until M7. **Accepted
caveat, noted:** `record_ids=[]` ledger provenance for 202-confirmed chunks (ids stay
derivable — deterministic on `(chunk_id, pipeline_version[, discriminator])`; inline/mock
fleet unaffected).

**Slice result — same day, merged (`0ce4941`; `dev` fast-forwarded with it).** The deep
session landed the full charge: the **D16 wire verbatim including the re-drive condition**
(`POST /capture/sessions/{id}/redrive` + `emitter.redrive_accepted_chunks` + 2 drill tests;
a re-drive that hits a done-claim also **backfills `record_ids`**, softening the accepted
caveat), D9 emission on both services (zero new deps, pure-ASGI middleware,
cardinality-bounded; both dashboard JSONs shipped), node-7 smokes of all three real audio
backends GREEN (+2 real pyannote torch-2.x fixes found by the smoke), **DP OQ13 resolved +
recording OQ3 answered per-modality** (no ladder: 16 kHz mono audio is model-native — the
existing demux target was already exactly right; video is resolution-bound not bitrate-bound
→ container-copy, ~2560 px only for OCR-heavy screens, cost dial = keyframe cadence). Their
18-agent adversarial round confirmed 9 findings — 5 fixed pre-merge, 1 deferred **fails
SAFE** (a DP-restart false-`gaps` over-report window; never hides loss, so
never-falsely-`clean` holds; the M7 durable journal closes it — land before async is trusted
for final archived verdicts). **Founders' merge review executed here:** all three suites
re-run independently — **DP 98 · recording 120 · storage 26, green** — and the condition +
OQ records verified in the diff. Detail:
[ws-async-observability](../services/data-processing/handoff/ws-async-observability.md).

**D15 — CLOSED (2026-07-25): the learn loop is proven end-to-end.** The continuum kickoff ran to
completion. The consolidation core (**Morpheus**, reimplemented from the research line `b3c58e1`,
parity-proven) trains a real 32B life adapter → gate v1.1 → C5 → **served in vLLM** (M0), behind a
lean 5-verb loop over storage client seams. The **Phase-3 DP dogfood** then routed real Speed data
through the **actual recording→DP→storage→continuum services** and — once block content matched
parity (the 1-min rule-bend's collapse was **dose**, not the pipeline) — **reproduced the baseline
separation (PIPELINE SOUND)**. The captured-days-are-inert-until-continuum-runs gap (below) is now
mechanically closed. **Remaining founder acts, both scheduled not blocking:** the **C10 freeze
becomes a C10 *evolution*** (raw range-read → **day-log fetch, random-access by `(user, window_id)`**)
folded into a **storage/C10 board session** that also ratifies the storage charter expansion
(day-log materialization + recipe registry + reservoir); and a **recipe/dose finding for Gnandeep**
(dose must scale with block-text at our native cadence). The cluster-split + DP-OQ5 items below rode
along: continuum's nightly window ran on node-7 via SLURM without disturbing Gnandeep's occupancy.
Detail: [../services/continuum/HANDOFF.md](../services/continuum/HANDOFF.md).

*Original D15 plan (for the record):*

1. **Continuum kickoff is the next founders-led slice.** It is the last unstarted pillar and
   the thesis itself: every upstream leg now stands (serve loop proven on real Qwen3-VL-32B;
   capture alpha-complete on three real surfaces; DP real for both live modalities; `/context`
   filling with pipeline-versioned records) — captured days are inert exactly until continuum
   runs. **Gate: a C10 v0 interface freeze first** (storage × continuum jointly propose,
   founders ratify — the pattern that made C1/C2 interoperate first try), frozen against the
   beta-proven range read (`GET /context/records?user_id=&from=&to=`, half-open `[from,to)` —
   deliberately C10's shape since D12). Kickoff is also the deliberate forcing function for two
   parked conversations: the **cluster split** (agenda item 2 — nightly training window vs
   Gnandeep's wider-cluster occupancy vs serving) and **DP OQ5 reprocess policy** (mixed
   `pipeline_version` dialects inside a training window). The long-parked **D6 OCR spot-check**
   rides the vLLM relaunch that continuum-era eval needs anyway.
2. **Platform D9 backbone as the small parallel slice:** the ONE shared Prometheus + Grafana on
   node-7, scraping what the deep session emits, provisioning both dashboard JSONs + the
   standard node/dcgm exporters. No file/service contention with kickoff; closes D9 end-to-end
   so both founders open one Grafana URL.
3. **DP image/text pipelines (charter M2) explicitly deferred until a producing surface
   exists.** Nothing on the fleet emits an `image` or `text` C1 stream today (phone camera →
   video; extension/mac → video+audio), and the image pipeline's chartered payload — on-screen
   text — already flows through the video keyframe OCR weave (D8). The OQ14b bbox additive C2
   field waits with it, by design. Revisit trigger: a screenshot-still / document / clipboard
   capture surface, or continuum finding keyframe-caption density insufficient for training.

Considered and passed, on the record so we don't re-litigate:
- **Mobile app + C8 sync API now** — mobile's v0 roles (chat surface + speech-output sink) get
  their differentiated value from a personalized model, and C8's one-dialect duty binds when
  interactive requests carry media. Their moment is when the personalization era opens (first
  adapter serving), not before.
- **Standalone C10 freeze without kickoff** — freezing a contract without its consumer at the
  table breaks the freeze-with-both-sides pattern that made C1/C2 land clean; meanwhile the
  beta is not blocked (it uses the range read as-is).

---

## Open agenda
0. ~~**NEXT SLICE — Data-collection (learn) loop MVP**~~ **SLICED + C1/C2 FROZEN + M0 BUILT +
   CAPTURE M1 + COMPUTER SURFACES — ALPHA COMPLETE (2026-07-19).** The learn loop is fully
   wrapped to the alpha bar: M0 skeleton → capture M1 (enforced gap-detection via the continuity
   ledger + DP break/dup detector; faster-whisper standing with a VAD gate; VAD-cut variable
   chunking, OQ4→D-M1-2) → three real capture clients on the `/capture/*` wire (phone web /
   Chrome-MV3 extension via `tabCapture` D-E7 / mac CLI), **all verified `clean` on real
   hardware**. Client transport pinned segmented-HTTP (**D14**; streaming ingest a deferred
   additive leg). **Next → now IN FLIGHT (2026-07-19): the DP-led deep session** (async
   `/ingest` M7-early · D9 emission · real-backend smokes · OQ3 — see §Post-capture-alpha
   sequencing above). Full state lives in the recording +
   data-processing canvases (this founders' thread links, not restates). The forward-looking
   plan that drove the slice is kept below for history.
   Original slice plan (2026-07-09, delivered): Skeleton = computer mic → ASR → `/context` (D10);
   C1/C2 frozen (D11),
   adversarially reviewed pre-freeze; M0 fan-out built (storage/data-processing/recording/platform),
   **the mock capture loop runs E2E on live ports + real-ASR leg ran once** (62 tests, idempotency
   proven, independently verified). **Next (capture M1, audio stream — staying on this component):**
   (1) **enforce gap-detection** on `(stream_id, sequence)` — the top item, it's recording's "zero
   silent loss" guarantee, currently emit-side only (a break/dup detector on data-processing ingest
   feeding recording's continuity report); (2) **async `/ingest`** — ACK `202` + process on a
   worker/queue so capture cadence decouples from ASR latency (dedup + `record_id` determinism keep
   retry safe; M0 is inline); (3) real computer-mic capture (recording M1) replacing the file
   source; (4) consent gate (recording M2) before any always-on capture; (5) **fuller audio
   pipeline** — **VAD gate → diarize → ASR → translate → acoustic-event captioning** (non-speech
   audio is *captioned, not dropped* — ambient sound is life-context signal; VAD also kills
   Whisper's silence-hallucination) + real faster-whisper as the standing backend; (6) chunk length:
   lift the M0 5 s placeholder to **~20–30 s + overlap** (recording OQ4, joint with DP).
   **Founders' sequencing (2026-07-18): recording-led.** Wrap the recording service as the next
   big gain (user-facing; gives the beta tester a touch-and-feel surface): items **(1)
   gap-detection** and **(5) the ASR pipeline** are the priority pair; capture surfaces to build
   behind the `ChunkSource` seam are **bodycam (device)** and **computer** — mic, screen
   recording, and browser-extension screen capture. Capture-modeling note: screen *video* and
   any system/tab *audio* are **separate C1 streams** (each with its own `stream_id`, like the
   wearable's A/V demux) — browsers expose tab/system audio via `getDisplayMedia`/`tabCapture`
   only on some platforms (Chrome: tab audio broadly, system audio Windows/ChromeOS; macOS needs
   a native-app loopback), and the mic is always captured as its own stream, never through the
   screen recorder. A recording-lead session (Prompt B + this scope) owns the slice.
   **Founders' refinement (2026-07-18, second pass):** consent gate → **back-burner (D13** —
   pre-pilot, not pre-beta). Capture-surface order: **(1) phone web client** (camera + mic via
   `getUserMedia` over HTTPS/tunnel — the bodycam stand-in AND the structured beta handover:
   Gnandeep gets a press-record URL; the live_video_chat POC already proved iOS capture +
   MediaRecorder + tunnel on this exact leg — reference, not lift, D7); **(2) computer** —
   screen video via app + browser-extension screen share, **tab audio** via the extension
   (`tabCapture`); system audio out of scope for now; computer mic continues from M0. The
   recording server demuxes phone A/V into per-modality C1 streams (charter OQ8 pattern).
   **Chunk-length lean (OQ4, pin in-session with DP): variable-length chunks cut at VAD speech
   pauses within ~5–30 s bounds** — frozen C1 already supports it (per-chunk `t_start`/`t_end`;
   `sequence` density is length-independent); semantic cuts avoid mid-sentence splits and may
   obviate audio overlap (exact `t_end[n] == t_start[n+1]` adjacency becomes a clean second
   continuity signal); fixed windows remain fine for video/screen streams.
1. ~~Serve-loop MVP slice~~ **DONE** (see build-result sections above).
2. Cluster split: which a3mega nodes serve (vLLM) vs train (continuum) vs pipeline work.
   **2026-07-18 interim answer:** Gnandeep runs continuum-side model-stabilization experiments
   across the wider cluster (the `engram` SLURM jobs — his workspace, outside this repo);
   product components keep **node-7**; allocate beyond one node on demand. Revisit when
   continuum's nightly window lands. **2026-07-19: continuum kickoff (D15) is the scheduled
   forcing function — settle the split at kickoff.**
3. Mobile app (now v0, D5) — one codebase serving both the chat surface (input) and the
   speech-output playback sink (output); sequence it after the computer text slice proves the loop.
   **2026-07-19: loop proven; considered at the D15 sequencing and passed for now — mobile's value
   binds to a personalized model (§Post-capture-alpha sequencing); revisit once the first adapter
   serves.**
4. **Observability & per-service dashboards** (CTO ask, **RATIFIED 2026-07-09, D9** — see
   [../ARCHITECTURE.md](../ARCHITECTURE.md) §Observability + [../STACK.md](../STACK.md) ports):
   each service **exposes a `/metrics` endpoint** (Prometheus text; instrumentation
   owned by the service — the service knows what to measure: request rate, latency histogram,
   error rate; inference adds GPU via dcgm-exporter; DB-touching services add query metrics).
   **Platform runs ONE shared Prometheus + Grafana** (pinned port) rather than 8 bespoke dashboard
   servers — each service ships a **Grafana dashboard JSON in its own repo** (per-service
   ownership), Platform provisions them into the shared Grafana. Both founders open one Grafana
   URL and pick any service. Standard exporters (node/dcgm/db) cover hardware/GPU/DB so services
   don't hand-roll them. Ports + the Grafana URL get pinned in [../STACK.md](../STACK.md) +
   [../ARCHITECTURE.md](../ARCHITECTURE.md) and each service's HANDOFF. Note: node/CPU graphs are
   placeholders until the true multi-node microservice split (CTO's own point); app-latency,
   error-rate, and GPU are the metrics that mean something today. Build as a near-term Platform
   slice (service agents instrument; Platform builds the backbone). **2026-07-19: the emission
   half (recording M6 + DP M8) is in flight via the deep session; the shared backbone is the
   D15 small parallel slice that follows.**

## Decisions
- **D3 Serve-loop first** (2026-07-09) — thin end-to-end backbone before capture/continuum.
- **POCs are reference, not source** (D7) — `poc/live_video_chat` informs the serve-loop
  contracts and streaming shape, but the production path is written fresh. No lift-and-shift.

## Worklog
- 2026-07-08 — thread seeded at product-structure standup.
- 2026-07-09 — build order locked (D3); BWM = Qwen3-VL-32B (D6); mobile app in v0 (D5);
  POC-no-reuse recorded (D7). Agenda refocused on slicing the serve-loop MVP.
- 2026-07-18 — **return sync (founders).** Cluster custody clarified: the vacation-week jobs
  are Gnandeep's continuum-side experiments; product keeps node-7 (agenda item 2 note). All
  repos committed + pushed (umbrella `main`, both POC submodules; `poc/live_video_chat` now
  tracked in the umbrella). Doc-hygiene pass over stale canvases (inference/storage/recording
  HANDOFFs, ARCHITECTURE/ORG ratification remnants, root README). Fleet on node-7 verified
  down — stale "Live now" note removed from the founders' board. **D12 recorded: branching +
  beta model** — service branches → `main` when solid; standing `dev` branch as the beta
  playground. First beta: Gnandeep drives the serve + learn loops against his fine-tunable
  model; storage's `/context` range read (`GET /context/records?user_id=&from=&to=`, half-open
  `[from,to)` — deliberately C10's read shape) is his training-window feed until C10 lands.
  **Next slice pinned: recording-led capture M1** (see agenda item 0 sequencing).
- 2026-07-19 — **post-capture-alpha sequencing (founders).** The capture alpha + DP modality
  slices being done and verified, a **DP-led deep session launched in parallel** (branch
  `svc/dp-async-observability`, worktree `~/nmn/cl-dp-async`): async `/ingest` (M7-early) ·
  D9 metrics emission (DP M8 + recording M6) · node-7 real-audio-backend smokes · OQ3 codec
  ladder (joint) + the OQs the work answers (DP OQ13 resolved by the slice). This session
  holds the board: pinned the **ratification bar for the async `/ingest` reply shape**
  (inter-service wire, not a C-number — §Post-capture-alpha sequencing; the silent-loss window
  a `202` ACK opens is the load-bearing clause), opened the escalation row, and recorded
  **D15** — continuum kickoff next (gated on a C10 v0 freeze, storage × continuum), Platform
  D9 backbone as the small parallel slice, DP image/text deferred until a producing surface
  exists; mobile+C8 and a standalone C10 freeze considered + passed. Learn fleet re-verified
  up on node-7 (storage/DP/recording healthy). **Later same session — ratification executed
  (→ D16):** the deep session's FINAL async-`/ingest` design memo arrived (five-reviewer
  verified; code claims spot-checked here) and was ratified — it strengthened the bar's
  headline clause into the non-negotiable **`dp_acked` == "C2 durably written"** invariant
  fix (recording moves in-slice; "zero recording change" dropped as unsound). One condition
  (accepted-unconfirmed **re-drive drill** in-slice) + one accepted caveat (202-path
  `record_ids=[]` provenance) recorded in the ratification block above; reply left in the
  deep session's scratch dir.
- 2026-07-19 (later) — **deep session LANDED + MERGED (`0ce4941`).** Founders' merge review:
  all three suites re-run independently (DP **98** / recording **120** / storage **26**
  green); D16 condition verified in code + drill tests; OQ3/OQ13 records confirmed in the
  charters. `dev` fast-forwarded with `main`. Board synced — **D15 is now the active
  sequence**: continuum kickoff (C10 freeze gate) + platform D9 backbone; fleet restart
  pending to begin emitting `/metrics`.
- 2026-07-20 — **DP v1 consumed + verified (founders).** The DP team shipped the durable
  ingest journal + stage-graph pipeline (`86acb95`, pushed; commit attribution-free).
  Founders' verification: suites re-run independently (**DP 128 / recording 120 / storage
  26** green), refs `main`=`dev`=origin confirmed, `INGEST_ASYNC` still 0-default, the
  fairness-knob startup warning present in code (`ingest_queue.py:88`). **The D16-era
  deferred false-`gaps` caveat is closed** (journal rehydration), so the async-trust rider
  is now satisfiable — the `INGEST_ASYNC` fleet flip is a live decision. Founders' caveat
  inventory prepared for a drill: (1) `INGEST_MODALITY_LIMITS` HOL-block; (2) a mutate-ordering
  rule in `resolve` as a hard prerequisite for any SECOND mutate stage (the ws drop-in table's
  own speaker-ID example would trip finding #7); (3) fingerprint-guard order-dependence (LOW).
  Also flagged: Architecture-Atlas custody vs the D2 single-doc protocol; the journal's
  `pipeline_version`-staleness reprocess mechanics as the first real OQ5 code (continuum input);
  `processed` retention; fleet behind (restart pending). Board synced.
- 2026-07-21 — **DP hardening slice consumed + verified; docs aligned to DP final state
  (founders).** The DP deep session shipped a hardening slice and merged it (`5350f7a`,
  conflict-free, carrying `aaebd88`; `dev`=`13bad86`; pushed). **It overtook the caveat drill
  by closing inventory items (1)–(3) structurally**: SlotView capability slot-ownership
  (fingerprint guard *deleted*), mutate `writes` + deterministic overlap chaining with
  chain-order in the dialect, and a permit-at-dispatch queue rewrite (fairness knob now
  production-safe, EXPERIMENTAL warning gone). Added opt-in `INGEST_ISOLATION=subprocess`
  (poison chunk → 1 chunk; drain-cancel SIGKILLs the ghost). A 47-agent adversarial round
  (19 confirmed / 2 refuted → 9 fixes + 7 drills) caught two HIGH bugs in the *new* code.
  Founders re-verified independently: **DP 163 / recording 120 / storage 26 green**, merge
  topology + attribution-free commits + off-by-default knobs checked; byte-identity re-proven
  by the session's own C2-digest diff. **Recorded engineering decisions absorbed from the ws
  file:** (a) **sync/inline path — KEEP** (considered retiring it "now that async is fast";
  it is the C8/M6 skeleton — the single shared `process_chunk` core plus ~40 lines of HTTP
  mapping — and the byte-identical verification baseline; flipping the async *production
  default* stays a founders' call after the D16 re-drive drill; retiring the inline *handler*
  waits until C8 lands its own surface — never the shared core); (b) **M7 is substantially
  done** (backpressure/dead-letter/durable-journal/kill-recovery/epochs/bounded-re-drive/
  fairness/isolation all ✓), remaining: dead-letter backfill tooling, reprocess-by-version
  drill at pilot-day scale, `processed` retention, warm child pool + wall-clock kill, and the
  **ops story for WHO restarts DP** (no supervisor config in-repo — platform owns deploy; must
  be confirmed before the M7 box is checked). **Doc-alignment pass (this session):** DP
  HANDOFF H-row → merged; recording HANDOFF given a DP-durability alignment note (guarantee now
  durable on both legs; `/redrive` stays belt-and-suspenders); ARCHITECTURE §Observability
  corrected (the shipped `/metrics` use a **zero-dep in-house emitter**, not
  `prometheus-fastapi-instrumentator` as the prose claimed); `requirements-video.txt` header
  clarified (no pip deps — system prereq + endpoint recipe). Verified accurate, no edit:
  storage docs (frozen C2 contract, unaffected), PROMPTS, root/services READMEs (serve-loop
  scoped). **Open threads into the next session** (all in the handoff docs): D16 re-drive drill
  (gate to flip async default), M1 WER/DER exit measurement, M2 text/image as the next
  unstarted charter work, supervisor/deploy confirmation with platform.

- 2026-07-26 — **Timezone: decided, then re-decided, then BUILT end to end (D17).** Review item
  **O-1** closed. This entry replaces an earlier same-day write-up whose conclusion was wrong; the
  reasoning is kept because the mistake is instructive.

  **Verified first (all confirmed, cheaply):** `context_records` had no tz column
  (`storage/app/db.py:78-88`; the only `timezone` token in `storage/app/*.py` was the UTC import);
  the wearer's tz was `nightly.py:27` `--tz`, default `"UTC"`. Three consumers rode on it, not
  two — the window boundary, `_render_block`, **and `cycle.py:217`**, which rebuilds *prior*
  windows with *tonight's* tz under `replay_source="rawlog"`.

  **The first D17 was wrong.** It gave storage a per-user `home_tz` *only*, banned tz from C1/C2,
  and deferred travel. Two errors: (a) it applied the emission law's **T2 as a veto** on a field
  whose consumer this very slice builds — T2's own text says it is "a gate on *when*, not a veto";
  (b) it defended a `window_id`-total-order problem (dateline → one id for two windows) that only
  exists because `window_for` derives bounds from a **local date** — and the watermark window
  ARCHITECTURE's C10 row already specified dissolves it.

  **The fact that settled it:** the capturing device already knows, and we were discarding it at
  the first step — `new Date(seg.tStart).toISOString()` (`clients/web/app.js:262`,
  `clients/extension/uploader.js:110`) computes the local instant and throws the zone away on the
  same line. `Intl.DateTimeFormat().resolvedOptions().timeZone` is one line away. Meanwhile C1
  collected `device_location` and **C2 dropped it**, and `device_location`/`device_clock` were
  declared in both recording's and DP's `models.py` and read by neither — dead fields.

  **D17 as ratified: the device owns the FACT, storage owns the POLICY.** Per-chunk `device_tz` +
  `device_utc_offset_minutes` on C1 → verbatim through DP → C2 `source{}` → storage columns →
  continuum's renderer. Per-user `home_tz` does **scheduling** (when does this user's night fire?)
  and **fallback** only. UTC stays canonical and is the only ordering/range axis. Two standing
  rules: never persist a derived local wall-clock (two sources of truth that will disagree), never
  accept an abbreviation (`PST` is ambiguous + DST-sensitive).

  **Why the offset is NOT redundant with the zone:** it records what the device *believed* at
  capture. When a tzdata build is stale or wrong, the offset is the only independent witness — the
  zone id alone would silently re-derive the wrong wall clock forever.

  **Built, in ORG.md:44-45 order** (ARCHITECTURE §Contracts → `contracts/` → owning canvases):
  | Hop | Change |
  |---|---|
  | Clients | web + extension `civilTime()`; mac `local_iana_zone()` + `civil_time_params()` (stdlib-only — `/etc/localtime` symlink, because `time.tzname` yields forbidden abbreviations). **Offset evaluated at the chunk's own instant**, so a chunk either side of a DST flip carries the true offset (tested: LA −480 in January, −420 in July) |
  | Recording | `/capture/segments` accepts + **validates at the edge** (abbreviation → 400, unknown IANA id → 400, out-of-range offset → 422); ledger columns + additive migration; `_build_envelope` **omits** rather than nulls; carried on the **`/redrive`** path too (the query already joined `segments`) |
  | DP | `build_c2` copies `device_tz`, `device_utc_offset_minutes` **and the long-dropped `device_location`** verbatim. Zero timezone logic. `C1Envelope`/`C2Source` are `extra="forbid"` — undeclared fields would have been **rejected**, so the models had to move with the schema |
  | Storage | promoted columns beside the UTC instant (a civil-time query is a column read, not a JSON scan) + additive migration; `record_json` still served **byte-verbatim**. Its pydantic `Source` mirror is `_Strict` and **caught the omission in test** — schema and mirror must move together |
  | Continuum | `Segment.tz` + `_block_zone()`: device zone wins, window `home_tz` falls back, an unresolvable id **degrades instead of sinking the night**. `--tz` now **required** |

  **Deliberately no backfill on either migration.** A record captured before clients reported a
  zone genuinely has none; inventing one (the server's, `"UTC"`) is precisely the silently-wrong
  timezone failure this slice removes. NULL is honest and reads as "fall back to `home_tz`".

  **Cross-service E2E:** a Tokyo chunk through recording → DP → storage → continuum, with the
  operator's fallback deliberately set to UTC, renders **"around 15:00 local time"**. Pre-D17 the
  identical run rendered **"06:00"** — a UTC reading labelled local, no error, no metric.

  **Suites: storage 32 · continuum 189 · recording 144 · DP 770 (+21 skipped) · extension deno 11.
  +26 tests, zero regressions.** Notable coverage: two zones in one window rendering independently
  *with different local dates* (the case one-tz-per-window structurally cannot express); both
  migrations against hand-built pre-D17 schemas; and **`record_id` stability** — civil-time context
  is provenance, not a dialect input, so it must not fork the record or every existing record would
  re-key on upgrade.

  **OQs closed:** storage **OQ3** (clock skew — the "normalize upstream" lean was right and is now
  built: nobody normalizes, and skew is *detectable* from `device_clock` + offset + `ingest_time`
  rather than silently corrected) · DP **OQ4** (device clock discipline) · DP **OQ9** substantially
  (envelope time suffices, now auditable; residual = nobody *measures* the disagreement rate yet) ·
  continuum **OQ10**'s timezone half (a tz is needed for scheduling only) · **E-4**'s premise.

  **Specified, NOT built — the board's own agenda, not for an unreviewed slip:** day-log
  materialization continuum → storage, and the cycle window → watermark `[last_trained_t, now)`.
  Both are written into the ARCHITECTURE C10 row. The second retires `window_for` entirely; it also
  changes training-window semantics (`window_id` keys the journal, C5 `training_window`, and
  publish's alias monotonicity), which is exactly why it wants the board and not a side-effect.

  **Fleet applied (same session).** node-7's learn fleet was restarted onto the new code; both
  SQLite migrations ran against live data. Backed up first with SQLite's online-backup API →
  `/home/ubuntu/nmn/backups/pre-d17-20260726-211912/`. Post-restart verification:
  `context_records` **125/125 rows intact**, both columns present, **all NULL** (no backfill by
  design); `segments` **40/40 intact**, both columns present. The **`dp_state` migration also
  fired**, backfilling all **68 chunks** → `processed`: the running processes dated from
  2026-07-19 and predated the D16-async, v1-journal and hardening merges, so this restart
  collected those too — the board's long-standing "fleet is behind" note is now cleared.
  Live wire checks against the running service: `device_tz=Asia/Tokyo` accepted + persisted;
  `device_tz=PST` **400**. A real `--smoke` run carved 3 chunks through faster-whisper →
  C2 → `/context`; those records correctly **omit** the civil-time fields, since headless
  `/capture/run` has no reporting device — absence, not null, is the contract. Verification rows
  deleted afterwards (ledger back to its 40-segment baseline).

  **Correction before commit (O-12, pass 2 of the review).** D17's headline read "BUILT + verified
  end to end" while the row also carried the **watermark-window** clause — which is decided, not
  built (`window_for()`/`closed_window_before()` are still local-date; `nightly.py` still calls
  them). Fair catch: the Decisions log is the most authoritative doc we have, so a blanket BUILT
  claim over a two-part decision is exactly the kind of drift this session existed to remove. D17's
  status is now **split explicitly** — timezone split BUILT + verified, watermark window
  DECIDED/pending — and ARCHITECTURE's C10 row is reworded from "the cycle window is" to "is to
  become … and is still what runs". Both now name the blocking design question: `window_id` is the
  local start date and keys the day-log, the cycle journal, C5's `training_window`, and publish's
  active-alias monotonicity; the natural watermark key is the window's **end instant** (monotone per
  user, no dateline case), but that changes the `w2026-07-21` format and **forks adapter lineage** —
  a board call, not a refactor.

- 2026-07-26 (later) — **Review items O-2 / O-3 / O-4 closed: three doc defects, three ceremony
  levels.** They arrived as one batch of eight edits across five files. Treating them uniformly was
  the trap; sorting them by *shape* first is what made the ceremony obvious, and none of the three
  needed a D-number.

  | Item | Shape | Ceremony | Why |
  |---|---|---|---|
  | **O-2** — C5's field list short in four places | **INCOMPLETE description** — one truth, written down only partially | The real judgment call (below); doc edits + one comment-only code edit | Nothing is in dispute: `publish.py:83-99` writes nine fields, `record_gate_failure` (`:101-114`) writes a third status. The docs simply hadn't caught up |
  | **O-3** — "LoRA over all layers" | **INTENT/BUILD GAP** — two truths about different things | Lowest; state both, name the gap | Neither side is wrong. The defect was asserting the *intent* in the voice of the *build*, so a newcomer read the charter and believed vision towers were adapted |
  | **O-4** — C2 `record_id` discriminator | **PROSE LAGGING ITS OWN AUTHORITATIVE SCHEMA** — one truth, already recorded correctly | Lowest; **explicitly not a contract change** | The frozen schema has mandated the discriminator since v0; only ARCHITECTURE's summary lagged. Confirmed before editing, against the CURRENT file (post-D17), not from memory |

  **Verified before editing, not taken on the brief's word:** `LM_PROJECTIONS` + `_LM_SCOPE` and
  their in-source rationale (`morpheus/train.py:27-32`), `lora_target_modules()` (`:89-100`), the
  252/252 parity row ([phase-2a-report](../services/continuum/handoff/phase-2a-report.md):60), the
  nine-field `publish()` entry and the `gate_failed` row, the C2 schema's current
  `record_id.description`, `compute_record_id`'s empty-discriminator byte-identity
  (`data-processing/app/pipeline.py:33-46`), and all eight target lines.

  **O-3 — the reframe worth keeping.** "All layers" mis-names the axis. v0 *does* adapt every
  layer — all 36 — at all 7 projections. What it excludes is a **tower**, not a layer. So the gap
  is *which stacks*, one axis not two, and that is how it is now written. Both facts the CTO asked
  to shape the wording ride beside it, so nobody re-derives the argument: **flipping back is cheap**
  (a module-name-filter change plus a re-parity run — an option kept open, not a door closed), and
  **the exclusion's premise is falsifiable and self-expiring** (it holds only while the day log
  reaches the trainer as text; the day DP feeds it pixels, the reason evaporates on its own).
  ARCHITECTURE §Decisions keeps "all layers" as the intent — still sourced to `start.md`, an
  inherited founding assumption, never a ratified D-number.

  **O-2 — the judgment call, taken explicitly: (a) describe as-built now, labelled.** C5 is
  deliberately unfrozen (`publish.py:3-4`) and **this session did not freeze it** — inference is not
  at the table, and the C5 → model directory → C6 → vLLM tail is still unwired. The counter-argument
  was real (a field list in ARCHITECTURE §Contracts is how things become de-facto frozen around
  here) and is answered **inside the wording rather than by silence**: the *"as built, not frozen"*
  label rides in the same table cell as the field list, so the row cannot be quoted without its
  caveat. A known-wrong description should not outlive a session that has no date. The most costly
  omission was not a field but a **status**: all four sites said `active/rolled-back`, hiding
  `gate_failed` — the audit trail for a blocked candidate, i.e. the thing a reader most needs to
  know exists.

  **`gate_failed` is not only documentation — it constrains the storage build at freeze time.**
  Storage's `model_directory` today is the *trivial C6 row* (`user_id, model_id, adapter,
  adapter_path` — `storage/app/db.py:59-63`): no entries log, no status column. So "the
  storage-hosted swap-in is a transport change, not a redesign" is true of *continuum*, not of
  storage — storage has to build the log, and the short field list is exactly what an implementer
  would have built to. Three constraints, now written into the storage charter's model-directory
  row so they reach the freeze session: **(1)** `status` must be a **three**-value enum, or
  `record_gate_failure()` has nowhere to land and the audit trail is dropped silently at the swap;
  **(2)** `gate_failed` rows carry **NULL `adapter_dir` and NULL `base_model_hash`**, so neither
  column can be `NOT NULL` — a schema derived from the happy path would reject precisely the rows
  that matter most; **(3)** C6 eligibility is a **log replay** (`active` pushes, `rolled_back` pops
  the matching top, **`gate_failed` does neither** — `publish.py:33-44`), *not* "latest row wins" —
  a directory resolving the newest entry would serve a gate-failed candidate, which is the ungated
  swap the gate exists to prevent.

  **Eight sites edited** (only O-4 touched contract prose, and it was a summary catching up to its
  own schema — no [ORG.md](../ORG.md):42-45 contract-change routing was owed, and none was
  performed): ARCHITECTURE §Contracts C2 block + C5 row + §Decisions Personalization row · continuum
  CHARTER mission + scope + C5 row · storage CHARTER model-directory row ·
  `continuum/app/publish.py` module docstring (**comment-only**; continuum suite re-run after —
  **195 passed, 1 skipped**, the same 196 collected as the headless 189+7 baseline, with more
  parity tests simply running in this env). LEARN_LOOP §3 (C2 + C5) and §8 items 3 + 12 updated to
  match; O-2/O-3/O-4 struck from REVIEW_NOTES, which now carries only the seven charter/canvas
  hygiene items (O-5…O-11).

  **Two sites the review's O-3 inventory missed — found by a repo-wide sweep after the three
  listed edits, and fixed in the same pass.** [VISION.md](../VISION.md):68 and :104 also say "all
  layers", and **:68 is the worst instance of the defect anywhere in the repo**: it reads *"v0
  mechanism (**locked**): per-user LoRA over all layers"* — the intent asserted not merely in the
  build's voice but as a *locked v0 mechanism*, in the company's most-quoted document. Fixed
  minimally rather than rewritten (VISION should stay a vision doc): "all layers" survives as the
  stated intent, with a parenthetical naming the as-built narrowing and pointing at continuum's
  charter for the argument. **Lesson for the next review pass:** an inventory built by reading the
  *engineering* docs will miss VISION.md — `grep -rn "all layers" --include=*.md` takes one second
  and would have caught it at pass 1.

- 2026-07-26 (later — **founders' storage/C10 board: D18**). Ratified the storage scope expansion
  and the C10 evolution. **Everything below is DECIDED, NOT BUILT** — stated that way from the
  first draft rather than corrected in afterwards, which is the O-12 lesson applied prospectively.

  **The gate first, because it decided the rest.** All five `window_id` claims in the launch prompt
  verified. Three more consumers found that it did not list: **`publish.py:106`** is a *fourth*
  string comparison (the alias-monotonicity guard itself — the prompt named only `active_before` at
  `:83`), and **`window.py:44`** + **`reservoir.py:65-69`** *parse the id back into a date*, which
  **`cycle.py:217`** then uses to rebuild prior windows under *tonight's* timezone.

  **A premise of the prompt was wrong, and correcting it made the session easier, not harder.**
  The stated HARD CONSTRAINT — "`render_block` must stay byte-parity with the research line @
  `b3c58e1`; moving the renderer must not break it" — conflates two functions with the same name:

  | | Locked by | Consumes | Verdict |
  |---|---|---|---|
  | `Profile.render_block` (`morpheus/profiles/speed.py:89`) | `tests/parity/test_render_block.py`, 1427/1427 vs research goldens | 5-min description dicts | **recipe-coupled — STAYS** |
  | `daylog.py:183 _render_block` | nothing; no golden has ever existed | C2 records | **moves to storage** |

  The research line never materialized the 10 s-segment/2 min-block schema (continuum's own canvas:
  "zero producing code; a research 'block' = one 5-min description"), so the product renderer could
  not have had a golden. `morpheus/blocks.py:5-7` had already drawn the boundary — *"keeping that
  boundary narrow is what lets the day-log move behind a storage client without any kernel
  noticing."* So the move **cannot** break research parity. The bar became a **differential
  byte-equality** against our own current output for a real window — script + result committed, DP's
  byte-identity precedent — with continuum's local path **not deleted until that diff is green**.
  That is strictly more falsifiable than re-running a golden suite that never covered this code.

  **The four decisions.** *(0)* `window_id` → `w<YYYYMMDD>T<HHMMSS>Z`, minted once from the window's
  **end instant**, **parsed by nobody**; seconds not minutes because a truncating id can silently
  collide two windows. The format change is a **consequence** of the watermark window, not a cost of
  it: `[last_trained_t, now−δ)` has no local date to name. Re-keys enumerated in D18 including the
  **training seed** (`cycle.py:147`) — accepted as a real discontinuity rather than papered over,
  and `tests/parity/` is unaffected because it seeds from its own harness. `w-day5` ruled **a mess,
  not a precedent** (`m0_smoke.py:133` breaks the total order twice over), so the durable output is
  **one minter + one validator**. *(1)* **C12** per-user profile — a *profile*, fenced to
  system-read policy values, `home_tz` only in v0, **404 on absence**, and **declared, not inferred** —
  the user sets it and storage never writes it unprompted (corrected same day; D18's first
  draft had storage auto-seed from the first device-reported zone, which would have let a
  guess masquerade as an answer). Schema written (`contracts/c12_user_profile.v0.json`); **C13** recipe registry and
  **C14** reservoir minted, schemas land with the build slice per `contracts/README.md`'s own rule.
  *(2)* Day-log → storage. The decisive argument is **replay**: it re-reads *prior* day-logs nightly,
  so a continuum-side builder re-pulls every prior day's raw records every night — **O(days²)** to
  rebuild what storage could have kept. *(3)* The window watermarks on **`ingest_time`**, which
  *dissolves* late data rather than handling it; `last_trained_t` advances **only** on
  `published`/`skipped_no_data`, which makes the design-of-record's **failed-day merge structural**
  and demotes `_UserState.debt` to reporting; reprocessed records resolve **latest `ingest_time`
  wins per `(chunk_id, kind, discriminator)`** — on `ingest_time` because `pipeline_version` is a
  *composed* string and not orderable, on `kind` because Phase-3 proved captions and transcripts
  share one.

  **E-2 demoted, not dropped.** The one-dialect materialization rule is what actually fixes the
  WS-VC double-count, so E-2 stops gating the cutover and reverts to the retraction/privacy/space
  primitive it always was. Its shape rides storage M5.

  **Two obligations this session opened rather than closed** — recorded because a decision session
  that only closes things is not being honest: **(a)** the day-log and reservoir are *second copies
  of user content*, so **M5's deletion must cascade to both** (a retraction that clears `/context`
  and leaves a day-log standing has deleted nothing); **(b)** the **within-chunk discriminator is
  not independently readable from C2** — it lives only inside the `record_id` hash — so the build
  must either surface it as an additive-optional C2 field (ARCHITECTURE → schema → **both**
  `extra="forbid"` mirrors, the exact D17 trap) or prove `(chunk_id, kind, t_start)` unique per
  dialect. **Do not start the materializer before that is chosen.**

  **Also corrected:** "storage OQ8" does not exist. Blob-by-reference is **recording's** OQ8
  (`recording/CHARTER.md:175`); the mislabel originated at `ws-phase3-dogfood.md:55` and had
  propagated into this session's own launch prompt. Storage's OQ list now carries a note that OQ
  numbers are stable ids and are never renumbered.

  **No code changed; no suite was run this session.** Baselines stand as of D17 — storage 32 ·
  continuum 189 · recording 144 · DP 770 (+21 skipped) · extension deno 11.

- 2026-07-27 — **D19: the stage is PROTOTYPE, and the docs now say so.** The founders' read: every
  canvas here is written in a production voice, so a fresh session builds for durability we have not
  earned. Fixed globally (**ARCHITECTURE §Stage**, **ORG §Stage**) and locally (a banner on **all
  eight** service charters). The posture licenses re-cutting contracts rather than versioning them,
  wiping data rather than migrating it, and deferring durability work with the reason recorded — and
  explicitly does **not** license skipping ORG's contract-edit order, leaving a decision unwritten,
  or calling a thing BUILT when it is DECIDED.

  **Seven calls taken under it.** Retention → **keep everything**, but the *knob ships and the
  policy does not*: a versioned per-store retention document, every store `keep_forever`, read and
  surfaced on `/metrics`, **no sweeper built**, and rules that mark *eligibility* while an explicit
  sweep acts and writes a manifest — so a bad config edit can produce a wrong report, never silent
  data loss. Storage tech → **local now, option (c) later**, kept cheap by a rule rather than
  foresight (every new store behind a narrow interface from day one, the shape continuum already
  proved client-side). C2 `discriminator` → **surfaced** rather than inferred from
  `(chunk_id, kind, t_start)` uniqueness, taking the option that cannot rot now that contracts are
  explicitly not frozen; it adds no promise (DP already rejects duplicates within a chunk) and
  `record_id` is unchanged, so nothing re-keys. Existing state → **wiped, not migrated**, which is
  what makes D18's `window_id` reformat genuinely free. Cycle trigger → **cron at the user's
  `home_tz` boundary, interval configurable**; materialization **on demand at fetch**, buying a slow
  first fetch to delete a whole scheduler; min-data floor → a recipe knob in *characters of block
  text*, default 0. C5 freeze → **deferred**, free because C5 is unfrozen, with one standing rule
  recorded for whoever freezes it (`training_window` must be frozen **opaque, never as a date**).

  **Two corrections to D18, both from the CTO's answers:**
  1. **`home_tz` is declared, not inferred** — D18's first draft had storage auto-seed it from the
     first device-reported `device_tz`. Wrong: a guessed zone and a chosen zone are different facts,
     and only the second can be corrected by the person who knows. The CTO's question — *should it
     change when the user travels?* — has exactly one right answer, **no**, and it is the whole
     point of D17's FACT/POLICY split: a week in Tokyo moves every record's `device_tz` and moves
     nothing here, so the boundary stays put instead of jumping 9 h and producing a 15 h night
     followed by a 33 h one.
  2. **The watermark advances if and only if a cycle PUBLISHES** — D18 also advanced on
     `skipped_no_data`. My own refinement, taken because the min-data floor forces it: a below-floor
     night must not advance or the material is lost. Unifying gives one sentence covering gate
     failure, freeze, crash, no data and too-little data, and makes the name literally true —
     `last_trained_t` is the high-water mark of what has actually been trained. Named cost: an
     inactive user's open window grows and is re-scanned nightly, which is correct and cheap.

  **Commits:** the tree was split into two — `6bb8f4a` (O-2/O-3/O-4 doc slice) and `b96a1b0` (D18).
  Perfect hunk attribution was not achievable: the two overlapping sessions' edits had merged into
  shared hunks in four docs, so the split is at file granularity and `b96a1b0`'s message says so
  rather than implying a cleanliness it does not have.

- 2026-07-27 (overnight) — **the D18 storage expansion is BUILT, and the storage↔continuum seam is
  closed.** Three founders' decisions carried it: **D19** (prototype posture + seven calls),
  **D20** (M9's parity bar narrowed after it failed, plus a *definition of done* — because the
  original wipe gate, "no defects, no artifacts", is unfalsifiable and would justify reviewing
  forever). Commits `a5a48fb` storage · `1757efb` continuum · `2698b63` data-processing.

  **What now exists.** Storage owns the day-log, the training-window ledger and the `window_id`
  minter, and hosts C12/C13/C14; continuum issues a warrant for `(user_id, window_id)` and takes
  what comes back. `window_for()`, `closed_window_before()`, `Window.local_date` and
  `ReservoirEntry.local_window_date()` are **deleted**, and with them `cycle.py`'s reconstruction of
  prior windows under *tonight's* timezone. `nightly.py --tz` is retired for the C12 profile read.

  **Four review rounds ran; every one found something real.** Recorded because the pattern is the
  lesson, not the individual bugs:
  | Round | Headline finding |
  |---|---|
  | 1 | the day-log stamped a `recipe_id` whose knobs it never used — a night auditable as trained under a recipe it was not |
  | 2 | a corrected `home_tz` never re-materialized the cached day-log; a naive `now` read as server-local |
  | 3 | a crash **closed** the window, so a retry minted a *second* `window_id` — measured: full re-train, 2 C5 entries, 2 reservoir admissions |
  | 3 | the **default** nightly path fed ingest-time bounds into an event-time query — measured result: an empty day-log. The shipped default trained on nothing |
  | 3 | the M9 proof held only for a **grid-aligned** window origin, which no real window has |

  Round 3's middle finding is the one to remember: the seam had 148 green checks *while the default
  configuration silently trained on nothing*. Green harnesses proved the paths they exercised, and
  the default was not one of them.

  **Two corrections to my own prior claims**, both recorded rather than quietly fixed: I reported
  the parity proof as PASS when it covered only an aligned origin (a stronger claim than the
  evidence supported), and my watermark refinement reached three of four sites, leaving
  ARCHITECTURE — the doc named authoritative — contradicting the code.

  **A cross-service coupling nobody knew about.** Deleting `window_for()` broke *data-processing*:
  `scripts/prompt_ab.py` imported continuum's `app/window.py` across the service boundary. DP's
  suite caught it (788 → 787). Fixed by building the `Window` value object inline, reproducing the
  old 04:00Z/24 h semantics exactly, because those numbers are compared across runs.

  **On test staleness (CTO, 2026-07-27):** tests must evolve, and deleting is sometimes right — but
  "eliminate staleness" collapses into "make it green" unless you can name which case you are in.
  All four occurred here: *stale assertion* → two storage tests pinning `recipe_id ==
  "consolidation-v1.0"`, rewritten to assert the contract (`== daylog_recipe_id()`); *deliberate
  behaviour change* → the crash test, whose assertion was **inverted**; *test right, code broken* →
  DP's, which deleting would have hidden; *behaviour genuinely gone* → the `window_for` tests.

  **Recipe `consolidation-v1.1`** minted and **both** services re-pinned. It forks v1.0 on exactly
  one knob — `replay.source` `amp` → `rawlog` — proven by diffing the artifacts. Under `amp` only a
  user's FIRST night could run over HTTP: amp pools amplified corpus *bodies* and C14 serves a
  *ledger* by design. v1.0 is **retained**, being the recipe the Phase-1/Phase-3 numbers were
  produced under. The re-pin is deliberately two-sided — storage stamps the day-log's `recipe_id`,
  continuum records C5 lineage, so a one-sided re-pin trains under a recipe the artifact is not
  labelled with.

  **D20's bar, verified by the founders' session rather than relayed:** storage **310** · continuum
  **251** +7 skipped · recording **144** · data-processing **788** +21 skipped · M9 parity **PASS,
  31 binding checks over 2 window origins including a misaligned one** (exit 0) · live two-process
  seam **PASS, 10 steps / 151 checks, zero blockers** (exit 0) · `app/morpheus/` and `tests/parity/`
  **byte-unchanged**.

- 2026-07-27 (close-out) — **the seam is shipped, the fleet is live, and the review backlog is
  empty.** Final round fixed three defects an adversarial pass proved against the real services and
  no suite caught: **H1** the publish tail was not atomic (two `active` C5 rows for one window, and
  one `rollback()` flipping the alias to the *same* adapter — the only safety net a bad adapter has,
  silently dead; worse branch, a C14 append-only 409 that stranded a night forever); **H2** the
  replay pool filtered on ledger *state* and never *outcome*, so nights that never entered the
  adapter were rehearsed — measured at 50% of a night's budget re-teaching text already in that
  night's fresh corpus; **M1** one window could strike twice and freeze a user. Fixed at the source:
  `publish()` idempotent with one live activation per window, a reservoir conflict non-fatal,
  `prior_windows` published-only, `debt` membership as the strike guard. 11 tests, each proven to
  fail without its fix.

  **`seam_check` was asserting H2 as correct behaviour** — green while pinning the bug. That is the
  second harness this week to bless a defect (the `recipe_id` test did it first), and it is the
  reason the review rounds kept finding what the suites could not.

  **Fleet cut over:** stopped → three stores backed up and *verified restorable* → cleared →
  restarted on the new code. Proven live rather than in tests: real capture through faster-whisper
  into `/context`; a C12 profile set and a missing one 404'd; a nightly to **published** over HTTP;
  the watermark advancing **only** on `published`; **exactly one** active C5 row.

  **Doc close-out:** all eleven accuracy-review items are closed (O-1 → D17, O-2/3/4 → 07-26,
  O-5…O-11 → today), storage OQ7 resolved, OQ9 re-scoped now that `day_logs` is a real table, and
  **two false claims of my own retracted** — D19's "the min-data-floor mechanism exists" (it appears
  nowhere in the repo) and the parity PASS I reported before it covered a misaligned origin. Both
  were caught by adversarial rounds rather than tests, which is the week's actual lesson: every
  serious defect started as a document disagreeing with the code.
