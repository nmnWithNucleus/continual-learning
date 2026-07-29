# Storage Service — Charter

> The durable data layer for Nucleus v0: the `/raw`, `/context`, and `/sessions` stores and the
> per-user model directory. Stable doc — working state lives in [HANDOFF.md](HANDOFF.md);
> system-wide architecture + contracts in [../../ARCHITECTURE.md](../../ARCHITECTURE.md).

> ### ⚠️ STAGE: PROTOTYPE (pre-dev, pre-production) — D19, 2026-07-27
> This charter is written in a production voice. **It is aspirational, not a commitment.** We are
> building one end-to-end product that genuinely works, as fast as we can honestly get there.
> **Licensed:** re-cutting contracts rather than versioning them (a pinned shape is stable enough
> to build against today, never immutable — which is why we no longer call one *frozen*); wiping
> and re-collecting stored data rather than migrating it; deferring durability work with the
> reason written down.
> **Not licensed:** skipping [ORG.md](../../ORG.md)'s contract-edit order, leaving a decision
> unrecorded, silent breakage, or calling a thing BUILT when it is only ratified.
> Full posture + what changes at dev/prod: [ARCHITECTURE.md](../../ARCHITECTURE.md) §Stage.

**Status:** chartered · **Last updated:** 2026-07-27 · the [D18](../../DECISIONS.md) scope
expansion is `built` (`a5a48fb`, 310 tests). Lineage: [§How this charter got here](#how-this-charter-got-here).

## Mission

Own every byte the product persists — every durable store lives here, per [ARCHITECTURE §Ownership
splits](../../ARCHITECTURE.md): raw capture blobs (`/raw`), the processed life-stream
(`/context`), every conversation with the model (`/sessions`, incl. full mentor + tool traces),
and the registry of per-user adapters (model directory). Make all of it trivially retrievable by
**(user, time)** — the axis every consumer leans on, from continuum's training-window reads (C10)
to "what happened Tuesday" recall, and make per-user isolation, encryption at rest, and per-store
deletion primitives properties of the layer itself, not obligations on its callers. Platform
composes those primitives into the cross-store right-to-be-forgotten pipeline with
proof-of-deletion (its M2). Storage produces no data and trains no models; it keeps what others
produce safe, ordered, and fast to read.

## Scope — v0

> **Expansion RATIFIED 2026-07-26 (D18) — the "data jobs" for the learn loop. **BUILT 2026-07-27** (`a5a48fb` storage · `1757efb` continuum · `2698b63` DP).**
> The continuum/Morpheus design settled that continuum stays a lean training engine and storage
> owns the *data* work around it; the founders' storage/C10 board ratified it and minted the
> contract IDs. Rows tagged **In+** below. The 2026-07-23 framing is kept verbatim because it is
> still the rationale:
> **(1) Day-log materialization** — a scheduled job renders a user-day's `/context` (C2) records
> into the segment/block **day-log** (incl. `render_block` anchored text); continuum fetches it via
> C10 (which evolves from "raw record range read" to "day-log fetch"). **(2) Recipe registry** —
> host versioned consolidation/serving recipes; continuum *and* inference pull the pinned recipe.
> **(3) Reservoir custody** — store the amplified corpora continuum writes (audit/provenance);
> replay itself re-reads prior day-logs. This keeps "storage produces no *faithful* data, trains
> no models" intact: the day-log is a derived VIEW over C2, the reservoir holds training artifacts
> others produced, and the registry holds config — none of it is new sensor data or model weights.
>
> **What D18 added on top of that framing, and what it costs us.** (a) A **fourth** responsibility,
> the per-user **profile** (`home_tz`, C12) — and it is a *prerequisite*, not a peer: if storage
> renders the day-log then storage inherits D17's timezone resolution (`daylog._block_zone` — the
> record's `device_tz` wins, the window's `home_tz` falls back), so **materialization reads the
> profile**. (b) The **training-window ledger** and the sole `window_id` minter, which the watermark
> window puts here because `[last_trained_t, now−δ)` is a fact about *our* ingest clock and nothing
> else can own it. (c) An obligation that arrives with the day-log and is easy to miss: **the
> day-log is a second copy of user content**, so **M5's deletion primitives must cascade to it** —
> and to the reservoir. A retraction that clears `/context` and leaves a materialized day-log
> standing has deleted nothing. That is a genuine widening of M5, recorded in its row below.

