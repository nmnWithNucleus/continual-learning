# HANDOFF — Storage Service working canvas

> Single touch-point for any agent (or human) picking up work on this service.
> Read [CHARTER.md](CHARTER.md) first (mission/scope/interfaces), then this file — the
> volatile working record. Conventions: [../../ORG.md](../../ORG.md) § Documentation protocol.

**Status:** serve-loop MVP (v0.0) built + tested + **integrated E2E** (integrator ran the live loop 2026-07-09: C4 written by inference, re-read by `turn_id` + `session_id`; C6 resolved base). **Learn-loop capture M0: `/raw` blob leg (C1) + `/context` store (C2) built + tested (26 pass) + integrated E2E + independently verified 2026-07-09** (blob-first + push loop live; idempotency proven on both legs); unchanged through the 2026-07-10 DP modality-seam pass (still 26 tests) · **Last updated:** 2026-07-18 (post-return doc sync)

## Workstream index
| WS | What | Status | Working file | Owner session |
|---|---|---|---|---|
| WS-D | serve-loop MVP: `/sessions` (C4 write/read) + model directory (C6 resolve) | done (built, tested, smoke-run on :8083) | [handoff/ws-storage-mvp.md](handoff/ws-storage-mvp.md) | prior session |
| WS-C | learn-loop capture M0: `/raw` blob leg (C1) + `/context` store (C2) | done (integrated E2E + independently verified 2026-07-09) | this file (below) | learn-loop M0 fan-out |

## Current state
- **Built (v0.0 serve-loop):** FastAPI + SQLite storage service on `:8083`. Endpoints:
  - `POST /sessions/turns` — validates a **C4** turn record against
    `../../contracts/c4_turn_record.v0.json` (authoritative gate, incl. the nested-C3 `$ref`),
    persists verbatim (idempotent upsert on `turn_id`), returns `{ok, turn_id}`.
  - `GET /sessions/turns/{turn_id}` — the stored C4 (404 if absent).
  - `GET /sessions/{session_id}/turns` — the session's C4 turns, ordered by `created_at`.
  - `GET /model-directory/resolve?user_id=…` — **C6** body (seeded base entry, `adapter_path:null`).
  - `GET /health` → `{ok:true}`.
- **Built (capture M0 — learn-loop) — the exact wire the integrator + recording + data-processing bind to:**
  - `PUT /raw/blobs?user_id=&device_id=&chunk_id=&codec=&sha256=&bytes=` — body = raw bytes
    (`application/octet-stream`). Verifies the body's SHA-256 == `sha256` (and `len` == `bytes`
    if sent) → **422** on mismatch; mints an **opaque** `blob_ref`; stores the bytes under the
    dev blob dir. **Idempotent on `chunk_id`** (re-PUT → same `blob_ref`, no dup blob/row).
    → `200 {blob_ref, bytes, sha256}`. `blob_ref` is storage-owned, may contain `/`.
  - `GET /raw/blobs?ref=<blob_ref>` — `ref` is a **query param** (not a path segment, since it
    may contain `/`) → `200` raw bytes (`application/octet-stream`); **404** if the ref is
    unknown **or the blob was since-deleted** (consumers must tolerate the latter).
  - `POST /context/records` — body = **C2** JSON. Validates against
    `../../contracts/c2_processed_record.v0.json` (same authoritative-gate style as the C4 write) →
    **422** on violation; **idempotent upsert on `record_id`**; stores the full C2 verbatim,
    time-indexed on `(user_id, t_start)`; assigns its own `ingest_time` (audit axis, NOT in C2,
    preserved across reprocess). → `200 {ok:true, record_id}`.
  - `GET /context/records/{record_id}` — the stored C2 (404 if absent). `record_id` is URL-safe.
  - `GET /context/records?user_id=&from=&to=` — that user's C2 records ordered by `t_start`.
    Window is **half-open `[from, to)`** (from inclusive, to exclusive — matches C10's
    `[last_trained_t, now)`); either bound omittable. Per-user isolation enforced by the
    mandatory `user_id` filter.
- **Storage:** SQLite dev file DB (`STORAGE_DB_PATH`, default `app/dev.db`) + local dev blob dir
  (`STORAGE_RAW_DIR`, default `app/raw_store/`, gitignored). Tables: `turns`, `model_directory`,
  **`raw_blobs`** (PK `chunk_id`, index on `blob_ref`; bytes on disk at the ref's hex-sharded
  path), **`context_records`** (PK `record_id`, index `(user_id, t_start)`, full C2 as JSON).
  Fresh connection per op (dev volume). GCS is the prod target for the bytes; metadata stays here.
- **Tested (isolated `.venv`, FastAPI TestClient, in-process — no real port bound):** **26 pytest
  pass** — the original **10** (serve-loop, unregressed) + **16** new: `/raw` PUT→GET round-trip +
  sha256 verify + idempotent-on-`chunk_id` (same ref, no dup) + distinct-chunk refs + sha/bytes
  mismatch → 422 + unknown-ref 404 + since-deleted 404; `/context` round-trip + schema-validate +
  idempotent upsert on `record_id` + time-range ordering/bounds + per-user isolation + invalid-C2
  → 422. Also **live-smoke-run** against real uvicorn (blob-first PUT, idempotent re-PUT, GET-by-ref
  byte round-trip with `/` in ref, C2 POST/GET/time-range) — all green; server torn down.

