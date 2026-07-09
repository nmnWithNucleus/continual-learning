# Founders' thread — Engineering

> Running canvas for founders' engineering sessions (launch: [../PROMPTS.md](../PROMPTS.md) §D).
> Cross-service build sequencing, integration plans, infra calls. Service-internal
> engineering lives in each service's canvas, not here.

**Status:** active · **Last updated:** 2026-07-09

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

**Deliberate follow-up (its own slice):** push vLLM 0.19.1 → **≥0.20 with CUDA-13 (cu13) wheels +
matching flash-attn** to leverage driver 580. Kept out of the finish-line step on purpose (one
variable at a time). Also: the D6 OCR spot-check on real screen-capture data can run now that the
model serves.

---

## Open agenda
1. **Serve-loop MVP slice** — build order is locked (serve-loop first, D3). Next: cut the
   thin backbone (computer text surface → QueryBuilder → inference on base Qwen3-VL-32B →
   output text stream; session/turn stored) into workstreams and decide which service leads
   to launch first. Inference is the heart; input's M0 interface-freeze (C3/C8) gates the
   others, so it likely goes first or jointly.
2. Cluster split: which a3mega nodes serve (vLLM) vs train (continuum) vs pipeline work.
3. Mobile app (now v0, D5) — one codebase serving both the chat surface (input) and the
   speech-output playback sink (output); sequence it after the computer text slice proves the loop.
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
   slice (service agents instrument; Platform builds the backbone).

## Decisions
- **D3 Serve-loop first** (2026-07-09) — thin end-to-end backbone before capture/continuum.
- **POCs are reference, not source** (D7) — `poc/live_video_chat` informs the serve-loop
  contracts and streaming shape, but the production path is written fresh. No lift-and-shift.

## Worklog
- 2026-07-08 — thread seeded at product-structure standup.
- 2026-07-09 — build order locked (D3); BWM = Qwen3-VL-32B (D6); mobile app in v0 (D5);
  POC-no-reuse recorded (D7). Agenda refocused on slicing the serve-loop MVP.
