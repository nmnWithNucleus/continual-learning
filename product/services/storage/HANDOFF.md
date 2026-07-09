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
- This is **`/sessions` + model directory ONLY.** `/context` (C2), `/raw` (C1 blob leg), the
  training-window read (C10), and the recency/semantic index (C11) are **later slices** —
  deliberately absent. Model directory is trivial (everyone → base, no adapter) until continuum
  ships C5 registration.

## Next
- `/context` write path (C2) + time-ranged read (per CHARTER M1).
- C5 adapter registration → per-user overrides in `model_directory` (M3).
- Encryption at rest + per-user isolation tests (M4); deletion primitives (M5).
- Integrator: point inference's C4 writer + C6 resolve at `:8083` (see build conventions).
- **Observability (D9, ratified 2026-07-09) — now on backlog:** expose `/metrics` (request rate/latency/errors + DB/query metrics: query latency, rows read/written, DB/file size, pool health) and own the Grafana dashboard JSON (`dashboards/*.json`); shared Prometheus/Grafana is Platform's. See [CHARTER.md](CHARTER.md) scope/M7 + [../../ARCHITECTURE.md](../../ARCHITECTURE.md) §Observability.

## Gotchas
- **Contracts are the source of truth.** Schema validation uses a `referencing` registry so
  C4's `$ref: "c3_userprompt.v0.json"` resolves — do not inline/fork the C3 shape.
- `created_at` ordering relies on RFC3339 UTC strings sorting lexicographically; `rowid` breaks ties.
- Writes are **idempotent** (`INSERT OR REPLACE` on `turn_id`) — a re-POST updates in place, no dup row.
