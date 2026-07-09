# WS-A — Input MVP (serve-loop v0.0) worklog

House style: Goal / Done / In flight / Next / Gotchas.

## Goal
Build the **input** service (:8081) for the text-only serve-loop skeleton: a computer chat
surface + the QueryBuilder (C3 producer). A user types → we mint session/turn ids → build a
schema-valid **C3 UserPrompt** → call inference `/infer` streaming → **relay the C9 stream
straight back to the browser unchanged**. No personalization, no capture, no extra
modalities/surfaces.

## Done
- **Layout** `services/input/{app/, tests/, run.sh, requirements.txt, pytest.ini}`.
- **QueryBuilder** (`app/query_builder.py`) — isolated + unit-testable:
  - pydantic models (`UserPrompt`/`Message`/`ClientCapabilities`) mirror
    `contracts/c3_userprompt.v0.json` (`extra="forbid"` == `additionalProperties:false`).
  - mints `sess-<uuid>` / `turn-<uuid>`; stamps `template_version="mvp-0"`,
    `surface="computer"`, `modalities=["text"]`, `can_render_markdown=true`.
  - `build()` validates every payload against the frozen JSON Schema before returning
    (`jsonschema`); rejects empty text.
- **FastAPI app** (`app/main.py`):
  - `GET /` serves the static surface; `GET /health`; `POST /api/turn {text, session_id?, user_id?}`.
  - `/api/turn` builds+validates C3, POSTs it to `${INFERENCE_URL}/infer` (default
    `http://localhost:8010`) via httpx streaming, and relays the C9 bytes unchanged
    (`StreamingResponse`, `application/octet-stream`). Session/turn ids echoed as
    `X-Session-Id`/`X-Turn-Id` headers so the surface keeps a session **without** touching
    the relayed C9 body.
  - Unreachable-inference fallback: emits a C9-conformant end frame with `error` (empty
    answer + `U+001E` + end frame) so the browser always gets a parseable stream, not a 500.
- **Surface** (`app/static/index.html` + `app/static/app.js`): textbox, Send, answer area.
  JS reads the fetch stream, splits on `U+001E`, renders the answer, shows end-frame usage.
  **Markdown seam left explicit**: `renderAnswer` uses `textContent` (plain text) with a
  clear comment that the integrator wires in output's `services/output/app/static/c9_reader.js`
  for C9 parsing + markdown — no markdown lib duplicated here.
- **Tests (19, all green)**: `test_query_builder.py` (C3 validates against the JSON Schema,
  id minting/passthrough, capabilities, empty-text reject, ISO-UTC `created_at`);
  `test_api_turn.py` (byte-for-byte C9 relay; C3 sent to inference is schema-valid; session
  mint + passthrough via header; 400 on empty; unreachable-inference → valid C9 error frame;
  health; index). httpx stubbed at the `_client_factory` seam — no live inference needed.
- **Ran here:** `python3 -m pytest -q` → 19 passed. End-to-end smoke: ran the input service
  against a throwaway mock inference on :8010 — confirmed chunked streaming relay, the mock
  validated the C3, and the end-frame `turn_id` matched the `X-Turn-Id` header.

## In flight
- Nothing — WS-A M0/M1 slice complete for the mock loop.

## Next (later slices, not this WS)
- Integrator: swap the plain-text `renderAnswer` for output's `c9_reader.js`; run the full
  input→inference→output→storage loop; real `vllm` backend on the a3mega node.
- Mobile / extension / wearable surfaces; speech/image/video via C8; C11 recent-context
  injection into the C3; clarification-answer C3 variant (mentor relay return leg).

## Gotchas / decisions
- **Scope pinned:** computer **text** surface only. Other surfaces + non-text modalities are
  explicitly deferred (charter M3 / later slices).
- **user_id:** C3 requires it but `/api/turn` has no auth in v0. Accept optional `user_id` in
  the body; default to `DEV_USER_ID` env (`dev-user`). Real identity is platform's later.
- **Relay is byte-for-byte:** input never parses the C9 body (that is output's job). Ids ride
  in response headers precisely so the body stays untouched for output's reader.
- **Separator hygiene:** `U+001E` is written as an escape in source (`app/main.py`,
  `app/static/app.js`), never a raw control byte.
- **Env:** `INFERENCE_URL` (default `http://localhost:8010`), optional `CONTRACTS_DIR` /
  `DEV_USER_ID` / `PORT` / `HOST`. Run: `bash run.sh` (uvicorn on :8081).
- Python 3.11 target; validated here on 3.12 (fine). Deps installed to run tests.
