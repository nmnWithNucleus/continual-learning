# Storage Service — Charter

> The durable data layer for Nucleus v0: the `/raw`, `/context`, and `/sessions` stores and the
> per-user model directory. Stable doc — working state lives in [HANDOFF.md](HANDOFF.md);
> system-wide architecture + contracts in [../../ARCHITECTURE.md](../../ARCHITECTURE.md).

> ### ⚠️ STAGE: PROTOTYPE (pre-dev, pre-production) — D19, 2026-07-27
> This charter is written in a production voice. **It is aspirational, not a commitment.** We are
> building one end-to-end product that genuinely works, as fast as we can honestly get there.
> **Licensed:** re-cutting contracts rather than versioning them (*"v0 frozen" means stable enough
> to build against today, not immutable*); wiping and re-collecting stored data rather than
> migrating it; deferring durability work with the reason written down.
> **Not licensed:** skipping [ORG.md](../../ORG.md)'s contract-edit order, leaving a decision
> unrecorded, silent breakage, or calling a thing BUILT when it is only DECIDED.
> Full posture + what changes at dev/prod: [ARCHITECTURE.md](../../ARCHITECTURE.md) §Stage.


**Status:** chartered · **Last updated:** 2026-07-26 (**scope expansion RATIFIED — D18**,
founders' storage/C10 board: day-log materialization + recipe registry + reservoir custody +
per-user profile are now in scope, with contract IDs **C12/C13/C14** minted and **C10 evolved** to
a day-log fetch over a `[last_trained_t, now−δ)` **ingest-time watermark window**. All four rows are
**DECIDED, NOT BUILT** — nothing here ships until it is built and the day-log's byte-equality is
proven. *Earlier 2026-07-26:* **D17** added the profile row and corrected § The time index, which
had promised a per-record timezone this service never stored.)

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

> **Expansion RATIFIED 2026-07-26 (D18) — the "data jobs" for the learn loop. DECIDED, NOT BUILT.**
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