| | Item | Notes / owning sibling | Card |
|---|---|---|---|
| In | `/raw` store | raw capture blobs; recording writes via ingest, the C1 envelope carries the ref | — |
| In+ | **Day-log materialization** | scheduled job: C2 records → the segment/block day-log, served to continuum over C10 | [↓](#day-log-materialization) |
| In+ | **Training-window ledger + `window_id` minter** | the per-user `last_trained_t` watermark, the durable window rows, and the sole minter | [↓](#the-training-window-ledger-and-the-window_id-minter) |
| In+ | **Recipe registry** (C13) | versioned recipe/config hosting; fetch API for continuum and inference | [↓](#the-recipe-registry-c13) |
| In+ | **Reservoir custody** (C14) | per-user store of the amplified corpora continuum writes; audit and provenance | [↓](#reservoir-custody-c14) |
| In+ | **Per-user profile** (C12) | one durable row per user for policy values the *system* reads to decide its behaviour | [↓](#the-per-user-profile-c12) |
| In | `/context` store | processed life-stream records; storing side of C2 | — |
| In | `/sessions` store | conversations → sessions → turns, incl. full mentor + tool traces; storing side of C4 | — |
| In | Model directory | per-user adapter registry behind C5/C6. Hosting deferred ([D19](../../DECISIONS.md)) | [↓](#the-model-directory) |
| In | People/known-faces registry persistence | data-processing matches/enriches, input curates the UX, storage persists | — |
| In | Schemas + indexing | canonical record schemas; every store indexed by (user_id, time) | — |
| In | Time-ranged retrieval | per-user time-window reads (recall queries); producing side of C10 | — |
| In | Recency/semantic index | over `/context` + `/sessions`; producing side of C11, for input's same-day grounding | — |
| In | Per-user isolation | hard namespace per user; cross-user access fails closed | — |
| In | Encryption at rest | all stores and backups | — |
| In | Retention + deletion primitives | full-user delete (incl. `/raw` + adapter artifacts) and time-slice delete, auditable | — |
| In | Backup/restore | scheduled backups, tested restore | — |
| In | Observability | expose `/metrics` and own the Grafana dashboard JSON; the shared backbone is Platform's | [↓](#observability) |
| Out | Producing processed records | Data Processing Service | — |
| Out | Raw capture + upload | Recording Service (the durable blobs those uploads land in are ours) | — |
| Out | Cross-store deletion orchestration + proof-of-deletion | Platform Service (its M2), calling our per-store primitives | — |
| Out | Training jobs / adapter production | Continuum Service | — |
| Out | Serving, mentor orchestration, context assembly | Inference Service | — |
| Out | User-facing request building | Input Service | — |
| Out | Response delivery | Output Service | — |
| Out | Infra provisioning (DBs, buckets, network, SLURM) | Platform Service | — |

### Day-log materialization
> `built` 2026-07-27 · [D18](../../DECISIONS.md) · `a5a48fb` storage · `1757efb` continuum · `2698b63` data-processing

**In one line.** A scheduled job renders a user-day's `/context` (C2) records into the
segment/block day-log, a derived view over `/context` that continuum fetches via the evolved C10.

**Rules**

- Serve it random-access by `(user_id, window_id)`. The wire is
  [ARCHITECTURE §Contracts → C10](../../ARCHITECTURE.md), never restated here.
- Stamp the body with `daylog_format_version` and `recipe_id`; the format is recipe-versioned.
- Membership is by `ingest_time`, which is what the watermark window requires.
- Segment buckets sit on a **global epoch grid** rather than window-relative (`daylog.py:74`).
- Apply the **one-dialect-per-record** rule: latest `ingest_time` wins per
  `(chunk_id, content.kind, discriminator)`.

**Why it's this way**

- **We inherit `daylog.py`'s `build_daylog` + `_render_block`** — the *product* renderer over C2
  records. We do *not* inherit continuum's parity-locked `Profile.render_block`, which is
  recipe-coupled and stays with the amplifier.
- The global epoch grid is required once membership sits on the ingest axis, and it also makes a
  bucket stable across re-materialization.

**Watch out for**

- The one-dialect rule has a named sub-item the builder had to close first — see
  [§Open questions](#open-questions) item 7, now resolved.

### The training-window ledger and the `window_id` minter
> `built` 2026-07-27 · [D18](../../DECISIONS.md) · `a5a48fb` storage · `1757efb` continuum · `2698b63` data-processing

**In one line.** Storage keeps the per-user `last_trained_t` watermark and the durable window rows,
and is the only place a `window_id` is ever minted.

**Shape** — `window_id`, `t_start`, `t_end` and state per row, behind
`POST /training/windows`.

**Rules**

- `POST /training/windows` is an **idempotent get-or-create** of the user's open window.
- A window's bounds are **immutable once opened**.
- `window_id` is minted in **exactly one place**, format `w<YYYYMMDD>T<HHMMSS>Z`, and is opaque to
  every consumer.

**Why it's this way**

- Immutable bounds are what preserve continuum's crash-safe journal replay. Recomputing `now` on a
  retry would mint a fresh id and force a full re-train, a second C5 entry and a second reservoir
  admission.
- **Storage is the only service that can own this**: the watermark is a fact about *our* ingest
  clock, and nothing else can own it.

### The recipe registry (C13)
> `built` 2026-07-27 · [D18](../../DECISIONS.md) · `a5a48fb` storage · `1757efb` continuum · `2698b63` data-processing

**In one line.** Versioned recipe and config hosting, with a fetch API for continuum's
consolidation recipe and inference's serving-side knobs.

**Rules**

- `recipe_id` == filename stem, **global and versioned — never per-user**.
- The **gate policy is a separate artifact with its own id**. Only the *training recipe* may enter
  a cycle stage key.

**Why it's this way**

- Changing a publish threshold must not fork `recipe_id` or invalidate hours of GPU cache.
- Reference implementation to lift: `../continuum/app/clients/registry.py`.

### Reservoir custody (C14)
> `built` 2026-07-27 · [D18](../../DECISIONS.md) · `a5a48fb` storage · `1757efb` continuum · `2698b63` data-processing

**In one line.** A per-user store of the amplified corpora continuum writes, kept for audit and
provenance.

**Rules**

- Append-only, keyed `(user_id, window_id, recipe_id)`, content-hashed.
- **Amplified or synthetic text never lands in `/context`** — same storage discipline, different
  namespace. This is the one invariant of the data design.
- Deletion here is a deliberate privacy act, never housekeeping, and it is **in M5's cascade**.

**Why it's this way**

- This is audit and provenance, not the replay path: replay re-reads prior day-logs via C10, not
  this store.

### The per-user profile (C12)
> `built` 2026-07-27 · [D18](../../DECISIONS.md) · schema [`c12_user_profile.v0.json`](../../contracts/c12_user_profile.v0.json)

**In one line.** One durable row per user holding **policy** values — things that are neither
sensor data nor recipe config, of which v0 carries exactly one, `home_tz`.

**Shape** — physically **one table keyed by `user_id`** (CTO, 2026-07-27). The day-log renderer
resolves `home_tz` by looking the row up, exactly as every other store already carries `user_id` as
a column.

**Rules**

- `home_tz` is an IANA zone with exactly two jobs: **scheduling**, deciding when this user's
  nightly consolidation fires — a question asked before any of that night's records exist, and
  *fallback*, when a record carries no `device_tz`.
- **Declared, not inferred.** The user sets it; storage never writes it on its own initiative. A
  device's zone may be *suggested* in a UI, never stored as an answer.
- A missing profile is a **404**, and a user with no `home_tz` is *not schedulable* — an
  operational alert, never a silent skip, because D17 abolished default timezones.
- **Identity is not policy.** This row holds only values the *system* reads to decide its own
  behaviour. An account name, email or avatar belongs to a different table behind a different
  contract.
- **Do not make `user_id` a validated foreign key yet.**

**Why it's this way**

- The identity fence is what makes the general noun *profile* safe, and what stops inference or
  input reading identity out of a storage *policy* read.
- **`home_tz` does not move when the user travels.** A week in Tokyo changes every record's
  `device_tz` and changes nothing here, so the night boundary stays put instead of jumping 9 h and
  producing a 15 h night followed by a 33 h one. That is the whole point.
- It **cannot ride the recipe registry**: `recipe_id` is global and versioned (`recipe_id` ==
  filename stem), so a per-user value there would fork `recipe_id` per user.
- It is **not** the pipeline's time semantics — that is per-record `device_tz`, and *not* part
  of the C10 range arithmetic.
- On the foreign key: today `user_id` is a bare string with no owning table anywhere in the system,
  so a validating FK would mean a user must be provisioned before capture works, which blocks the
  prototype's simplest path. The row may or may not exist, and its absence is a 404, never a
  rejected write elsewhere.

**Watch out for**

- In the prototype the operator sets `home_tz` directly and there is no auto-seed to fall back on,
  so **a user with no `home_tz` simply does not consolidate**. Visible, not silent.
- A second field is named but deliberately **not** minted until it has a consumer (the E-5
  precedent): a per-user `boundary_local_time` override, today a *global* recipe knob that cannot
  express a night-shift worker.

**How it got here**

- **2026-07-27 — `home_tz` became declared, not inferred.**
  - **Was** — D18's first draft had storage auto-seed `home_tz` from the most recent
    device-reported `device_tz`.
  - **Changed** — D19 overturned it: the user sets the value, and storage never writes it
    unprompted.
  - **Now** — a guess can never masquerade as an answer, and the profile does not chase a
    travelling user's device.
  - **Payoff** — a guessed zone and a chosen zone are different facts, and only the second can be
    corrected by the person who knows.

### The model directory
> `designed` · hosting deferred 2026-07-27 · [D19](../../DECISIONS.md)

**In one line.** The per-user adapter registry behind C5 and C6 — which adapter a user has, and
which one may serve.

**Rules**

- **Hosting is deferred.** The consumer is inference (C6), which is not being built, and
  continuum's local `entries.jsonl` carries the lifecycle meanwhile. This is not on the learn-loop
  critical path.
- C5's shape is described here **as continuum builds it today and not yet pinned** — pinning it
  needs inference at the table, and describing it is not pinning it.
- Nine fields: `contract:"C5"`, `user_id`, `adapter_version`, `adapter_dir`, `base_model_hash`,
  `training_window`, `recipe_id`, `eval_report`, `status` ∈ `active` | `gate_failed` |
  `rolled_back` (`../continuum/app/publish.py:83-114`).

**Watch out for**

- **Hosting C5 is a build, not a transport swap.** Storage's `model_directory` table today is the
  *trivial C6 row* (`user_id, model_id, adapter, adapter_path` — `app/db.py:59-63`), with no
  entries log and no status column at all. The short field list this row used to carry is precisely
  what an implementer would have built to.
- Three of the nine fields are load-bearing for **us** at pin time, not just for the docs.
  - **`status` is a three-value enum.** A two-value `active/rolled_back` column has nowhere to put
    `record_gate_failure()`, and the audit trail for blocked candidates would be dropped silently
    at the swap.
  - **`gate_failed` rows carry NULL `adapter_dir` and NULL `base_model_hash`**, so neither column
    can be `NOT NULL`, or the schema rejects exactly the rows that matter most.
  - **C6 eligibility is a log replay, not "latest row wins."** `active` pushes, `rolled_back` pops
    the matching top, and `gate_failed` does neither (`publish.py:33-44`). A directory that
    resolved the newest entry would serve a gate-failed candidate — the ungated swap the gate
    exists to prevent.

### Observability
> `designed` · [D9](../../DECISIONS.md)

**In one line.** Storage exposes `/metrics` and owns its Grafana dashboard JSON; the shared
Prometheus and Grafana backbone is Platform's.

**Rules**

- Expose request rate, latency and errors, **plus** DB and query metrics: query latency, rows read
  and written, DB and file size, and connection/pool health for the `/sessions` and
  model-directory stores.
- Own the dashboard JSON (`dashboards/*.json`); Platform provisions it. Shape:
  [ARCHITECTURE.md](../../ARCHITECTURE.md) §Observability.

## Position in the system

Writers upstream, readers downstream. Contracts are owned in
[../../ARCHITECTURE.md § Contracts](../../ARCHITECTURE.md) — referenced here by ID, never
redefined.

| Contract | Peer | Storage's role |
|---|---|---|
| C1 (blob leg) | recording → `/raw` | host the blob store: raw blobs land via ingest, and the envelope carries the ref onward |
| C2 | data-processing → `/context` | serve the write path: land processed records idempotently, time-indexed on arrival |
| C4 | inference → `/sessions` | serve the write path: persist turn records incl. traces, keyed conversation → session → turn |
| C5 | continuum → model directory | host the registry: accept adapter version entries; one active adapter per user; rollback kept |
| C6 | inference ↔ model directory | serve the hot read path: resolve the active adapter per request, within a tight budget |
| C10 | storage → continuum | serve the training-window read: a day-log fetch by `(user_id, window_id)`, plus window-ledger open/close |
| C12 | storage → continuum *(later inference/input)* | serve the per-user profile read (`home_tz`); the write surface is ours, prose-pinned on D11's precedent |
| C13 | storage → continuum + inference | host + serve the recipe registry: versioned recipes, and the separately-versioned gate policy |
| C14 | continuum ↔ storage | host the reservoir: accept amplified-corpus writes, serve the ledger |
| C11 | storage → input (QueryBuilder) | serve the recent-context read; the index lives here, QueryBuilder decides what enters the UserPrompt |

**On C10.** It evolved in place and is `built` ([D18](../../DECISIONS.md), 2026-07-27). Its shape,
rules, reasoning and full evolution live in one home,
[ARCHITECTURE §Contracts → C10](../../ARCHITECTURE.md). Two things worth knowing from here: the
window is `[last_trained_t, now−δ)` on the `ingest_time` axis, and the raw range read
`GET /context/records?user_id=&from=&to=` is **not** retired — it stays first-class as D12's beta
training feed, the debugging path, and C11-adjacent. C10-evolved is additive.

### The time index (the load-bearing decision)

**In one line.** Every record is placed on two clocks — the device's wall-clock and storage's own
ingest clock, and which one answers a question is decided here, once.

**Rules**

- Every record carries device wall-clock `t_start`/`t_end` (from C2/C4) **and** a storage-assigned
  `ingest_time`. Wall-clock is the query axis; ingest time is the audit axis.
- **All timestamps are stored UTC, with the capturing device's local timezone alongside**
  (`device_tz`, IANA + `device_utc_offset_minutes`, carried on C2 `source{}`).
- **UTC is canonical and is the only query axis.** `t_start` orders the timeline and answers every
  range read; `GET /context/records?from=&to=` needs no timezone at all, because C10 is a duration
  query.
- Never persist a derived local wall-clock string. `device_local_time` is recoverable from instant
  plus zone, and storing it makes two sources of truth that will disagree.
- Never accept a timezone *abbreviation* — `PST` and `MST` are ambiguous and DST-sensitive. IANA
  ids only.
- One timeline per user, indexed `(user_id, t_start)`, with modality and device as filter columns.
  A user's streams overlap — body cam and computer at the same moment, and these are not parallel
  timelines.
- Both axes are indexed.

**Why it's this way**

- Storing the zone alongside the instant is what makes "what happened Tuesday" resolve in the
  user's *actual* local time, including for a day they spent in another zone. **Built 2026-07-26**
  ([D17](../../DECISIONS.md)); this line had promised it since 2026-07-08 while `context_records`
  had no such column, and the gap is now closed rather than the promise withdrawn.
- The zone is *context beside* the instant, never a replacement for it, and never an index.
- **Two timezone concepts, deliberately distinct** (see [ARCHITECTURE §Ownership
  splits](../../ARCHITECTURE.md) → *User timezone*): the per-record `device_tz` is the *fact* —
  where the user was, owned by the device, correct under travel, while the per-user profile
  `home_tz` is the *policy*, when this user's night is, owned by us.
- **`t_start` and `ingest_time` are two axes and D18 assigns each a job.** Conflating them is the
  same class of error D17 fixed for timezones. Event time is the *content* axis: recall queries,
  day-log bucketing, block anchors, and deletion ranges — "delete last Tuesday" is a claim about a
  lived period.
- **Ingest time is the completeness axis.** It is the only axis on which "everything I had" is a
  guarantee, so it is what continuum's training window watermarks on.
- Continuum's nightly window (C10) is `[last_trained_t, now−δ)` per user **on the `ingest_time`
  axis** ([D18](../../DECISIONS.md)). A single index-range scan must satisfy it at v0 scale, a
  handful of pilot users.
- Three properties follow, and they are the reason for the choice: **storage needs no timezone to
  serve C10 at all**; a missed or gate-failed night is *absorbed* into the next window rather
  than lost; and *late data cannot exist*, because `ingest_time` is assigned by us at write, so a
  record can never land below an already-closed boundary.

**Watch out for**

- `δ` (default 60 s) exists for exactly one reason: an in-flight write racing the boundary could
  otherwise be assigned an `ingest_time` below `t_end` yet commit after materialization read the
  range. It is watermark lag, not slack.
- **Note the deliberate asymmetry, and do not "fix" it.** Training windows are ingest-time,
  retraction ranges are event-time. They answer different questions, and making them agree would
  break one of them.

## v0 deliverables

| M | Deliverable | Exit criterion |
|---|---|---|
| M0 | Foundations: schemas for all three stores; tech pick; time + isolation conventions | DDL applied on a dev instance; C2/C4/C5 field mapping reviewed with peers; decisions recorded |
| M1 | `/context` live: C2 write path + time-ranged read API | a pilot-day lands; a time-window query returns records correctly ordered across devices; re-ingest is idempotent |
| M2 | `/sessions` live: C4 write path + session/turn reads | inference persists a complete turn incl. traces; a conversation replays exactly from the store |
| M3 | Model directory live: C5 registration + C6 resolution | continuum registers an adapter; inference resolves it within budget; rollback flips resolution atomically |
| M4 | Security baseline: encryption at rest + isolation test suite | cross-user access attempts fail closed under test; encryption verified on DB, blobs, and backups |
| M5 | Retention + deletion primitives, all cascading to the derived stores | see the card: [↓](#m5--deletion-primitives-and-what-d18-widened) |
| M6 | Backup/restore | scheduled backups running; a point-in-time restore drill passes on a dev instance |
| M7 | Metrics + dashboard | `/metrics` scraped by the shared Prometheus; the dashboard shows request and DB metrics; Platform provisions it |
| M8 | **C12 profile + C13 recipe registry + C14 reservoir** ([D18](../../DECISIONS.md)) | see the card: [↓](#m8--profile-registry-reservoir) |
| M9 | **C10 evolved: day-log materialization + the training-window ledger** ([D18](../../DECISIONS.md)) | see the card: [↓](#m9--the-day-log-parity-bar) |

### M5 — deletion primitives, and what D18 widened
> `designed` · widened 2026-07-26 by [D18](../../DECISIONS.md)

**In one line.** Full-user delete and time-slice delete, plus the kind-aware retraction (E-2) — and
every one of them must reach the derived stores too.

**Exit criterion.** Full-user delete purges `/raw`, `/context`, `/sessions`, directory entries and
adapter artifacts, and schedules backup expiry. The deletion manifest is auditable. Platform's
orchestration and proof-of-deletion (its M2) call these primitives; we do not own the end-to-end
pipeline.

**Rules — E-2's shape** (board-reviewed 2026-07-26)

- `DELETE /context/records?user_id=&from=&to=&kind=&pipeline_version=`, with `user_id` required.
- `from`/`to` are half-open on `t_start` — event time, because a retraction is about a lived
  period.
- `kind` and `pipeline_version` are optional filters.
- It returns an **auditable manifest** of counts by `kind` × `pipeline_version`, and `dry_run=true`
  returns the manifest without deleting.
- It must **re-materialize or invalidate every affected day-log**.

**Why it's this way**

- **D18 widened this and the widening is easy to miss.** Once we materialize day-logs and host the
  reservoir, both are second copies of user content, so both must be in every deletion's cascade.
- A retraction that clears `/context` and leaves a day-log standing has deleted nothing. That is a
  right-to-be-forgotten defect, not a tidiness one.

### M8 — profile, registry, reservoir
> `built` 2026-07-27 · [D18](../../DECISIONS.md)

**In one line.** The three new stores D18 put here: C12, C13 and C14.

**Exit criterion**

- The profile read serves `home_tz` with a 404 on absence and **declared-not-inferred** semantics —
  storage never writes `home_tz` on its own initiative, validated against
  [`c12_user_profile.v0.json`](../../contracts/c12_user_profile.v0.json), with tzdata resolution on
  write.
- The registry serves recipes and the gate policy by id.
- The reservoir accepts append-only corpus writes and serves its ledger.

**Watch out for**

- **C12 lands first** — day-log materialization depends on it.

### M9 — the day-log parity bar
> `built` 2026-07-27 · [D18](../../DECISIONS.md) · bar narrowed by [D20](../../DECISIONS.md), then widened by F4

**In one line.** Storage mints windows idempotently over the `ingest_time` watermark, materializes
day-logs, and serves fetch-by-`(user, window_id)` plus enumeration and close.

**Exit criterion — a differential proof, not a claim.** `scripts/daylog_parity_diff.py`, committed
with its output. Continuum's local path is **not deleted until the narrowed diff is green**.

**Rules — the three tiers**

- **Byte-identical, non-negotiable** — block `text`, block ordering, `block_id`, `anchors`, block
  `quality`, and segment payloads (bounds, caption/asr/ocr, `tz`, `quality`). This is the artifact
  that trains the model.
- **Proven-equivalent, not identical — `seg_id`.** The relabelling must be an order-preserving
  bijection with per-block membership preserved, which the script *measures* rather than assumes.
- **Excluded deliberately — `content_fingerprint`.** It hashes `seg_id`, and it is a cache key
  compared only to *itself* across runs.

**Why it's this way**

- **The bar was narrowed 2026-07-27 (D20) after the first run failed it**, because the bar as
  first written contradicted D18's own materialization rule and no code could satisfy both.
- Continuum's `seg_id` *was* `floor((t − window_start)/segment_seconds)` over an *event-time*
  window origin, while D18 deletes the window origin from storage's grid and puts storage's window
  on the *ingest* axis, where a backlog record yields a *negative* index.
- `seg_id` is written to `segments.jsonl` and **read by nothing** — the trainer and amplifier
  consume `blocks.jsonl` via `load_blocks`. The only reader anywhere is `phase3_daylog.py:88`,
  counting `len(b.seg_ids)` for a histogram, which is invariant under relabelling.
- `content_fingerprint` **should** change when the renderer changes; forcing it to match would make
  the cache lie. It changes once at cutover, that night re-runs, and that is correct.

**Watch out for**

- **The bar was widened 2026-07-27 (F4), in the other direction.** D20 had narrowed *what* is
  compared; F4 found that the *one window* it was compared over had a grid-*aligned* origin —
  which no window this service mints has, since `[watermark, now−δ)` is second-granular.
- That alignment was the only reason continuum's then window-relative bucket grid agreed with
  storage's global one.
- Measured, not argued: shifting that fixture's origin 1–9 s failed tier A at all nine offsets,
  block text included, and at +3 s the segment count and per-block membership too.
- Fixed in continuum's **reference** renderer (`app/daylog.py` — not `morpheus/profiles/`, which is
  the research-golden surface and untouched). It now buckets on the same global epoch grid and
  labels `seg_id` by the same ordinal rule.
- The script runs its whole origin-dependent bar over an aligned **and** a misaligned origin. `A8`
  asserts origin-independence directly, and `P3` asserts of each origin that it is what it claims
  to be, so the misaligned case cannot be repaired by aligning it.

## Retention — the knob ships, the policy does not
> `designed` 2026-07-27 · [D19](../../DECISIONS.md)

**In one line.** Every store is `keep_forever` prototype-wide, and the *mechanism* for changing
that ships even though the *policy* does not.

**Rules**

- Nothing is deleted on a schedule. The only deletions are the explicit privacy primitives in M5.
- Retention is **data, not code** — a versioned document storage reads at startup and on change.
- It is **versioned and logged**, with the active version reported on `/metrics`.
- It is **per-store**, not global: `/raw`, `/context`, day-log, reservoir, `/sessions`, adapters.
- Rules mark **eligibility**; a separate explicit sweep acts and writes a manifest.
- **Deletion is never the mechanism for correctness.**

**Why it's this way**

- The founders' instinct — that this belongs in *service config*, not in code, is right, and this
  section pins the shape so the dev/prod conversation is a config edit rather than an excavation.
- **Changing what we keep must never require a deploy.** It is a *policy*, and we already learned
  this shape once: the gate policy was split from the training recipe (2026-07-24) precisely so a
  threshold change could not fork an artifact id.
- **Versioning answers the question a privacy review actually asks** — *"what policy was in force
  when this record was deleted?"* That is unanswerable if the rule was a constant in a source file,
  and unanswerable after the fact if it was an unversioned config edit.
- **The six stores genuinely differ.** Raw A/V is the expensive one and the least
  re-derivable-from; day-logs are cheap, derived, and the thing replay needs forever; the reservoir
  is append-only by charter. A single global TTL would be wrong for all six.
- **Eligibility plus an explicit sweep is the important one.** A retention config that deletes
  *implicitly* means a typo in a config file destroys user data with no review step and no record.
  This way, the blast radius of a bad edit is a wrong number in a report.
- D18 established the deletion-is-not-correctness rule in the specific case: the one-dialect
  materialization rule fixes the WS-VC double-count, and E-2 does not. It generalises — if we ever
  find ourselves deleting records to make training come out right, the bug is upstream.

**Watch out for**

- **v0 concretely:** one versioned `retention.v0` document, every store `keep_forever`, read and
  surfaced on `/metrics`, and *no sweeper implemented*. That is a few lines, and it is the
  cheapest possible way to buy the future decision.
- The reservoir's own charter line already anticipates the posture — *deletion there is a
  deliberate privacy act, never housekeeping*, and this generalises it to every store.

**What changes at dev/prod** (tracked, not forgotten): choose real per-store values; build the
sweeper and its manifest; decide whether retention tiers by consent state; and reconcile with the
research design-of-record's stance (raw A/V ≤72 h · day-logs forever · 14-night hard-delete
replay), which continuum's canvas has flagged since 2026-07-22 as *"a product decision to take
explicitly"* and which D19 explicitly defers rather than silently adopts.

## Open questions

> **OQ numbers are stable identifiers and are never renumbered** — resolved ones are struck
> through in place (OQ3, OQ6), so a cross-service reference like "storage OQ3" keeps meaning what
> it meant. The counter runs across both subsections, which is why Engineering skips 5.

Engineering:

1. **Storage tech.** Postgres (records + directory) plus GCS (bulk payloads, adapter artifacts) is
   the lean default for a handful of users — confirm with platform at M0, including where the DB
   runs.

   **Why it's this way**

   - **Answered for the prototype ([D19](../../DECISIONS.md), 2026-07-27): stay local** — SQLite
     and filesystem, including for the four new stores (day-log, window ledger, reservoir,
     profile).
   - **The target is unchanged and is option (c)**: metadata in Postgres, day-logs and corpora in
     GCS. It is near the top of the list the moment we leave prototype, because day-logs-forever is
     the first store that grows without bound.
   - **What keeps that migration cheap is a rule, not foresight:** every new store goes behind a
     *narrow interface* in storage from day one, so the swap is a backend change rather than a
     rewrite.
   - Continuum already proved the shape on the client side (`app/clients/` — local today,
     HTTP-to-storage later, the cycle unchanged); storage owes the same on the server side.
   - Two existing properties help and must be preserved: `blob_ref` is already **opaque and
     storage-owned**, and `record_json` is served *byte-verbatim*, so neither leaks the substrate
     to a caller.
2. **Adapter artifact placement.** The directory holds adapter artifacts and metadata only — base
   world model weights custody is inference's (ARCHITECTURE §Ownership splits). Adapter weight
   files must sit where vLLM can hot-swap fast (GCS vs NFS vs node-local cache) — split with
   inference and platform.
3. ~~**Clock skew.** Does data-processing normalize device clocks before C2, or does storage keep
   raw + corrected times?~~ *Resolved ([D17](../../DECISIONS.md), 2026-07-26) — the lean was
   right, and is now built.*

   **Why it's this way**

   - Storage stores **exactly what C2 carries, verbatim, plus its own `ingest_time`**. It corrects
     nothing, and data-processing normalizes nothing — it is a pure passthrough for `device_tz`,
     `device_utc_offset_minutes` and `device_location`.
   - What makes this safe rather than naive is that the envelope now carries its own audit trail:
     `device_clock` (`synced|unsynced`) says whether the stamp is NTP-disciplined,
     `device_utc_offset_minutes` witnesses what the device believed, and `ingest_time` is an
     independent server-side clock.
   - So skew is **detectable after the fact from stored data**, instead of being silently baked in
     by an upstream correction.
   - If a corrected time is ever needed it lands as a new additive field beside the raw one. The
     raw device stamp is never overwritten.
4. **ID minting.** Session and turn ids originate in input (C3) — does storage enforce referential
   integrity on C4 writes, or trust writers?
6. ~~**C10 watermark semantics.** Late-arriving records, reprocessed records, `pipeline_version`
   bumps — what advances `last_trained_t`, and what happens to a record landing with a `t_start`
   inside an already-trained window?~~ *Resolved ([D18](../../DECISIONS.md), 2026-07-26)*, this
   was the session's substantive design work. Full statement in
   [ARCHITECTURE.md](../../ARCHITECTURE.md) § Contracts → *C10 evolved*; the four rules:

   **Why it's this way**

   - **The window watermarks on `ingest_time`, not event time.** This dissolves the question rather
     than answering it: a record landing with a `t_start` inside an already-trained window is
     simply a record whose `ingest_time` is *now*, so it joins the current window and trains,
     rendered in a block anchored to its own real local date.
   - **Late data cannot be lost, because on an ingest-time watermark late data does not exist.**
     Content stays event-time-correct because day-log blocks are formed by temporal adjacency and
     carry their own anchors, so a week-old backlog forms its own blocks rather than corrupting
     today's.
   - **`last_trained_t` advances if and only if the cycle publishes** *(refined 2026-07-27 — the
     first draft also advanced on `skipped_no_data`)*. Gate failure, freeze, crash, no data and
     *too little* data all leave it, so the next window is a strict *superset*.
   - That is the design-of-record's *failed-day merge* obtained structurally rather than by
     bookkeeping — it demotes continuum's `_UserState.debt` to reporting, and it makes the
     min-data floor nearly free: a below-floor night just does not advance, so material
     accumulates until a run is worth it.
   - Named cost: an inactive user's open window grows and is re-scanned nightly, which is correct
     and cheap at v0 scale.
   - **Reprocessed records: one dialect per record, latest `ingest_time` wins**, keyed
     `(chunk_id, content.kind, within-chunk discriminator)`. Keyed on `ingest_time` because
     `pipeline_version` is a *composed* string and therefore not orderable; keyed on `content.kind`
     because Phase-3 proved captions and transcripts can share one `pipeline_version`, so a
     kind-blind rule drops transcripts to drop captions.
   - **This is what actually fixes the re-consolidation double-count** (`daylog.py` filters on
     neither field today), and it is why *E-2 is no longer a correctness blocker for the WS-VC
     cutover*.
   - **A `pipeline_version` bump is a forward-only correction.** It improves future training; it
     does not repair past weights, which on an append-only weight chain is irreducible.
   - The remedy for a dialect bad enough to need repair is a deliberate **rebuild from base over
     retained history** — named as the escape hatch, not built. Accepted, named cost: the same
     lived moment can train twice in two dialects (OQ8 below).
7. ~~**The within-chunk discriminator is not readable from C2 — a blocking sub-item for the build
   slice.**~~ *Resolved (2026-07-27), option (a) taken: it is surfaced.*

   **Why it's this way**

   - `discriminator` is an additive-optional top-level field on C2
     (`contracts/c2_processed_record.v0.json`), emitted by DP only when non-empty — absence, not
     `""`, is the 1:1 contract, the shape D17 used for the civil-time fields, with both
     `extra="forbid"` pydantic mirrors moved alongside it.
   - It adds no new promise: DP already rejected duplicate discriminators within a chunk
     (`stagegraph/executor.py:396-401`), so this only makes an enforced invariant readable.
   - `record_id` is unchanged and pinned by test. The value was always an id input, and an empty
     discriminator reproduces the two-component v0 id byte-for-byte, so nothing re-keyed.
   - The day-log's one-dialect rule keys on `(chunk_id, content.kind, discriminator)` and is
     covered by unit tests plus the live seam check.
   - **Chosen over the cheaper alternative** — proving `(chunk_id, kind, t_start)` unique per
     dialect, because that is a proof that expires silently the day a stage emits two records at
     one timestamp, and D19's prototype posture makes an additive contract edit cheap.
   - *Original text:* the one-dialect rule needs to group by `(chunk_id, content.kind,
     discriminator)`, but the discriminator is today folded into the `record_id` hash and exists
     as no independent field (`../data-processing/app/pipeline.py:33-46`).
   - The build must either **(a)** surface it as an additive-optional C2 field — a schema edit, so
     ARCHITECTURE first, then the schema, then *both* pydantic mirrors, which are `extra="forbid"`
     on DP *and* storage and will reject it otherwise (the exact trap D17 hit), or *(b)* prove
     `(chunk_id, kind, t_start)` unique per dialect and key on that.
   - Do not start the materializer before this is chosen.
8. **Double exposure across a dialect bump (accepted, tracked).** Because a reprocessed record
   re-enters a later window, the same lived moment can be trained twice.
   - Suppressing already-rendered chunks would stop the double exposure, but it would equally stop
     the *correction* from ever training, and we bump precisely because the old dialect was worse.
     So **training the correction wins**. Revisit only if measured over-weighting appears.
9. **Day-log and reservoir deletion cascade** (opened by D18; still open, now concrete).
   - Both are second copies of user content, and both now **exist** as real stores — `day_logs` and
     the reservoir are tables and directories in a live database as of the 2026-07-27 cutover, not
     future work. So a retraction that clears `/context` and leaves them standing has deleted
     nothing.
   - Sharpened by the cutover: **E-2's `DELETE /context/records` is still unbuilt**, which the
     fleet bring-up hit directly — clearing verification rows required a full re-wipe, because no
     retraction primitive exists.
   - See M5, which this widens. Design owed: does a delete cascade synchronously, or mark day-logs
     stale for re-materialization — the mechanism `home_tz` correction already uses?

Research:

5. **Deletion vs weights.** A time-slice delete of records already trained into an adapter is not
   executable by storage alone. The v0 default is a full retrain from retained records; the final
   policy is continuum × platform's open question (ARCHITECTURE §Ownership splits). Storage's part
   stays record-level.

*Resolved 2026-07-08:* the training-window read is now **C10** (we produce it; see the contract
table — watermark semantics remain its design work); the semantic/recency index is now **C11** and
lives here; a full-user delete cascades `/raw`, because the blobs are our store and our primitive.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Breach of life-stream data | product-ending trust loss | encryption at rest everywhere; hard per-user isolation; fail-closed access tests (M4); least-privilege creds |
| Time-index defects (skew, tz, ordering) | training windows and recall queries silently wrong | UTC-only storage; `ingest_time` audit column; C2 validation rejects non-monotonic/absurd timestamps; a cross-device ordering test in M1 |
| C6 sits on the request path | adds latency to every user turn | cached resolution; an explicit budget agreed with inference; fallback to base model if unreachable |
| Incomplete deletion (backups, raw blobs, adapters) | right-to-be-forgotten violated | the manifest enumerates every store; backup expiry policy; per-user LoRA keeps adapter delete clean |
| Unbounded stream growth | cost blowup + degraded queries | metadata/bulk split (DB vs GCS) from M0; retention hooks from day one; per-user growth tracked |
| Upstream schema churn (pipeline versions) | readers break on old records | `pipeline_version` first-class in schema; additive-only migrations; changes route through ARCHITECTURE |

Life-stream data is the most sensitive data a user has, which is why the first row's impact is
stated as product-ending rather than severe. Platform's proof-of-deletion (its M2) verifies the
fourth row end to end.

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
- Outside: vLLM multi-LoRA serving loads adapter weights from local paths — informs OQ2 (artifact
  placement near the serving node).

## How this charter got here

- **2026-07-27 — the D18 scope expansion is `built`.**
  - **Was** — all four expansion rows were ratified and not built, and this charter's status line
    said so.
  - **Changed** — the build landed (`a5a48fb`; 310 tests), and day-log byte-identity against
    continuum was proven over two window origins.
  - **Now** — day-log materialization, the training-window ledger, the recipe registry, reservoir
    custody and the per-user profile all run in code.
  - **Payoff** — the charter stopped describing an intention in the voice of a build.
- **2026-07-26 — D18 put four data jobs here.**
  - **Was** — storage owned three stores, and continuum did its own data work around training.
  - **Changed** — the founders' storage/C10 board ratified the expansion, minted C12, C13 and C14,
    and evolved C10 to a day-log fetch over a `[last_trained_t, now−δ)` ingest-time watermark
    window.
  - **Now** — the In+ rows above, each with its own card.
  - **Payoff** — continuum stays a lean training engine, and nothing here shipped until it was
    built and the day-log's byte-equality was proven.
- **2026-07-26 — D17 added the profile row and corrected §The time index.**
  - **Was** — §The time index promised a per-record timezone this service never stored.
  - **Changed** — D17 built the timezone split end to end and added the per-user profile row.
  - **Now** — the promise and the schema agree.
  - **Payoff** — the gap was closed rather than the promise withdrawn.
