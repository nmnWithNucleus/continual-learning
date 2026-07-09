# HANDOFF — Storage Service working canvas

> Single touch-point for any agent (or human) picking up work on this service.
> Read [CHARTER.md](CHARTER.md) first (mission/scope/interfaces), then this file — the
> volatile working record. Conventions: [../../ORG.md](../../ORG.md) § Documentation protocol.

**Status:** serve-loop MVP (v0.0) built + tested + **integrated E2E** (integrator ran the live loop 2026-07-09: C4 written by inference, re-read by `turn_id` + `session_id`; C6 resolved base) · **Last updated:** 2026-07-09

## Workstream index
| WS | What | Status | Working file | Owner session |
|---|---|---|---|---|
| WS-D | serve-loop MVP: `/sessions` (C4 write/read) + model directory (C6 resolve) | done (built, tested, smoke-run on :8083) | [handoff/ws-storage-mvp.md](handoff/ws-storage-mvp.md) | this session |

## Current state
- **Built (v0.0):** FastAPI + SQLite storage service on `:8083`. Endpoints:
  - `POST /sessions/turns` — validates a **C4** turn record against
    `../../contracts/c4_turn_record.v0.json` (authoritative gate, incl. the nested-C3 `$ref`),
    persists verbatim (idempotent upsert on `turn_id`), returns `{ok, turn_id}`.
  - `GET /sessions/turns/{turn_id}` — the stored C4 (404 if absent).
  - `GET /sessions/{session_id}/turns` — the session's C4 turns, ordered by `created_at`.
  - `GET /model-directory/resolve?user_id=…` — **C6** body (seeded base entry, `adapter_path:null`).
  - `GET /health` → `{ok:true}`.
- **Storage:** SQLite dev file DB (`STORAGE_DB_PATH`, default `app/dev.db`). Tables: `turns`
  (PK `turn_id`, index `(session_id, created_at)`, full C4 stored as JSON) + `model_directory`
  (seeded base row). Fresh connection per op (dev volume).
- **Tested:** 10 pytest tests pass (round-trip + schema-validate, list-by-session ordering +
  isolation, idempotent write, invalid-C4 → 422, C6 resolve + schema-validate). Also smoke-run
  against live uvicorn on `:8083` (health, resolve, C4 write→read, list, 404, 422 all green).

## Scope boundary (v0.0)
- **Built so far: `/sessions` + model directory.** `/raw` (C1 blob leg) + `/context` (C2) are the
  **now-active capture slice** (C1/C2 frozen 2026-07-09 — see Next). The training-window read (C10)
  and the recency/semantic index (C11) remain **later slices** — deliberately absent. Model
  directory is trivial (everyone → base, no adapter) until continuum ships C5 registration.

## Next
- **⇐ ACTIVE: capture slice (learn-loop MVP).** C1 + C2 are **frozen** (2026-07-09, D10/D11 —
  `../../contracts/c1_raw_stream_envelope.v0.json`, `c2_processed_record.v0.json`). Storage M0 leads
  the fan-out (both recording and data-processing write to us):
  - **`/raw` blob leg (C1):** `PUT /raw/blobs` (raw bytes + `chunk_id`/`user_id`/codec/sha256 →
    **storage mints an opaque `blob_ref`**, idempotent on `chunk_id`) + `GET /raw/blobs/{blob_ref}`
    (data-processing pulls bytes for ASR). Local blob dir for dev; GCS in prod.
  - **`/context` write (C2):** `POST /context/records` (validate against the C2 schema like the
    existing `/sessions` gate; idempotent upsert on `record_id`), time-indexed on `(user_id,
    t_start)`; `GET /context/records/{id}` + `GET /context?user_id=&from=&to=` (time-range).
- `/context` time-ranged read hardening (per CHARTER M1).
- C5 adapter registration → per-user overrides in `model_directory` (M3).
- Encryption at rest + per-user isolation tests (M4); deletion primitives (M5).
- Integrator: point inference's C4 writer + C6 resolve at `:8083` (see build conventions).
- **Observability (D9, ratified 2026-07-09) — now on backlog:** expose `/metrics` (request rate/latency/errors + DB/query metrics: query latency, rows read/written, DB/file size, pool health) and own the Grafana dashboard JSON (`dashboards/*.json`); shared Prometheus/Grafana is Platform's. See [CHARTER.md](CHARTER.md) scope/M7 + [../../ARCHITECTURE.md](../../ARCHITECTURE.md) §Observability.

## Gotchas
- **Contracts are the source of truth.** Schema validation uses a `referencing` registry so
  C4's `$ref: "c3_userprompt.v0.json"` resolves — do not inline/fork the C3 shape.
- `created_at` ordering relies on RFC3339 UTC strings sorting lexicographically; `rowid` breaks ties.
- Writes are **idempotent** (`INSERT OR REPLACE` on `turn_id`) — a re-POST updates in place, no dup row.