| | Item | Notes / owning sibling |
|---|---|---|
| In | `/raw` store | raw capture blobs; recording writes via ingest, the C1 envelope carries the ref; custody split in ARCHITECTURE §Ownership splits |
| In+ | **Day-log materialization** *(RATIFIED D18 — decided, not built)* | scheduled job: C2 records → segment/block day-log (+ anchored block text), a derived view over `/context`; served to continuum via the evolved **C10**, random-access by `(user_id, window_id)`. Recipe-versioned format (`daylog_format_version` + `recipe_id` on the body). **We inherit `daylog.py`'s `build_daylog` + `_render_block`** (the *product* renderer over C2 records) — **not** continuum's parity-locked `Profile.render_block`, which is recipe-coupled and stays with the amplifier. Two rule changes come with the watermark window: membership is by **`ingest_time`**, and segment buckets sit on a **global epoch grid** rather than window-relative (`daylog.py:74`), which is required once membership is on the ingest axis and also makes a bucket stable across re-materialization. Also inherits the **one-dialect-per-record** rule (latest `ingest_time` wins per `(chunk_id, content.kind, discriminator)`) — see § Open questions for the one named sub-item the builder must close first |
| In+ | **Training-window ledger + the `window_id` minter** *(RATIFIED D18 — decided, not built)* | the per-user `last_trained_t` watermark and the durable window rows (`window_id`, `t_start`, `t_end`, state). `POST /training/windows` is an **idempotent get-or-create** of the user's open window; bounds are **immutable once opened** (this is what preserves continuum's crash-safe journal replay — recomputing `now` on a retry would mint a fresh id and force a full re-train, a second C5 entry and a second reservoir admission). `window_id` is minted in **exactly one place**, format `w<YYYYMMDD>T<HHMMSS>Z`, opaque to every consumer. Storage is the only service that can own this: the watermark is a fact about *our* ingest clock |
| In+ | **Recipe registry** *(RATIFIED D18 → **C13** — decided, not built)* | versioned recipe/config hosting; fetch API for continuum (consolidation recipe) + inference (serving-side knobs). `recipe_id` == filename stem, **global and versioned — never per-user**. The **gate policy is a separate artifact with its own id**: only the *training recipe* may enter a cycle stage key, so changing a publish threshold must not fork `recipe_id` or invalidate hours of GPU cache. Reference implementation to lift: `../continuum/app/clients/registry.py` |
| In+ | **Reservoir custody** *(RATIFIED D18 → **C14** — decided, not built)* | per-user store of amplified corpora (continuum writes via API); **audit/provenance — replay re-reads prior day-logs via C10, not this**. Append-only, keyed `(user_id, window_id, recipe_id)`, content-hashed. Guards the one invariant of the data design: **amplified/synthetic text never lands in `/context`** — same storage discipline, different namespace. Deletion here is a deliberate privacy act, never housekeeping, and it is **in M5's cascade** |
| In+ | **Per-user profile** *(RATIFIED D18 → **C12**, schema `../../contracts/c12_user_profile.v0.json` — decided, not built)* | one durable row per user for **policy** values that are neither sensor data nor recipe config. First v0 field: **`home_tz`** (IANA zone), whose *only* jobs are **(a) scheduling** — deciding when this user's nightly consolidation fires, a question asked before any of that night's records exist — and **(b) fallback** when a record carries no `device_tz`. Seeded from the most recent device-reported zone, user-overridable. It is NOT the pipeline's time semantics (that is per-record `device_tz`) and NOT part of the C10 range arithmetic. Cannot ride the recipe registry: `recipe_id` is global and versioned (`recipe_id` == filename stem), so a per-user value there would fork `recipe_id` per user. **D18 additions:** it is a **profile, not a settings blob** — it holds values *the system reads to decide its own behaviour* for this user (scheduling, fallbacks, policy) and never user-facing identity or presentation, which are input's; that fence is what makes the general noun safe. A missing profile is a **404** and a user with no `home_tz` is **not schedulable** — an operational alert, never a silent skip, because D17 abolished default timezones. **Storage never writes it on its own** (corrected 2026-07-27 — D18's first draft had it auto-seed from the first device-reported `device_tz`): `home_tz` is **declared, not inferred**. The user sets it; the device's zone may be *suggested* in a UI, never stored as an answer. Consequence worth stating because it is the whole point: **`home_tz` does not move when the user travels** — a week in Tokyo changes every record's `device_tz` and changes nothing here, so the night boundary stays put instead of jumping 9 h and producing a 15 h night followed by a 33 h one. In the prototype the operator sets it directly; there is no auto-seed to fall back on, so **a user with no `home_tz` simply does not consolidate** — visible, not silent. Second field named but deliberately **not** minted until it has a consumer (the E-5 precedent): a per-user `boundary_local_time` override, today a *global* recipe knob that cannot express a night-shift worker **Physically this is ONE TABLE keyed by `user_id`** (CTO, 2026-07-27) — the day-log renderer resolves `home_tz` by looking the row up, exactly as every other store already carries `user_id` as a column. Two things to hold while building it: **(a) identity is not policy** — this row holds only values the *system* reads to decide its own behaviour; an account name, email or avatar belongs to a different table behind a different contract, and keeping C12 scoped to policy is what stops inference/input reading identity out of a storage *policy* read. **(b) Do NOT make `user_id` a validated foreign key yet** — today `user_id` is a bare string with no owning table anywhere in the system, so a validating FK would mean a user must be provisioned before capture works, which blocks the prototype's simplest path. The row is one that may or may not exist, and its absence is a 404, never a rejected write elsewhere |
| In | `/context` store | processed life-stream records; storing side of C2 |
| In | `/sessions` store | conversations → sessions → turns, incl. full mentor + tool traces; storing side of C4 |
| In | Model directory | per-user adapter registry behind C5/C6. **Hosting DEFERRED (D19, 2026-07-27)** — the consumer is inference (C6), which is not being built; continuum's local `entries.jsonl` carries the lifecycle, so this is not on the learn-loop critical path. **C5's shape, as continuum builds it today and NOT yet frozen** (the freeze needs inference at the table; describing it is not pinning it) — nine fields: `contract:"C5"`, `user_id`, `adapter_version`, `adapter_dir`, `base_model_hash`, `training_window`, `recipe_id`, `eval_report`, `status` ∈ **`active` \| `gate_failed` \| `rolled_back`** (`../continuum/app/publish.py:83-114`). **Three of those are load-bearing for US at freeze time, not just for the docs** — today storage's `model_directory` table is the *trivial C6 row* (`user_id, model_id, adapter, adapter_path` — `app/db.py:59-63`), with no entries log and no status column at all, so hosting C5 is a build, not a transport swap, and the short field list this row used to carry is precisely what an implementer would have built to: **(1) `status` is a THREE-value enum** — a two-value `active/rolled_back` column has nowhere to put `record_gate_failure()`, and the audit trail for blocked candidates would be dropped silently at the swap; **(2) `gate_failed` rows carry NULL `adapter_dir` and NULL `base_model_hash`** — so those columns cannot be `NOT NULL`, or the schema rejects exactly the rows that matter most; **(3) C6 eligibility is a LOG REPLAY, not "latest row wins"** — `active` pushes, `rolled_back` pops the matching top, and **`gate_failed` does neither** (`publish.py:33-44`); a directory that resolved the newest entry would serve a gate-failed candidate, which is the ungated swap the gate exists to prevent |
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
| C10 | storage → continuum | serve the training-window read. **EVOLVED + RATIFIED (D18) — decided, not built:** a **day-log fetch**, random-access by `(user_id, window_id)`, plus window enumeration and the window-ledger open/close writes; over a `[last_trained_t, now−δ)` **ingest-time watermark window**. Watermark semantics are **decided** (see § Open questions, formerly the open OQ) and pinned in ARCHITECTURE's C10 row + its detail block. **The raw range read `GET /context/records?user_id=&from=&to=` is NOT retired** — it remains first-class (D12's beta training feed, debugging, C11-adjacent); C10-evolved is additive |
| C12 | storage → continuum *(later inference/input)* | serve the **per-user profile** read (`home_tz`). Read side is C12; the write surface is ours and prose-pinned (D11's `/raw` precedent) until input ships a settings consumer |
| C13 | storage → continuum + inference | host + serve the **recipe registry** (versioned recipes; the separately-versioned gate policy) |
| C14 | continuum ↔ storage | host the **reservoir**: accept amplified-corpus writes, serve the ledger |
| C11 | storage → input (QueryBuilder) | serve the recent-context read: recency/semantic retrieval over `/context` + `/sessions`; the index lives here, QueryBuilder decides what enters the UserPrompt |

### The time index (the load-bearing decision)
- Every record carries device wall-clock `t_start`/`t_end` (from C2/C4) **and** a
  storage-assigned `ingest_time`. Wall-clock is the query axis; ingest time is the audit axis.
- **All timestamps stored UTC; the capturing device's local timezone stored alongside** (`device_tz`,
  IANA + `device_utc_offset_minutes`, carried on C2 `source{}`), so "what happened Tuesday" resolves
  in the user's *actual* local time — including for a day they spent in another zone. **Built
  2026-07-26 (D17)**; this line had promised it since 2026-07-08 while `context_records` had no such
  column, and the gap is now closed rather than the promise withdrawn.
