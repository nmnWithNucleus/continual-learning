# Storage Service — Charter

> The durable data layer for Nucleus v0: the `/raw`, `/context`, and `/sessions` stores and the
> per-user model directory. Stable doc — working state lives in [HANDOFF.md](HANDOFF.md);
> system-wide architecture + contracts in [../../ARCHITECTURE.md](../../ARCHITECTURE.md).

**Status:** chartered · **Last updated:** 2026-07-08

## Mission

Own every byte the product persists — ALL durable stores live here, per
[ARCHITECTURE §Ownership splits](../../ARCHITECTURE.md): raw capture blobs (`/raw`), the processed
life-stream (`/context`), every conversation with the model (`/sessions`, incl. full mentor + tool
traces), and the registry of per-user adapters (model directory). Make all of it trivially
retrievable by **(user, time)** — the axis every consumer leans on, from continuum's
training-window reads (C10) to "what happened Tuesday" recall — and make per-user isolation,
encryption at rest, and per-store deletion primitives properties of the layer itself, not
obligations on its callers. Platform composes those primitives into the cross-store
right-to-be-forgotten pipeline with proof-of-deletion (its M2). Storage produces no data and
trains no models; it keeps what others produce safe, ordered, and fast to read.

## Scope — v0

| | Item | Notes / owning sibling |
|---|---|---|
| In | `/raw` store | raw capture blobs; recording writes via ingest, the C1 envelope carries the ref; custody split in ARCHITECTURE §Ownership splits |
| In | `/context` store | processed life-stream records; storing side of C2 |
| In | `/sessions` store | conversations → sessions → turns, incl. full mentor + tool traces; storing side of C4 |
| In | Model directory | per-user adapter registry: version, base-model hash, training window, eval report, active/rolled-back; behind C5/C6 |
| In | People/known-faces registry persistence | data-processing matches/enriches, input curates the UX; storage persists — split in ARCHITECTURE §Ownership splits |
| In | Schemas + indexing | canonical record schemas; every store indexed by (user_id, time) |
| In | Time-ranged retrieval | per-user time-window reads (recall queries); producing side of C10 (watermarked training-window export) |
| In | Recency/semantic index | over `/context` + `/sessions`; producing side of C11, consumed by input's QueryBuilder for same-day grounding |
| In | Per-user isolation | hard namespace per user; cross-user access fails closed |
| In | Encryption at rest | all stores and backups |
| In | Retention + deletion primitives | full-user delete (incl. `/raw` + adapter artifacts) and time-slice delete, auditable |
| In | Backup/restore | scheduled backups, tested restore |
| In | Observability (`/metrics` + dashboard JSON) | expose `/metrics` (request rate/latency/errors **plus** DB/query metrics — query latency, rows read/written, DB/file size, connection/pool health for the `/sessions` + model-directory stores) and own the Grafana dashboard JSON; shared Prometheus/Grafana backbone is Platform's — see [../../ARCHITECTURE.md](../../ARCHITECTURE.md) §Observability |
| Out | Producing processed records | Data Processing Service |
| Out | Raw capture + upload | Recording Service (the durable blobs those uploads land in are ours) |
| Out | Cross-store deletion orchestration + proof-of-deletion | Platform Service (its M2), calling our per-store primitives |
| Out | Training jobs / adapter production | Continuum Service |
| Out | Serving, mentor orchestration, context assembly | Inference Service |
| Out | User-facing request building | Input Service |
| Out | Response delivery | Output Service |
| Out | Infra provisioning (DBs, buckets, network, SLURM) | Platform Service |

## Position in the system

Writers upstream, readers downstream. Contracts are owned in
[../../ARCHITECTURE.md § Contracts](../../ARCHITECTURE.md) — referenced here by ID, never
redefined.

| Contract | Peer | Storage's role |
|---|---|---|
| C1 (blob leg) | recording → `/raw` | host the blob store: raw blobs land via ingest; the C1 envelope carries the ref onward to data-processing |
| C2 | data-processing → `/context` | serve the write path: land processed records idempotently, time-indexed on arrival |
| C4 | inference → `/sessions` | serve the write path: persist turn records incl. mentor + tool traces, keyed conversation → session → turn |
| C5 | continuum → model directory | host the registry: accept adapter version entries; one active adapter per user; rollback history kept |
| C6 | inference ↔ model directory | serve the hot read path: resolve the active adapter for user_id per request, within a tight latency budget |
| C10 | storage → continuum | serve the training-window read: time-ranged, watermarked export of `/context` + `/sessions` per user; watermark semantics (late-arriving/reprocessed records, pipeline-version bumps) are C10's core design work, pinned in ARCHITECTURE's C10 row as it lands |
| C11 | storage → input (QueryBuilder) | serve the recent-context read: recency/semantic retrieval over `/context` + `/sessions`; the index lives here, QueryBuilder decides what enters the UserPrompt |

### The time index (the load-bearing decision)
- Every record carries device wall-clock `t_start`/`t_end` (from C2/C4) **and** a
  storage-assigned `ingest_time`. Wall-clock is the query axis; ingest time is the audit axis.
- All timestamps stored UTC; the user's local timezone stored alongside, so "what happened
  Tuesday" resolves in user-local time.
- A user's streams overlap (body cam + computer at the same moment): one timeline per user,
  indexed (user_id, t_start), with modality/device as filter columns — not parallel timelines.