## Scope boundary (v0.0)
- **Built so far: `/sessions` + model directory.** `/raw` (C1 blob leg) + `/context` (C2) are the
  **now-active capture slice** (C1/C2 frozen 2026-07-09 — see Next). The training-window read (C10)
  and the recency/semantic index (C11) remain **later slices** — deliberately absent. Model
  directory is trivial (everyone → base, no adapter) until continuum ships C5 registration.

## Next
- **✅ DONE (this session): capture slice (learn-loop MVP) storage M0.** C1 + C2 were **frozen**
  (2026-07-09, D10/D11 — `../../contracts/c1_raw_stream_envelope.v0.json`,
  `c2_processed_record.v0.json`); storage M0 built the shared write targets (see Current state for
  the exact wire). One deviation from the earlier sketch below, pinned by the integrator's frozen
  wire spec: **`GET /raw/blobs?ref=<blob_ref>` takes the ref as a QUERY param, not a path segment**
  (`GET /raw/blobs/{blob_ref}`) — because a `blob_ref` may contain `/`. recording + data-processing
  must call the query-param form. Remaining fan-out: recording M0 (mic → `/raw` PUT → C1 emit) +
  data-processing M0 (C1 → ASR → C2 → `/context`) target these endpoints; integrator wires + runs
  one chunk end to end.
- `/context` time-ranged read hardening (per CHARTER M1).
- C5 adapter registration → per-user overrides in `model_directory` (M3).
- Encryption at rest + per-user isolation tests (M4); deletion primitives (M5).
- Integrator: point inference's C4 writer + C6 resolve at `:8083` (see build conventions).
- **Observability (D9, ratified 2026-07-09) — now on backlog:** expose `/metrics` (request rate/latency/errors + DB/query metrics: query latency, rows read/written, DB/file size, pool health) and own the Grafana dashboard JSON (`dashboards/*.json`); shared Prometheus/Grafana is Platform's. See [CHARTER.md](CHARTER.md) scope/M7 + [../../ARCHITECTURE.md](../../ARCHITECTURE.md) §Observability.

## Incoming — scope expansion from the continuum/Morpheus design (2026-07-23, pending board)
The continuum kickoff settled that storage owns the learn-loop **data jobs** (continuum stays a
lean training engine). Three additions land here once the founders' board ratifies (they re-cut
this charter) — details in [CHARTER.md](CHARTER.md) § Scope note + [../continuum/handoff/ws-morpheus-port.md](../continuum/handoff/ws-morpheus-port.md):
- **Day-log materialization** — a scheduled job renders a user-day's `/context` (C2) into the
  segment/block **day-log** (incl. `render_block` anchored text). This is where **C10 evolves**:
  from a raw record range read to a **day-log fetch**. The day-log format is recipe-versioned.
  (continuum has a working reference builder — `daylog.py`/`window.py`/`renderer.py` in the scaffold —
  to lift from; render_block must stay byte-parity with the research @ `b3c58e1`.)
- **Recipe registry** — versioned recipe/config hosting; fetch API for continuum + inference.
- **Reservoir custody** — amplified-corpus store (continuum writes via API); replay re-reads prior
  day-logs, so this is audit/provenance, not the replay hot path.
- **C10 watermark semantics** (charter OQ, still open) get decided at the same C10-evolution session
  jointly with continuum; new contract IDs for recipe-registry + reservoir minted at ratification.

**Sharpened by continuum's 2c build (2026-07-24) — concrete requirements the seam surfaced:**
- **Day-log fetch must serve ANY prior window on demand, by `(user_id, window_id)`** — not just the
  latest training window. Raw-source replay re-reads *prior* day-logs, so C10-evolved is random-access
  over a user's history, not a single forward cursor. (Surfaced concretely: the local rawlog replay
  test needed window-addressable fetch to work.)
- **Storage must expose "which windows has this user consolidated?"** — continuum today infers the set
  from the reservoir ledger; once the reservoir is pure audit/provenance, that enumeration needs a home
  in storage (a small list/index endpoint alongside the day-log fetch).
- **The materialized day-log must carry its recipe/format version** — the day-log shape is
  recipe-versioned, and continuum keys its cache on the day-log content fingerprint.

## Gotchas
- **Contracts are the source of truth.** Schema validation uses a `referencing` registry so
  C4's `$ref: "c3_userprompt.v0.json"` resolves — do not inline/fork the C3 shape.
- `created_at` ordering relies on RFC3339 UTC strings sorting lexicographically; `rowid` breaks ties.
- Writes are **idempotent** (`INSERT OR REPLACE` on `turn_id`) — a re-POST updates in place, no dup row.
