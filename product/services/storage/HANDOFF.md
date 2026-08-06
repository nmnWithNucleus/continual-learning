# HANDOFF — Storage service board

> Single touch-point for any agent (or human) picking up work on this service.
> Read [CHARTER.md](CHARTER.md) first (mission/scope/interfaces), then this file.
>
> **This is a board, not a log.** It is rewritten in place to describe *today*; nothing accumulates
> here ([../../ORG.md](../../ORG.md) §Documentation protocol).
>
> | Looking for | Go to |
> |---|---|
> | *How did we get here?* | [handoff/worklog.md](handoff/worklog.md) — newest first |
> | *What did the founders decide?* | [../../DECISIONS.md](../../DECISIONS.md) — the `D-n` register |

**Stage: PROTOTYPE** ([D19](../../DECISIONS.md)) · **Status:** serve loop + capture + the *D18
expansion built and live* · *Last updated:* 2026-08-06 (the D27/D28 joint rows BUILT on
branch `dp-rebuild-v1` at rebuild Stage E; the live `:8083` service now launches from the
main-pinned worktree `/home/ubuntu/nmn/dp-v0-live` — same interpreter, same data paths,
so branch work can no longer reach it; nothing it serves changed)

---

## Current state

**Suite: 310 passed** (re-run 2026-07-27). FastAPI + SQLite on `:8083`.

**Serve loop** — `POST /sessions/turns` (C4, validated against `contracts/c4_turn_record.v0.json`
incl. the nested-C3 `$ref`, idempotent on `turn_id`) · `GET /sessions/turns/{turn_id}` ·
`GET /sessions/{session_id}/turns` · `GET /model-directory/resolve?user_id=` (C6; still the trivial
base entry — see §Next) · `GET /health`.

**Capture** — `PUT /raw/blobs?…` (verifies the body's SHA-256 → 422 on mismatch, mints an **opaque**
`blob_ref`, idempotent on `chunk_id`) · `GET /raw/blobs?ref=` (*query* param, because a `blob_ref`
may contain `/`; 404 also when the blob was since-deleted, which consumers must tolerate) · `POST
/context/records` (C2-validated, idempotent upsert on `record_id`, assigns storage's own
`ingest_time`, an audit axis, *not* in C2, preserved across reprocess) · `GET
/context/records/{record_id}` · `GET /context/records?user_id=&from=&to=` (half-open `[from, to)`,
per-user isolation enforced by the mandatory `user_id`).

**The D18 expansion — built 2026-07-27, this is what changed most recently:**

- **C12 profile** — `GET/PUT /users/{user_id}/profile`. *404 on absence, no server-side default*,
  so a user without `home_tz` is *not schedulable*: an operational alert, never a silent skip.
- *Declared, never inferred* ([D19](../../DECISIONS.md)) — storage never writes it unprompted, so
  it does not chase a travelling user's device.
- **Training-window ledger + the sole `window_id` minter** — `POST /training/windows`
  (get-or-create the user's *open* window), `GET /training/windows` (enumerate, continuum's source
  for prior windows), `POST /training/windows/{window_id}/close`. `window_id` is
  `w<YYYYMMDD>T<HHMMSS>Z` (`window_id.py`: mint format `w%Y%m%dT%H%M%SZ`, validator
  `^w\d{8}T\d{6}Z$`), minted *once from the window's end instant* and *parsed by nobody*.
- Storage is the only minter and the only validator.
- **Day-log materialization (C10 evolved)** — `GET /training/daylog?user_id=&window_id=`,
  materialized *on demand at fetch* ([D19](../../DECISIONS.md)) rather than by a scheduler.
- Reprocessed records resolve *latest `ingest_time` wins per `(chunk_id, content.kind,
  discriminator)`* — on `ingest_time` because `pipeline_version` is a composed string and not
  orderable, on `kind` because captions and transcripts can share one `pipeline_version`. Every
  body is stamped with its `recipe_id` *and* `daylog_format_version`, and continuum *refuses* a
  body whose stamps are not the ones it trains under.
- **C13 recipe registry** (`GET /recipes/{recipe_id}`, `GET /policies/{policy_id}`) and
  *C14 reservoir* (`GET/POST /reservoir/{user_id}`, `/reservoir/{user_id}/{window_id}`). C14 serves
  a *ledger, not corpora* — by design.
- **Tables:** `turns`, `model_directory`, `raw_blobs`, `context_records`, `user_profiles`,
  `training_windows`, `day_logs`.
- **M9 parity passes** — 31 binding checks over *two* window origins including a *misaligned* one
  (the first run passed only on a grid-aligned origin, which no real window has; the bar was narrowed
  and the proof redone, [D20](../../DECISIONS.md)).

