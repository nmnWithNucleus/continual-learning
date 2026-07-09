# HANDOFF — Output Service working canvas

> Single touch-point for any agent (or human) picking up work on this service.
> Read [CHARTER.md](CHARTER.md) first (mission/scope/interfaces), then this file — the
> volatile working record. Conventions: [../../ORG.md](../../ORG.md) § Documentation protocol.

**Status:** serve-loop MVP (v0.0) built + tested + **integrated E2E** (integrator wired `c9_reader.js` into the input surface + verified the standalone `/deliver` relay against live inference 2026-07-09) · **Last updated:** 2026-07-09

## Workstream index
| WS | What | Status | Working file | Owner session |
|---|---|---|---|---|
| WS-C | serve-loop MVP: browser C9 reader + standalone C9 relay (:8082) | **done (mock-tested, real-HTTP-proven)** | [handoff/ws-output-mvp.md](handoff/ws-output-mvp.md) | this session |

## Current state
- **Deliverable 1 — browser-side C9 client**: `app/static/c9_reader.js` (dependency-free ES
  module). Stream-reads a `fetch()` Response in C9 wire format, splits the answer text from the
  JSON end frame on the first `U+001E`, renders the answer as **safe markdown** (HTML escaped
  first — no XSS), and exposes the end-frame usage. Primary export the input surface imports:
  `renderC9Stream(response, targetEl)` → `{answer, endFrame, usage}`; also exports
  `readC9Stream`, `renderMarkdown`/`markdownToHtml`, `escapeHtml`, `RECORD_SEPARATOR`.
  Self-test page: `app/static/selftest.html` (feeds a synthetic C9 stream, renders it, and has a
  "Run assertions" button covering XSS-inertness + answer/end-frame round-trip).
- **Deliverable 2 — standalone relay service (:8082)**: `app/main.py` (FastAPI). `POST /deliver`
  proxies a C9 stream from an `upstream_url` to the caller **byte-for-byte unchanged**, with a
  delivery ack in response headers (`X-Delivery-*`). `GET /health`, `GET /` index. Serves the
  static browser client at `/static/*`. NOT on the web MVP hot path (input relays directly) — it
  proves the delivery service boundary for future non-web surfaces + the proactive channel.
- **Tests (46, all green)**: `tests/test_c9_parse.py` (parser correctness incl. chunk-boundary +
  multibyte + malformed; end frame validated against the frozen `c9_response_stream.v0.json`),
  `tests/test_markdown.py` (HTML-escaping / no-XSS + formatting), `tests/test_relay.py` (relay
  proxies unchanged + ack, upstream-error → schema-valid C9 error frame; upstream faked with
  `httpx.MockTransport`).
- **Proven runnable**: `run.sh` boots uvicorn on :8082; relayed a real streamed C9 body end-to-end
  over real HTTP (byte-exact, ack headers, round-trips to a schema-valid end frame).

## Contracts
- **Consumes C9** (`../../contracts/c9_response_stream.v0.json`) — the only contract output
  touches in v0.0. Both the browser reader and the relay treat the C9 body as opaque bytes split
  on the first `U+001E`; end frames are validated against the schema in tests.
- Reads C3 fields only indirectly: `/deliver` forwards a caller-supplied `payload` (e.g. a C3
  UserPrompt) upstream untouched.

## Notes for the integrator
- Web MVP wiring: import `renderC9Stream` from `output/app/static/c9_reader.js` into input's
  `index.html` and call it with the `fetch()` Response from input's C9 relay + the answer element.
  The module is framework-free and has no build step.
- The relay is optional for the web hot path; use it for non-web surfaces / the proactive channel.
- Port `8010` may already host a live inference service from a sibling workstream — the relay
  surfaces any upstream failure (connect error / HTTP ≥ 400) as a schema-valid C9 error end frame.

## Next (later slices — NOT v0.0)
- M2 mobile speech path (TTS → mobile app → BT audio); M3 failure handling (idempotent retry,
  undeliverable queue, surface fallback); the proactive channel. See CHARTER.md § v0 deliverables.
