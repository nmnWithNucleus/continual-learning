# WS-B — Inference serve-loop MVP (v0.0)

Status: **done** (built, tested, live cross-service smoke) · Owner: this session · 2026-07-09

House style: Goal / Done / In flight / Next / Gotchas.

## Goal
Build the **inference** service (`:8010`) for the text-only serve-loop MVP: accept a **C3**
UserPrompt, resolve the model via **C6**, generate on the base model (mock default, vLLM real),
**stream C9** to the caller, and persist a **C4** turn record to storage. Base model only — no
adapter, no harness, no mentors. Ship both a runnable mock loop and a from-scratch vLLM launch
script; conform exactly to `product/contracts/*.json`.

## Done
- **`POST /infer`** (`app/main.py`): validates the body against `c3_userprompt.v0.json`
  (malformed → 422). Resolves C6 (`GET {STORAGE_URL}/model-directory/resolve?user_id=…`, base
  fallback on any error). Builds a fixed system prompt + the user text from C3. Generates via the
  `MODEL_BACKEND` switch. Streams the **C9 wire format**: answer text chunks → one `\x1e` (U+001E)
  byte → one JSON end frame `{contract:C9, version:0, turn_id, model_id, adapter:"base", usage,
  finished:true}` (or `…error:"…"`). After the stream, assembles a **C4** (full C3 embedded,
  never truncated, `tool_traces`/`mentor_traces` = `[]`) and POSTs it to `{STORAGE_URL}/sessions/turns`.
- **`GET /health`** — liveness + effective config (backend, model_id, storage_url).
- **Backends** (`app/backends/`): `mock` (default, no GPU) streams a clearly-labelled canned answer
  token-by-token with small async delays and word-count usage; `vllm` is a plain-httpx
  OpenAI-compatible streaming client to `{VLLM_URL}/v1/chat/completions` (`stream:true`,
  `stream_options.include_usage`), mapping deltas → C9 text chunks and the usage chunk → C9 usage
  (word-count fallback if the server omits usage).
- **Pydantic models** (`app/models.py`) mirror C3/C4/C6/C9; the JSON Schemas stay the source of
  truth (validated directly in tests via a `referencing` registry so C4→C3 `$ref` resolves).
- **`serve_vllm.sh`** — from-scratch Qwen3-VL-32B launch (TP=8, one a3mega node, text-only,
  `--max-model-len 32768`, gpu-mem-util 0.90). Comments carry the POC WS1 learnings (CUDA-12
  driver ⇒ vLLM 0.19.1; TP=8 head-count check; HF cache on `/mnt/localssd/.hf-home`). Clearly
  marked **GPU node only; not run by the mock loop or run.sh.**
- **`run.sh`** (uvicorn `:8010`), **`requirements.txt`**, **`pytest.ini`**.
- **Tests** (`tests/`, 6 pass): end-to-end mock loop (POST C3 → split the streamed body on `\x1e`
  → assert non-empty answer + C9 end frame validates + exactly one C4 persisted + C4 validates +
  `response_text == streamed text` + full C3 embedded); malformed-C3 → 422 (no turn persisted);
  health; backend selection; mock chunk reassembly + usage. Hermetic: mock backend + a live
  in-process storage stub (real uvicorn on an ephemeral thread; `RECORDED_TURNS` inspected directly).

## Verification actually run here
- `python3 -m pytest -q` → **6 passed** (Python 3.12 `moe` env; installed `jsonschema`,
  `referencing`).
- **Live cross-service smoke:** started the **real** storage service (`:8083`, its own SQLite +
  C4 schema gate) and this service (`:8010`, `MODEL_BACKEND=mock`), POSTed a C3 → received the
  streamed C9 (text + U+001E + end frame with `usage {prompt_tokens:29, output_tokens:24}`), then
  `GET /sessions/turns/turn-smoke-1` returned the persisted C4 with the full C3 embedded. Proves the
  C4 we emit passes storage's independent validation. Servers were stopped afterward.

## In flight
- Nothing — WS-B is complete for v0.0.

## Next
- Real-model path: run `serve_vllm.sh` on the a3mega node; exercise `/infer` with
  `MODEL_BACKEND=vllm` + `VLLM_URL`; confirm delta→C9 mapping and real usage counts (not run here —
  no GPU in this environment; honesty rule).
- M1 LoRA hot-swap once continuum publishes adapters (C5) → C6 resolution stops being trivial.
- Integrator wiring: input relays this C9 stream to the browser; output owns the browser-side C9
  reader + markdown render.

## Gotchas
- **C9 is a wire format, not one JSON doc** — split on the FIRST `\x1e`; `app/wire.py::split_stream`.
- The **C4 write runs inside the streaming generator after the end frame** — fully reading the
  `/infer` response guarantees the C4 POST completed (makes tests deterministic). A storage-write
  failure is logged, not surfaced (the answer is already delivered).
- **Resolve degrades to base** on any storage error (matches storage's charter risk row).
- vLLM path needs no `vllm`/`openai` pip package in this service — it's httpx against the HTTP API.
- Ports: inference `:8010`, storage `:8083`, vLLM `:8000` (real). Backend env: `MODEL_BACKEND`,
  `STORAGE_URL`, `VLLM_URL`, `MODEL_ID`, `SYSTEM_PROMPT`, `MOCK_TOKEN_DELAY`.