- **UTC is canonical and is the only query axis.** `t_start` orders the timeline and answers every
  range read (`GET /context/records?from=&to=` needs no timezone at all — C10 is a duration query).
  The zone is *context beside* the instant, never a replacement for it, and never an index.
- **Two timezone concepts, deliberately distinct** — see
  [ARCHITECTURE §Ownership splits](../../ARCHITECTURE.md) → *User timezone*: the per-record
  `device_tz` is the **fact** (where the user was; owned by the device, correct under travel), while
  the per-user profile `home_tz` is the **policy** (when this user's night is; owned by us, used for
  **scheduling the nightly cycle** and as the fallback when a record carries no zone).
- **Two rules that stay rules:** never persist a derived local wall-clock string (`device_local_time`
  is recoverable from instant + zone; storing it makes two sources of truth that will disagree), and
  never accept a timezone *abbreviation* — `PST`/`MST` are ambiguous and DST-sensitive, IANA ids only.
- A user's streams overlap (body cam + computer at the same moment): one timeline per user,
  indexed (user_id, t_start), with modality/device as filter columns — not parallel timelines.
- **`t_start` and `ingest_time` are TWO AXES and D18 assigns each a job** — conflating them is the
  same class of error D17 fixed for timezones. **Event time (`t_start`) is the CONTENT axis:**
  recall queries, day-log bucketing, block anchors, and **deletion ranges** ("delete last Tuesday"
  is a claim about a lived period). **Ingest time is the COMPLETENESS axis:** it is the only axis on
  which "everything I had" is a guarantee, so it is what continuum's training window watermarks on.
  Both are indexed.
