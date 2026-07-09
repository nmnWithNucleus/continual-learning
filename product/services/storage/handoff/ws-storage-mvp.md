# WS-D storage — serve-loop MVP (v0.0) worklog

House style: Goal / Done / In flight / Next / Gotchas.

## Goal
Build the durable layer for the text-only serve-loop skeleton: persist a **C4** turn record
keyed by `session_id`/`turn_id` (re-readable), and serve the trivial **C6** model-directory
resolve (base model, no adapter). FastAPI + SQLite on `:8083`. `/sessions` + model directory
only — no `/context`, no `/raw` (later slices).

## Done
- **Layout:** `app/{__init__,schemas,models,db,main}.py`, `tests/{conftest,test_turns,test_resolve}.py`,
  `run.sh`, `requirements.txt`, `pytest.ini`.
- **Endpoints** (all live, smoke-tested on uvicorn `:8083`):
  - `POST /sessions/turns` → jsonschema-validate against `c4_turn_record.v0.json` (authoritative;
    the nested C3 `$ref` resolves via a `referencing` registry over `contracts/*.json`), then a
    pydantic mirror check, then persist verbatim. Returns `{ok, turn_id}`. Invalid → **422** with
    `{error, violations:[{path,message}]}`. Idempotent (`INSERT OR REPLACE` on `turn_id`).
  - `GET /sessions/turns/{turn_id}` → stored C4, **404** if absent.
  - `GET /sessions/{session_id}/turns` → C4 list, `ORDER BY created_at ASC, rowid ASC`.
  - `GET /model-directory/resolve?user_id=…` → C6 body; own output re-validated against
    `c6_resolve.v0.json` before serving. Seeded base entry at startup.
  - `GET /health` → `{ok:true}`.
- **Persistence:** SQLite (`STORAGE_DB_PATH`, default `app/dev.db`). `turns` table stores the full
  C4 as JSON (exact round-trip) with PK `turn_id` + index `(session_id, created_at)`;
  `model_directory` table holds the seeded base row (sentinel `_base_`; per-user rows would
  override later, none in v0).
- **pydantic models** mirror C3 (nested) + C4 + C6 with `extra="forbid"` (== `additionalProperties:false`)
  and `Literal` consts.
- **Tests:** 10 pass — write→read round-trip (+ schema-validate both ways), list-by-session
  ordering + session isolation, unknown-session → `[]`, idempotent re-write, four invalid-C4
  rejections (missing field, extra field, wrong const, broken nested-C3 role → exercises `$ref`),
  C6 resolve (+ schema-validate) for multiple users, missing `user_id` → 422.
- **Ran here:** `pip install jsonschema` (rest of stack already present in the `moe` env),
  `python3 -m pytest -q` → `10 passed`. Live uvicorn smoke run: health / resolve / C4 write→read /
  session list / 404 / 422 all green.

## In flight
- Nothing open. WS-D slice complete.

## Next (later slices, per CHARTER)
- M1 `/context`: C2 write path + time-ranged read.
- M3 model directory: C5 adapter registration → per-user overrides in `model_directory`.
- M4 security: encryption at rest + fail-closed isolation tests. M5 deletion primitives.
- Integrator wiring: inference writes C4 + reads C6 against `:8083`.

## Gotchas
- **Contracts are source of truth.** Validation resolves C4→C3 via a `referencing.Registry`
  keyed by each schema's `$id`; don't inline the C3 shape or the `$ref` breaks.
- Ordering assumes RFC3339 UTC strings sort lexicographically (they do with fixed `…Z` format);
  `rowid` is the stable tie-breaker.
- Env not GPU-bound: this service is `MODEL_BACKEND`-agnostic (it never calls the model).
- No agent commits — files only; founders' session commits after integration.