- Continuum's nightly window (C10) is `[last_trained_t, now)` per user — a single index-range
  scan must satisfy it at v0 scale (handful of pilot users).

## v0 deliverables

| M | Deliverable | Exit criterion |
|---|---|---|
| M0 | Foundations: schemas for all three stores; tech pick (metadata DB + GCS blob layout); time + isolation conventions | DDL applied on a dev instance; C2/C4/C5 field mapping reviewed with peer services; decisions recorded here + ARCHITECTURE.md |
| M1 | `/context` live: C2 write path + time-ranged read API | a full pilot-day of processed stream lands; a time-window query returns records correctly ordered across devices; re-ingest is idempotent |
| M2 | `/sessions` live: C4 write path + session/turn reads | inference persists a complete turn incl. mentor + tool traces; a conversation replays exactly from the store |
| M3 | Model directory live: C5 registration + C6 resolution | continuum registers an adapter; inference resolves the active adapter within budget; rollback flips resolution atomically |
| M4 | Security baseline: encryption at rest everywhere + isolation test suite | cross-user access attempts fail closed under test; encryption verified on DB, blobs, and backups |
| M5 | Retention + deletion primitives: full-user delete + time-slice delete | full-user delete purges `/raw`, `/context`, `/sessions`, directory entries + adapter artifacts and schedules backup expiry; deletion manifest is auditable; platform's orchestration + proof-of-deletion (its M2) calls these primitives — we don't own the end-to-end pipeline |
| M6 | Backup/restore | scheduled backups running; point-in-time restore drill passes on a dev instance |
| M7 | Metrics + dashboard | service `/metrics` scraped by the shared Prometheus; dashboard (`dashboards/*.json`) shows request rate/latency/errors + DB/query metrics (query latency, rows read/written, DB/file size, pool health); Platform provisions it (§Observability) |

## Open questions

Engineering:
1. **Storage tech.** Postgres (records + directory) + GCS (bulk payloads, adapter artifacts) is
   the lean default for a handful of users — confirm with platform at M0, incl. where the DB runs.
2. **Adapter artifact placement.** The directory holds adapter artifacts + metadata only —
   BWM (base-model) weights custody is inference's (ARCHITECTURE §Ownership splits). Adapter
   weight files must sit where vLLM can hot-swap fast (GCS vs NFS vs node-local cache) — split
   with inference + platform.
3. **Clock skew.** Does data-processing normalize device clocks before C2, or does storage keep
   raw + corrected times? Lean: normalize upstream; storage stores C2's values + `ingest_time`.
4. **ID minting.** Session/turn ids originate in input (C3) — does storage enforce referential
   integrity on C4 writes, or trust writers?

Research:
5. **Deletion vs weights.** A time-slice delete of records already trained into an adapter is not
   executable by storage alone — v0 default is full retrain from retained records; final policy is
   continuum × platform's open question (ARCHITECTURE §Ownership splits). Storage's part stays
   record-level.

*Resolved 2026-07-08:* the training-window read is now **C10** (we produce it; see contract
table — watermark semantics remain its design work); the semantic/recency index is now **C11**
and lives here; full-user delete cascades `/raw` — the blobs are our store, our primitive.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Breach of life-stream data (the most sensitive data a user has) | product-ending trust loss | encryption at rest everywhere; hard per-user isolation; fail-closed access tests (M4); least-privilege creds per peer service |
| Time-index defects (skew, tz, ordering) | training windows and recall queries silently wrong | UTC-only storage; `ingest_time` audit column; C2 validation rejects non-monotonic/absurd timestamps; cross-device ordering test in M1 |
| C6 sits on the request path | adds latency to every user turn | cached resolution + explicit budget agreed with inference; fallback to base model if directory unreachable |
| Incomplete deletion (backups, raw blobs, adapters) | right-to-be-forgotten violated | deletion manifest enumerates every store incl. `/raw`; backup expiry policy; per-user LoRA keeps adapter delete clean; platform's proof-of-deletion (its M2) verifies end-to-end |
| Unbounded stream growth | cost blowup + degraded queries | metadata/bulk split (DB vs GCS) from M0; retention hooks from day one; per-user growth tracked |
| Upstream schema churn (pipeline versions) | readers break on old records | `pipeline_version` first-class in schema; additive-only migrations; contract changes route through ARCHITECTURE.md |

## Team shape

V0: one lead session + on-demand workstream agents (tracked in [HANDOFF.md](HANDOFF.md)).
Eventual sub-teams:

| Sub-team | Owns |
|---|---|
| Data platform | schemas, write/read APIs, migrations, the time index |
| Security & privacy | encryption, isolation, deletion/retention, audits |
| Reliability | backups, restore drills, SLOs, capacity |
| Retrieval | the recency/semantic index behind C11 |

## Related work

- [poc/live_stream_stability](../../../poc/live_stream_stability/HANDOFF.md) — GCS layout and
  bucket posture precedent (uniform bucket-level access + public-access prevention, signed URLs,
  GCS as bulk source of truth); "manifests are the spine" carries into the record schemas.
- [poc/live_video_chat](../../../poc/live_video_chat/HANDOFF.md) — contracts-as-spine workstream
  pattern this charter's milestones follow.
- Outside: vLLM multi-LoRA serving loads adapter weights from local paths — informs OQ2
  (artifact placement near the serving node).