- Continuum's nightly window (C10) is `[last_trained_t, now−δ)` per user **on the `ingest_time`
  axis** (D18) — a single index-range scan must satisfy it at v0 scale (handful of pilot users).
  Three properties follow and they are the reason for the choice: **storage needs no timezone to
  serve C10 at all**; a missed or gate-failed night is **absorbed** into the next window rather than
  lost; and **"late data" cannot exist** — `ingest_time` is assigned by us at write, so a record can
  never land below an already-closed boundary. `δ` (default 60 s) exists for exactly one reason: an
  in-flight write racing the boundary could otherwise be assigned an `ingest_time` below `t_end` yet
  commit after materialization read the range. It is watermark lag, not slack.
- **Note the deliberate asymmetry, and do not "fix" it:** training windows are ingest-time,
  retraction ranges are event-time. They answer different questions, and making them agree would
  break one of them.

## v0 deliverables

| M | Deliverable | Exit criterion |
|---|---|---|
| M0 | Foundations: schemas for all three stores; tech pick (metadata DB + GCS blob layout); time + isolation conventions | DDL applied on a dev instance; C2/C4/C5 field mapping reviewed with peer services; decisions recorded here + ARCHITECTURE.md |
| M1 | `/context` live: C2 write path + time-ranged read API | a full pilot-day of processed stream lands; a time-window query returns records correctly ordered across devices; re-ingest is idempotent |
| M2 | `/sessions` live: C4 write path + session/turn reads | inference persists a complete turn incl. mentor + tool traces; a conversation replays exactly from the store |
| M3 | Model directory live: C5 registration + C6 resolution | continuum registers an adapter; inference resolves the active adapter within budget; rollback flips resolution atomically |
| M4 | Security baseline: encryption at rest everywhere + isolation test suite | cross-user access attempts fail closed under test; encryption verified on DB, blobs, and backups |
| M5 | Retention + deletion primitives: full-user delete + time-slice delete **+ the kind-aware retraction (E-2)**, all cascading to the derived stores | full-user delete purges `/raw`, `/context`, `/sessions`, directory entries + adapter artifacts and schedules backup expiry; deletion manifest is auditable; platform's orchestration + proof-of-deletion (its M2) calls these primitives — we don't own the end-to-end pipeline. **WIDENED BY D18:** once we materialize day-logs and host the reservoir, **both are second copies of user content and both must be in every deletion's cascade** — a retraction that clears `/context` and leaves a day-log standing has deleted nothing, and this is a right-to-be-forgotten defect, not a tidiness one. **E-2's shape** (board-reviewed 2026-07-26): `DELETE /context/records?user_id=&from=&to=&kind=&pipeline_version=`, `user_id` required, `from`/`to` half-open on **`t_start`** (event time — a retraction is about a lived period), `kind` + `pipeline_version` optional filters, returns an **auditable manifest** of counts by `kind` × `pipeline_version`, with `dry_run=true` returning the manifest without deleting. It must **re-materialize or invalidate every affected day-log** |
| M6 | Backup/restore | scheduled backups running; point-in-time restore drill passes on a dev instance |
| M7 | Metrics + dashboard | service `/metrics` scraped by the shared Prometheus; dashboard (`dashboards/*.json`) shows request rate/latency/errors + DB/query metrics (query latency, rows read/written, DB/file size, pool health); Platform provisions it (§Observability) |
| M8 | **C12 profile + C13 recipe registry + C14 reservoir** (D18) | profile read serves `home_tz` with a 404 on absence, and **declared-not-inferred** semantics (storage never writes `home_tz` on its own initiative), validated against `../../contracts/c12_user_profile.v0.json` with tzdata resolution on write; registry serves recipes + gate policy by id; reservoir accepts append-only corpus writes and serves its ledger. **C12 lands first — day-log materialization depends on it** |
| M9 | **C10 evolved: day-log materialization + the training-window ledger** (D18) | storage mints windows idempotently over the `ingest_time` watermark, materializes day-logs, and serves fetch-by-`(user, window_id)` + enumeration + close. **Exit bar is a differential proof, not a claim** — `scripts/daylog_parity_diff.py`, committed with its output. **Bar NARROWED 2026-07-27 (D20) after the first run failed on it**, because the bar as first written contradicted D18's own materialization rule and no code could satisfy both: continuum's `seg_id` *was* `floor((t − window_start)/segment_seconds)` over an **event-time** window origin, while D18 deletes the window origin from storage's grid and puts storage's window on the **ingest** axis, where a backlog record yields a *negative* index. **Bar WIDENED 2026-07-27 (F4), in the other direction:** D20 had narrowed *what* is compared, and F4 found that the *one window* it was compared over was a grid-**aligned** origin — which no window this service mints has (`[watermark, now−δ)` is second-granular), and which was the only reason continuum's then window-relative bucket grid agreed with storage's global one. Measured, not argued: shifting that fixture's origin 1–9 s failed tier A at all nine offsets, block text included, and at +3 s the segment count and per-block membership too. Fixed in continuum's **reference** renderer (`app/daylog.py` — not `morpheus/profiles/`, which is the research-golden surface and untouched): it now buckets on the same global epoch grid and labels `seg_id` by the same ordinal rule. The script runs its whole origin-dependent bar over an aligned **and** a misaligned origin, `A8` asserts origin-independence directly, and `P3` asserts of each origin that it is what it claims to be so the misaligned case cannot be repaired by aligning it. The narrowed bar, and why each half is where it is: **(a) BYTE-IDENTICAL, non-negotiable — block `text`, block ordering, `block_id`, `anchors`, block `quality`, and segment payloads (bounds, caption/asr/ocr, `tz`, `quality`).** This is the artifact that trains the model. **(b) PROVEN-EQUIVALENT, not identical — `seg_id`:** the relabelling must be an **order-preserving bijection with per-block membership preserved**, which the script measures rather than assumes. `seg_id` is written to `segments.jsonl` and **read by nothing** — the trainer and amplifier consume `blocks.jsonl` via `load_blocks` (block text + anchors); the only reader anywhere is `phase3_daylog.py:88`, counting `len(b.seg_ids)` for a histogram, which is invariant under relabelling. **(c) EXCLUDED deliberately — `content_fingerprint`:** it hashes `seg_id`, and it is a cache key compared only to *itself* across runs. It **should** change when the renderer changes; forcing it to match would make the cache lie. It changes once at cutover, that night re-runs, and that is correct. Continuum's local path is **not deleted until the narrowed diff is green** |

