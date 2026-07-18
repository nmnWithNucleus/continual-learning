# HANDOFF — Recording Service working canvas

> Single touch-point for any agent (or human) picking up work on this service.
> Read [CHARTER.md](CHARTER.md) first (mission/scope/interfaces), then this file — the
> volatile working record. Conventions: [../../ORG.md](../../ORG.md) § Documentation protocol.

**Status:** M0 **integrated E2E + independently verified** (2026-07-09) · **`ChunkSource` seam added 2026-07-10** — 34 tests · **Last updated:** 2026-07-18 (post-return doc sync)

## Workstream index
| WS | What | Status | Working file | Owner session |
|---|---|---|---|---|
| A | M0 ingest spine (capturer + `/capture/run` + CLI) | done — integrated E2E 2026-07-09; **34 tests** after the 2026-07-10 seam pass | `app/`, `tests/` | learn-loop M0 fan-out |

## Current state
- **M0 ingest spine built** (`:8084`, FastAPI + uvicorn, isolated `.venv`). Carves a
  continuous audio source (synthetic WAV default; caller `.wav` path via `source=`) into
  fixed 5s chunks; per chunk **blob-first**: PUT bytes to storage `/raw/blobs` -> POST a
  **C1** envelope to data-processing `/ingest`. One ULID-like `stream_id`; dense zero-based
  `sequence`; stable ULID-like `chunk_id` reused on retry; at-least-once retry on both legs
  (transient/5xx only) that never advances `sequence`. Emitted C1 validated against the
  frozen schema on the emit path.
- **Drivable headless:** `POST /capture/run {storage_url, dp_url, source?, chunk_seconds?,
  base_wallclock?, user_id?, device_id?, sample_seconds?}` -> `{stream_id, chunks_emitted,
  chunk_ids, sequences, record_ids}`; `GET /health` -> `{ok:true}`; module CLI
  `python -m app.cli` / `python -m app`.
- **Tests: 27 passed** (own `.venv`, TestClient + httpx MockTransport fakes; no real ports).
  Covers: emitted C1 schema-valid; dense/zero-based/+1 sequence; single stream_id; blob-first
  ordering; retry reuses chunk_id + holds sequence; ceil(N/K) chunk count; contiguous
  wall-clock spans; storage+data-processing loss/dup drills (exactly-once via chunk_id).
  Also verified end-to-end over **real sockets** against an emulator that schema-validates
  each received C1 (scratchpad `e2e_check.py`).
- Downstream calls made (integrator: confirm they match the pinned wire):
  `PUT {storage_url}/raw/blobs?user_id=&device_id=&chunk_id=&codec=&sha256=&bytes=` (octet-stream body)
  and `POST {dp_url}/ingest` (C1 JSON body).
- **`ChunkSource` seam (2026-07-10):** the carver is generalized behind `app/sources/`
  (self-registering; the WAV file source is one impl — a future mic/screen/bodycam source is
  **one new file, no C1 change**). DP's `/ingest` now returns `record_ids[]` (one chunk → many
  records); the capturer flattens them — regression-tested (3 chunks × 3 records → 9) after the
  verifier caught a live 500 that stale test fakes had masked.
- Not in M0 (later milestones/backlog): real OS/browser mic capture (M1); consent controls
  (M2); device auth; `/metrics` + Grafana dashboard (M6 / D9 observability).

## Next
- ~~Integration~~ **DONE 2026-07-09** — one `/capture/run` drove 3 chunks E2E into `/context`
  on live ports; idempotency proven on both legs; independently verified.
- **Recording-led capture M1 (founders' pick 2026-07-18 — the next lead session owns this):**
  real capture sources behind the `ChunkSource` seam — computer mic (M1); computer screen +
  browser-extension screen capture; bodycam/wearable ingest — plus the **gap-detection
  continuity report** (joint with data-processing: break/dup detector on `/ingest` feeding
  recording's report) and the **fuller ASR pipeline** on the DP side as the paired priority.
  Consent gate (M2) stays the hard gate before any real always-on capture. Chunk length
  5 s → ~20–30 s + overlap (OQ4, joint with DP).
