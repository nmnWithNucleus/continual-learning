# HANDOFF — Inference Service working canvas

> Single touch-point for any agent (or human) picking up work on this service.
> Read [CHARTER.md](CHARTER.md) first (mission/scope/interfaces), then this file — the
> volatile working record. Conventions: [../../ORG.md](../../ORG.md) § Documentation protocol.

**Status:** serve-loop MVP (v0.0) **CLOSED on the real model** — integrated E2E on the mock loop, then a genuine turn driven on **Qwen3-VL-32B-Instruct (vLLM TP=8, node-7)** 2026-07-09; `mock` stays the no-GPU dev default · **Last updated:** 2026-07-18 (post-return doc sync)

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
  text-only, `--max-model-len 32768`); defaults to the `vllm-cu13` env (vLLM 0.24.0 / CUDA-13 +
  flashinfer, validated E2E 2026-07-09), `VLLM_BIN` overrides back to `vllm-vlm` (0.19.1 fallback).
  **GPU node only; NOT run by the mock loop or run.sh.**
- **Tested:** 6 pytest tests pass (C9 stream + schema-validate, C4 persisted + schema-validate,
  malformed-C3 → 422, health, backend selection, mock chunk reassembly). Tests are hermetic (mock
  backend + a live in-process storage stub on an ephemeral port).
- **Live smoke (mock):** ran `:8010` against the **real** storage service on `:8083` — POST a C3,
  got the streamed C9, and the C4 was accepted by storage's own schema gate and re-read by `turn_id`.

## Scope boundary (v0.0)
- **Base model only.** No per-user adapter (C6 resolves to base), no agentic harness/tools, no
  mentors (C7). Text-only. Trace arrays are present-but-empty so C4's shape never changes when
  harness/mentors arrive. Mid-turn C9 frames are reserved, not emitted.

## Incoming — serve-time memory harness (continuum kickoff decision, 2026-07-22; pending founders'-board ratification)
- Founder placed the **memory harness runtime HERE**: fast-memory (mneme/SSM) per-user state
  fed by tailing recent `/context` records, the think-back paging executor (~80–120 temporary
  LoRA steps on a past day's log + snapshot rollback at question time), day-log-grounded
  answering, and the memory router. Continuum **trains** the artifacts (nightly life adapter
  via C5 — your M1; later a versioned mneme module + reader-LoRA + paging recipe, likely a C5
  *bundle* — shape needs this service at the table before freezing); this service **executes** them.
- Load-bearing constraint from the research: **two model instances, routed, never merged** —
  today-path = base + reader-LoRA + fast module (NO life adapter); past-path = base + life
  adapter (+ paging). Merging loses ~65% of the fast-memory gap.
- **Serve tier is more mature than the kickoff note said** (research @ `b3c58e1`, v39): the
  serving harness `engram_server.py` boots the two-path router + think-back paging + muon-h8192
  fast memory **today** on the 35-day testbed, and it's now a **4-lane** stack — a **Council**
  lane was added (classic / planner / orchestrator / council) plus a page-weight cache (~2s vs
  ~90s paging). What is NOT built is the production **ingest** layer (continuous capture, 10s-segment
  day-log, surprise-gating, Vertex captioner) — that greenfield is DP + storage scope, not a
  research artifact to port.
- Deferred until continuum's nightly loop closes. Details: continuum
  [HANDOFF](../continuum/HANDOFF.md) § Architecture decisions + [ws-morpheus-port](../continuum/handoff/ws-morpheus-port.md)
  (the "NOT NOW — inference" rows are this service's future scope).
- **Recipe registry (incoming):** the consolidation/serving recipes will be storage-hosted +
  versioned (2026-07-23 decision); this service can pull serving-side recipe knobs directly from
  storage without consulting continuum.

## Next
- ~~**Real model (M0 finish)**~~ **DONE 2026-07-09** — `serve_vllm.sh` ran on node-7 (TP=8);
  `/infer` with `MODEL_BACKEND=vllm` drove a genuine turn E2E (real usage in the C9 end frame;
  C4 persisted with the real `model_id`). Fleet verified **down** 2026-07-17 — relaunch via
  `serve_vllm.sh` when needed (node-7 is the product node; wider cluster busy with teammate runs).
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
