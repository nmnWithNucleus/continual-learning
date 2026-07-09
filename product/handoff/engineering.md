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

## Open agenda
1. **Serve-loop MVP slice** — build order is locked (serve-loop first, D3). Next: cut the
   thin backbone (computer text surface → QueryBuilder → inference on base Qwen3-VL-32B →
   output text stream; session/turn stored) into workstreams and decide which service leads
   to launch first. Inference is the heart; input's M0 interface-freeze (C3/C8) gates the
   others, so it likely goes first or jointly.
2. Cluster split: which a3mega nodes serve (vLLM) vs train (continuum) vs pipeline work.
3. Mobile app (now v0, D5) — one codebase serving both the chat surface (input) and the
   speech-output playback sink (output); sequence it after the computer text slice proves the loop.

## Decisions
- **D3 Serve-loop first** (2026-07-09) — thin end-to-end backbone before capture/continuum.
- **POCs are reference, not source** (D7) — `poc/live_video_chat` informs the serve-loop
  contracts and streaming shape, but the production path is written fresh. No lift-and-shift.

## Worklog
- 2026-07-08 — thread seeded at product-structure standup.
- 2026-07-09 — build order locked (D3); BWM = Qwen3-VL-32B (D6); mobile app in v0 (D5);
  POC-no-reuse recorded (D7). Agenda refocused on slicing the serve-loop MVP.
