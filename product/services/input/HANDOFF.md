# HANDOFF — Input Service working canvas

> Single touch-point for any agent (or human) picking up work on this service.
> Read [CHARTER.md](CHARTER.md) first (mission/scope/interfaces), then this file — the
> volatile working record. Conventions: [../../ORG.md](../../ORG.md) § Documentation protocol.

**Status:** serve-loop MVP v0.0 built + tested + **integrated E2E (mock loop run by integrator 2026-07-09)** · **Last updated:** 2026-07-09

## Workstream index
| WS | What | Status | Working file | Owner session |
|---|---|---|---|---|
| A | Input MVP (:8081): computer text surface + QueryBuilder (C3 producer) + C9 relay | done (mock, tested) | [handoff/ws-input-mvp.md](handoff/ws-input-mvp.md) | WS-A |

## Current state
- **Runnable + tested** input service under [app/](app/). `MODEL_BACKEND` is not input's
  concern — input is model-agnostic; it just relays whatever C9 stream inference returns.
- `GET /` serves a minimal static chat surface ([app/static/index.html](app/static/index.html)
  + [app/static/app.js](app/static/app.js)); `POST /api/turn` builds a C3, calls
  `${INFERENCE_URL}/infer`, and relays the C9 stream byte-for-byte; `GET /health`.
- QueryBuilder is isolated in [app/query_builder.py](app/query_builder.py) (unit-tested).
- 19 tests pass; end-to-end smoke against a mock inference confirmed (chunked streaming
  relay, C3 schema-valid at the inference side, end-frame `turn_id` == `X-Turn-Id` header).
- **Render seam wired (integrator, 2026-07-09):** the surface no longer renders plain text.
  Output's `c9_reader.js` is **vendored** into [app/static/c9_reader.js](app/static/c9_reader.js)
  (same-origin so the browser ES-module import needs no CORS to `:8082`); `index.html` loads
  `app.js` as `type="module"` and `app.js` hands the `fetch()` response to
  `renderC9Stream(resp, #answer, {onEndFrame})`, which streams + SAFE-markdown-renders the
  answer and surfaces usage. Canonical source stays output's copy — re-copy on change.

## Contracts
- **Produces C3** (`contracts/c3_userprompt.v0.json`) — validated in code + tests.
- **Relays C9** (`contracts/c9_response_stream.v0.json`) unchanged — input never parses the
  body; output's `c9_reader.js` (WS-C) owns C9 parsing + markdown render (now wired into the
  browser surface, vendored copy).

## Next
- ~~Integrator: mount output's `c9_reader.js` into the surface + run the full loop.~~ **Done
  2026-07-09** (see Current state + [../../handoff/engineering.md](../../handoff/engineering.md)
  "Serve-loop MVP — v0.0 build result").
- Later slices: mobile / extension / wearable surfaces; speech/image/video via C8; C11
  recent-context injection; clarification-answer C3 variant.