## Workstream index
| WS | What | Status | Working file |
|---|---|---|---|
| WS-D | serve-loop MVP: `/sessions` (C4) + model directory (C6) | **done** | [handoff/ws-storage-mvp.md](handoff/ws-storage-mvp.md) |
| WS-C | learn-loop capture M0: `/raw` blob leg (C1) + `/context` store (C2) | **done** | [handoff/worklog.md](handoff/worklog.md) 2026-07-09 |
| WS-D18 | scope expansion: C12 profile · window ledger + `window_id` minter · day-log materialization (C10 evolved) · C13 · C14 | **done ✅ 2026-07-27** | [handoff/worklog.md](handoff/worklog.md) 2026-07-26 |

## Scope boundary

- **C11** (recency / semantic index) is still *deliberately absent* — a later slice.
- **The model directory is still the trivial C6 row** (`user_id, model_id, adapter, adapter_path`):
  no entries log, no status column. Hosting C5 is therefore a *build, not a transport swap*.
- **Storage owns the day-log's *representation* outright; its *content* is a contract neither service
  may move alone** ([D20](../../DECISIONS.md)) — *if the trainer can see it, it is contract; if only
  storage can see it, it is ours.*

## Incoming — the DP rebuild's joint rows (ratified 2026-08-06; BUILT on branch at Stage E)

Two of the rebuild's six rows are joint with us — **D27** and **D28**
([../../DECISIONS.md](../../DECISIONS.md)). Both were BUILT on `dp-rebuild-v1` at the
rebuild's Stage E (2026-08-06, worklog:
[../data-processing/docs/refactor_stage_E.md](../data-processing/docs/refactor_stage_E.md));
nothing RUNNING here changes before the Stage F cutover:

- **D27** — `ingest_time` split into `created_at` + `updated_at` (the byte-compare is the
  upsert; a no-op re-POST leaves the row completely untouched); the training-window axis
  and the day-log dedup key moved to `updated_at`. Built, suite-proven (heal×window
  matrix, all three Stage D shapes).
- **D28** — the day-log renderer walks C2 v1 `content.slots`
  ([../../contracts/c10_daylog.v2.json](../../contracts/c10_daylog.v2.json)); dedup is
  latest `updated_at` per `(chunk_id)`, rowid tiebreak; stamps bumped
  (`daylog_format_version` "2" + `consolidation-v2.0`). D20's parity bar re-baselined:
  31 checks green, tier A byte-identical to the untouched v0 reference over both origins.
- E-2 built whole-record (`DELETE /context/records` by `record_id` / `chunk_id` /
  `pipeline_version`; manifest by `pipeline_version`; day-log cascade; dry-run): §Next
  item 1's kind-aware shape retired unbuilt (D28).
- The branch is C2-v1-only end to end (founder ruling R1): the v0 wire keeps running on
  the LIVE worktree service until cutover, and continuum must be TAUGHT the two new
  stamps before then or its correct refusal blocks every window (the Stage F gate).

## Next

| # | Item | Why it's open |
|---|---|---|
| 1 | **E-2 — BUILT whole-record on `dp-rebuild-v1`** (rebuild Stage E, [D28](../../DECISIONS.md)); reaches the RUNNING service at the Stage F cutover. Remaining here: Platform M2 orchestration, the reservoir leg of the cascade (window-granular, not record-granular — full-user delete's job), and M5's time-slice delete. | Until cutover a re-wipe is still the only retraction the LIVE service has; the branch primitive ends that. CHARTER M5 |
| 2 | **C5 registration → the model-directory build** (M3). Three constraints, and they are not documentation: a *three*-value status enum (or `record_gate_failure()` has nowhere to land); *nullable* `adapter_dir` + `base_model_hash` (gate-failed rows carry NULLs); C6 eligibility as a *log replay*, not "latest row wins". | The last would otherwise serve a gate-failed candidate — the exact ungated swap the gate exists to prevent. Waits on the C5 shape pin (deferred by [D19](../../DECISIONS.md)) |
| 3 | **D9 observability** — `/metrics` (request rate/latency/errors + query latency, rows read/written, DB size) + our Grafana dashboard JSON. | Platform's shared backbone is the blocker; emission is ours. CHARTER M7 |
| 4 | **Retention mechanism** ([D19](../../DECISIONS.md)) — a versioned per-store retention document, every store `keep_forever`, read and surfaced on `/metrics`, *no sweeper*. Rules mark *eligibility*; a separate explicit sweep acts and writes a manifest. | So a bad config edit can produce a wrong report, never silent data loss |
| 5 | **Encryption at rest + per-user isolation tests** (M4). | Not started |
| 6 | **Postgres + GCS migration** — metadata in Postgres, day-logs/corpora in GCS ([D19](../../DECISIONS.md) option (c)). | Kept cheap by a rule, not foresight: every new store goes behind a **narrow interface** from day one, so the swap is a backend change |

## Gotchas
- **Contracts are the source of truth.** Schema validation uses a `referencing` registry so
  C4's `$ref: "c3_userprompt.v0.json"` resolves — do not inline/fork the C3 shape.
- `created_at` ordering relies on RFC3339 UTC strings sorting lexicographically; `rowid` breaks ties.
- Writes are **idempotent** (`INSERT OR REPLACE` on `turn_id`) — a re-POST updates in place, no dup row.