## Retention — the knob ships, the policy does not (D19, 2026-07-27)

**Decision: `keep_forever` for every store, prototype-wide.** Nothing is deleted on a schedule; the
only deletions are the explicit privacy primitives in M5. The founders' instinct — that this belongs
in *service config*, not in code — is right, and this section pins the shape so the dev/prod
conversation is a config edit rather than an excavation.

**The design, and why each property is load-bearing:**

| Property | Why it is not optional |
|---|---|
| Retention is **data, not code** — a versioned document storage reads at startup and on change | Changing what we keep must never require a deploy. It is a *policy*, and we already learned this shape once: the gate policy was split from the training recipe (2026-07-24) precisely so a threshold change could not fork an artifact id |
| **Versioned + logged**, with the active version reported on `/metrics` | The question a privacy review actually asks is *"what policy was in force when this record was deleted?"* — unanswerable if the rule was a constant in a source file, and unanswerable after the fact if it was an unversioned config edit |
| **Per-store**, not global — `/raw`, `/context`, day-log, reservoir, `/sessions`, adapters | These genuinely differ. Raw A/V is the expensive one and the least re-derivable-from; day-logs are cheap, derived, and the thing replay needs forever; the reservoir is append-only by charter. A single global TTL would be wrong for all six |
| **Rules mark ELIGIBILITY; a separate explicit sweep acts and writes a manifest** | This is the important one. A retention config that deletes *implicitly* means a typo in a config file destroys user data with no review step and no record. Eligibility + explicit sweep means the blast radius of a bad edit is a wrong number in a report |
| **Deletion is never the mechanism for correctness** | D18 established this in the specific case (the one-dialect materialization rule fixes the WS-VC double-count; E-2 does not). It generalises: if we ever find ourselves deleting records to make training come out right, the bug is upstream |

