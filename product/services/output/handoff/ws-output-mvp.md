# WS-C — Output service, serve-loop MVP (v0.0) worklog

House style: Goal / Done / In flight / Next / Gotchas. Newest notes at the bottom.

## Goal
Build the **delivery layer** for the text-only serve-loop MVP:
1. A browser-side **C9 reader** (`app/static/c9_reader.js`) — the actual delivery to the computer
   surface: stream-read a `fetch()` Response in C9 wire format, split answer text from the JSON
   end frame on the first `U+001E`, render the answer as **safe markdown** (escape HTML first, no
   XSS), expose end-frame usage; a clean export the input surface imports.
2. A thin standalone **relay service** (:8082, FastAPI): `POST /deliver` proxies a C9 stream from
   an upstream URL to the caller unchanged with a delivery ack; `GET /health`. Proves the delivery
   service boundary for future non-web surfaces + the proactive channel (not on the web hot path).

Conforms to the frozen shapes in `product/contracts/` (C9 is the only contract output touches in
v0.0). Stack: Python 3.11 (dev box ran 3.12), FastAPI + uvicorn, httpx, pydantic, pytest,
jsonschema. Port 8082.

## Done
- **`app/static/c9_reader.js`** — dependency-free ES module. Exports:
  - `renderC9Stream(response, targetEl, {onText, onEndFrame})` → `{answer, endFrame, usage}` — the
    primary function the input surface imports; streams + renders markdown into a DOM element live
    (rAF-coalesced), then surfaces the end frame + usage.
  - `readC9Stream(response, {onText})` → `{answer, endFrame}` — low-level splitter; robust to the
    separator or a multibyte UTF-8 char landing across a chunk boundary; falls back to
    `response.text()` where streaming isn't available.
  - `renderMarkdown`/`markdownToHtml`, `escapeHtml`, `RECORD_SEPARATOR`.
  - Markdown subset: headings, **bold**, *italic*, `inline code`, fenced code blocks, paragraphs,
    ordered/unordered lists. **HTML is escaped before any markup is added** — the security
    foundation. Inline-code spans are stashed before emphasis so markers inside code stay literal;
    `_`/`__` require non-word boundaries so `snake_case` is not mangled.
- **`app/static/selftest.html`** — feeds a synthetic C9 stream (built in-page from a mock
  streaming `Response`) through the reader and renders it; buttons for a markdown answer, an XSS
  attempt (must be inert), an error end frame, and a **"Run assertions"** in-page test.
- **`app/main.py`** — FastAPI relay. `POST /deliver` (body `{upstream_url, payload?, method?,
  turn_id?}`) opens a streaming httpx request upstream and relays bytes verbatim via a
  `StreamingResponse`; delivery ack in headers (`X-Delivery-Id/-Turn-Id/-Upstream/-Ack`). `GET
  /health`, `GET /` index. Mounts the browser client at `/static/*`. `create_app(client=…)` lets
  tests inject an httpx client.
- **`app/relay.py`** — relay core (`relay_c9`, `build_ack`, error-frame synth), client injectable
  for tests. On upstream connect error or HTTP ≥ 400 it appends a schema-valid C9 **error end
  frame** so the caller always gets a valid terminus.
- **Python mirrors for pytest** (WS row permits "a Python mirror is fine"): `app/c9_parse.py`
  (mirror of the JS splitter) and `app/markdown.py` (mirror of the JS renderer). Kept in lock-step
  with the JS so the tests validate the shipped algorithm.
- **Tests — 46, all green** (`pytest -q`):
  - `test_c9_parse.py` (16): answer/end-frame split, single-blob + parametrized chunk sizes
    1..64 (separator + `café`/`☕` split across boundaries), empty answer, missing separator,
    malformed / non-object end frame, JSON-like text in the answer not confused. End frames
    validated against `c9_response_stream.v0.json`.
  - `test_markdown.py` (24): no-XSS (script/img-onerror/anchor/quotes/code/code-block all
    escaped, `&` escaped once) + formatting (headings, bold/italic, `_` bold, snake_case safe,
    inline code protecting markers, lists, paragraphs, code blocks, mixed doc, empty input).
  - `test_relay.py` (6): `/health`, `/`, `/deliver` relays C9 byte-for-byte + ack headers +
    forwards payload/method/url, large body intact, upstream connect-error and HTTP-503 both →
    schema-valid C9 error frame. Upstream faked with `httpx.MockTransport`.
- **Ran for real** (honesty rule): `run.sh` → uvicorn :8082. `/health`, `/static/c9_reader.js`
  (10.8 KB), `/static/selftest.html` all 200. Relayed a live streamed C9 body from a local
  upstream over real HTTP — body byte-exact, ack headers present, round-trips through the parser to
  the correct answer + a schema-valid end frame. Also observed the error path against a real
  sibling inference service on :8010 (it rejected a bare C3 with HTTP 422; relay surfaced it as a
  schema-valid C9 error frame) and a connect-failure (same, clean error frame).

## In flight
- Nothing — WS-C deliverables complete.

## Next (later slices, not v0.0)
- M2 mobile speech (TTS → mobile app → BT audio); M3 failure handling (idempotent retry keyed by
  turn id, undeliverable queue, surface fallback); the proactive channel.
- If desired, a Node-based JS unit test of `c9_reader.js` directly (this box had no `node`, so the
  JS was validated via faithful Python mirrors + the in-browser `selftest.html` assertions).

## Gotchas
- **`U+001E` in source**: keep the separator/sentinels as escape sequences (`'\u001e'`,
  `'\ue000'`, `'\ue001'`) — raw control chars in source files are fragile through tooling. The
  constants resolve to the right code points (verified).
- **C9 body is opaque bytes** to the relay — never re-encode or reformat it, or the byte-exact
  guarantee (and the separator) breaks. The delivery ack therefore rides in **headers**, not the
  body.
- **`.split` on the FIRST separator only** — the answer may legitimately contain `{...}` JSON-like
  text or extra ``-adjacent content; only the first separator terminates the answer.
- **Port 8010** is inference's; it may already be live from a sibling workstream. The relay treats
  any upstream failure as a schema-valid C9 error frame, so it degrades cleanly.
- **Markdown is a deliberately minimal subset** — no links/images/tables/blockquotes/nested lists
  in v0.0 (unsupported markers render as escaped literal text, which is safe). Grow it in a later
  slice if the surface needs more.
