# HANDOFF — Recording Service working canvas

> Single touch-point for any agent (or human) picking up work on this service.
> Read [CHARTER.md](CHARTER.md) first (mission/scope/interfaces), then this file — the
> volatile working record. Conventions: [../../ORG.md](../../ORG.md) § Documentation protocol.

**Status:** M0 built (mock loop) — awaiting integration · **Last updated:** 2026-07-09

## Workstream index
| WS | What | Status | Working file | Owner session |
|---|---|---|---|---|
| A | M0 ingest spine (capturer + `/capture/run` + CLI) | built, 27 tests pass | `app/`, `tests/` | learn-loop M0 fan-out |

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
- Not in M0 (later milestones/backlog): real OS/browser mic capture (M1); consent controls
  (M2); device auth; `/metrics` + Grafana dashboard (M6 / D9 observability).

## Next
- Integration: run against the live extended storage `:8083` (`/raw`) + data-processing
  `:8085` (`/ingest`) and drive one chunk end-to-end into `/context`.
