# HANDOFF — Inference Service working canvas

> Single touch-point for any agent (or human) picking up work on this service.
> Read [CHARTER.md](CHARTER.md) first (mission/scope/interfaces), then this file — the
> volatile working record. Conventions: [../../ORG.md](../../ORG.md) § Documentation protocol.

**Status:** serve-loop MVP (v0.0) built + tested + **integrated E2E** (integrator ran the full input→inference→output→storage mock loop 2026-07-09; C9 stream + C4 write verified live) · **Last updated:** 2026-07-09

## Workstream index
| WS | What | Status | Working file | Owner session |
|---|---|---|---|---|
| WS-B | serve-loop MVP: `/infer` (consume C3 → resolve C6 → generate → stream C9 → write C4) + vLLM launch script | done (built, tested, smoke-run on :8010 against real storage :8083) | [handoff/ws-inference-mvp.md](handoff/ws-inference-mvp.md) | this session |

## Current state
- **Built (v0.0):** FastAPI service on `:8010`, `MODEL_BACKEND=mock` default (no GPU). Endpoints:
  - `POST /infer` — body = a **C3** UserPrompt, validated against
    `../../contracts/c3_userprompt.v0.json`. Flow: **C6 resolve** (`GET {STORAGE_URL}/model-directory/resolve`,
    base-model fallback if unreachable) → build system prompt + user text → generate via the
    `MODEL_BACKEND` switch → **stream the C9 wire format** (answer text chunks, a `\x1e` (U+001E)
    separator, then one JSON end frame) → assemble a **C4** turn record (full C3 embedded, never
    truncated, `tool_traces`/`mentor_traces` = `[]`) and `POST {STORAGE_URL}/sessions/turns`.
  - `GET /health` → `{status, service, backend, model_id, storage_url}`.
- **Backends** (`MODEL_BACKEND`): `mock` (default) streams a clearly-labelled canned answer
  token-by-token with small async delays + word-count usage; `vllm` is an OpenAI-compatible
  streaming client to `{VLLM_URL}/v1/chat/completions` (real Qwen3-VL-32B, GPU node).
- **serve_vllm.sh** — from-scratch launch for real Qwen3-VL-32B on vLLM (TP=8, one a3mega node,
  text-only, `--max-model-len 32768`). **GPU node only; NOT run by the mock loop or run.sh.**
- **Tested:** 6 pytest tests pass (C9 stream + schema-validate, C4 persisted + schema-validate,
  malformed-C3 → 422, health, backend selection, mock chunk reassembly). Tests are hermetic (mock
  backend + a live in-process storage stub on an ephemeral port).
- **Live smoke (mock):** ran `:8010` against the **real** storage service on `:8083` — POST a C3,
  got the streamed C9, and the C4 was accepted by storage's own schema gate and re-read by `turn_id`.

## Scope boundary (v0.0)
- **Base model only.** No per-user adapter (C6 resolves to base), no agentic harness/tools, no
  mentors (C7). Text-only. Trace arrays are present-but-empty so C4's shape never changes when
  harness/mentors arrive. Mid-turn C9 frames are reserved, not emitted.

## Next
- **Real model (M0 finish):** stand up `serve_vllm.sh` on the a3mega node; run `/infer` with
  `MODEL_BACKEND=vllm` + `VLLM_URL`; confirm the C9 stream maps deltas + real usage.
- **M1:** per-user LoRA hot-swap once continuum publishes adapters via C5 (C6 stops being trivial).
- **Integrator:** input relays this `/infer` C9 stream to the browser; output owns the browser-side
  C9 reader + markdown render. C9/C4 shapes are already exercised against real storage.
- **Observability (D9, now on backlog):** ship `/metrics` on `:8010` (request rate/latency/errors + **GPU via dcgm-exporter**, tokens/sec, time-to-first-token, backend, queue depth) + a `dashboards/*.json` Grafana dashboard; Platform runs the shared scrape/UI — see CHARTER M6 + [../../ARCHITECTURE.md](../../ARCHITECTURE.md) §Observability.

## Gotchas
- **C9 is a wire format, not one JSON doc.** Split the body on the FIRST `\x1e` (U+001E): before =
  answer text, after = the single JSON end frame. `app/wire.py` has `split_stream()`.
- The **C4 write happens after the stream** (inside the streaming generator, after the end frame).
  Consuming the full response drives it to completion — so a fully-read `/infer` response means the
  C4 has been POSTed. A storage-write failure is logged, not surfaced (answer already delivered).
- **Resolve degrades to base** on any storage error (matches storage's charter). A malformed **C3**
  returns HTTP 422 (no dependable `turn_id` to build a valid C9 error frame from); errors *during*
  generation are surfaced as a C9 end frame with an `error` field.
- Contract validation uses a `referencing` registry so C4's `$ref: c3_userprompt.v0.json` resolves —
  same posture as storage. `jsonschema` + `referencing` are in requirements.txt.
- `vllm` backend needs no `vllm`/`openai` Python package here — it's plain httpx against the
  OpenAI-compatible HTTP API. Running the server is the separate GPU task in `serve_vllm.sh`.