**v0 concretely:** one versioned `retention.v0` document, every store `keep_forever`, read and
surfaced on `/metrics`, **no sweeper implemented**. That is a few lines and it is the cheapest
possible way to buy the future decision. The reservoir's own charter line already anticipates the
posture — *deletion there is a deliberate privacy act, never housekeeping* — and this generalises it
to every store.

**What changes at dev/prod** (tracked, not forgotten): choose real per-store values; build the
sweeper + its manifest; decide whether retention tiers by consent state; and reconcile with the
research design-of-record's stance (raw A/V ≤72 h · day-logs forever · 14-night hard-delete replay),
which continuum's canvas has flagged since 2026-07-22 as *"a PRODUCT decision to take explicitly"*
and which D19 explicitly defers rather than silently adopts.

## Open questions

> **OQ numbers are stable identifiers and are never renumbered** — resolved ones are struck
> through in place (OQ3, OQ6), so a cross-service reference like "storage OQ3" keeps meaning what
> it meant. The counter runs across both subsections, which is why Engineering skips 5.

Engineering:
1. **Storage tech.** Postgres (records + directory) + GCS (bulk payloads, adapter artifacts) is
   the lean default for a handful of users — confirm with platform at M0, incl. where the DB runs.
   **ANSWERED FOR THE PROTOTYPE (D19, 2026-07-27): stay local** — SQLite + filesystem, including for
   the four new stores (day-log, window ledger, reservoir, profile). **The target is unchanged and
   is option (c): metadata in Postgres, day-logs + corpora in GCS**, and it is near the top of the
   list the moment we leave prototype — day-logs-forever is the first store that grows without
   bound. **What keeps that migration cheap is a rule, not foresight:** every new store goes behind
   a **narrow interface** in storage from day one, so the swap is a backend change rather than a
   rewrite. Continuum already proved the shape on the client side (`app/clients/` — local today,
   HTTP-to-storage later, the cycle unchanged); storage owes the same on the server side. Two
   existing properties help and must be preserved: `blob_ref` is already **opaque + storage-owned**,
   and `record_json` is served **byte-verbatim**, so neither leaks the substrate to a caller.
2. **Adapter artifact placement.** The directory holds adapter artifacts + metadata only —
   BWM (base-model) weights custody is inference's (ARCHITECTURE §Ownership splits). Adapter
   weight files must sit where vLLM can hot-swap fast (GCS vs NFS vs node-local cache) — split
   with inference + platform.
3. ~~**Clock skew.** Does data-processing normalize device clocks before C2, or does storage keep
   raw + corrected times?~~ **RESOLVED (D17, 2026-07-26) — the lean was right, and is now built.**
   Storage stores **exactly what C2 carries, verbatim, plus its own `ingest_time`**; it corrects
   nothing. Data-processing likewise normalizes nothing — it is a pure passthrough for
   `device_tz` / `device_utc_offset_minutes` / `device_location`. What makes this safe rather than
   naive is that the envelope now carries its own audit trail: `device_clock` (`synced|unsynced`)
   says whether the stamp is NTP-disciplined, `device_utc_offset_minutes` witnesses what the device
   believed, and `ingest_time` is an independent server-side clock — so skew is **detectable after
   the fact from stored data** instead of being silently baked in by an upstream correction. If a
   corrected time is ever needed it lands as a new additive field beside the raw one; the raw device
   stamp is never overwritten.
4. **ID minting.** Session/turn ids originate in input (C3) — does storage enforce referential
   integrity on C4 writes, or trust writers?
6. ~~**C10 watermark semantics.** Late-arriving records, reprocessed records, `pipeline_version`
   bumps — what advances `last_trained_t`, and what happens to a record landing with a `t_start`
   inside an already-trained window?~~ **RESOLVED (D18, 2026-07-26)** — this was the session's
   substantive design work. Full statement in [ARCHITECTURE.md](../../ARCHITECTURE.md) § Contracts
   → *C10 evolved*; the four rules:
   - **The window watermarks on `ingest_time`, not event time.** This dissolves the question rather
     than answering it: a record landing with a `t_start` inside an already-trained window is simply
     a record whose `ingest_time` is *now*, so it joins the current window and trains — rendered in
     a block anchored to its own real local date. **Late data cannot be lost because on an
     ingest-time watermark late data does not exist.** Content stays event-time-correct because
     day-log blocks are formed by temporal adjacency and carry their own anchors, so a week-old
     backlog forms its own blocks rather than corrupting today's.
   - **`last_trained_t` advances if and only if the cycle PUBLISHES** *(refined 2026-07-27 — the
     first draft also advanced on `skipped_no_data`)*. Gate failure, freeze, crash, no data and
     **too little** data all leave it, so the next window is a strict **superset**. This is the
     design-of-record's *failed-day merge* obtained structurally rather than by bookkeeping (it
     demotes continuum's `_UserState.debt` to reporting), and it makes the min-data floor nearly
     free: a below-floor night just doesn't advance, so material accumulates until a run is worth
     it. Named cost: an inactive user's open window grows and is re-scanned nightly — correct, and
     cheap at v0 scale.
   - **Reprocessed records: one dialect per record, latest `ingest_time` wins**, keyed
     `(chunk_id, content.kind, within-chunk discriminator)`. Keyed on `ingest_time` because
     `pipeline_version` is a *composed* string and therefore not orderable; keyed on `content.kind`
     because Phase-3 proved captions and transcripts can share one `pipeline_version`, so a
     kind-blind rule drops transcripts to drop captions. **This is what actually fixes the
     re-consolidation double-count** (`daylog.py` filters on neither field today) and it is why
     **E-2 is no longer a correctness blocker for the WS-VC cutover**.
   - **A `pipeline_version` bump is a forward-only correction.** It improves future training; it
     does not repair past weights, which on an append-only weight chain is irreducible. The remedy
     for a dialect bad enough to need repair is a deliberate **rebuild from base over retained
     history** — named as the escape hatch, not built. **Accepted, named cost:** the same lived
     moment can train twice in two dialects (OQ8 below).
7. **The within-chunk discriminator is not readable from C2 — BLOCKING sub-item for the build
   slice.** The one-dialect rule needs to group by `(chunk_id, content.kind, discriminator)`, but
   the discriminator is today folded into the `record_id` hash and exists as no independent field
   (`../data-processing/app/pipeline.py:33-46`). The build must either **(a)** surface it as an
   additive-optional C2 field — a frozen-schema edit, so ARCHITECTURE first, then the schema, then
   **both** pydantic mirrors, which are `extra="forbid"` on DP *and* storage and will reject it
   otherwise (the exact trap D17 hit) — or **(b)** prove `(chunk_id, kind, t_start)` unique per
   dialect and key on that. Do not start the materializer before this is chosen.
8. **Double exposure across a dialect bump (accepted, tracked).** Because a reprocessed record
   re-enters a later window, the same lived moment can be trained twice. Suppressing
   already-rendered chunks would stop the double exposure — but it would equally stop the
   *correction* from ever training, and we bump precisely because the old dialect was worse, so
   **training the correction wins**. Revisit only if measured over-weighting appears.
9. **Day-log + reservoir deletion cascade (new, opened by D18).** See M5 — scoped, not designed.

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
