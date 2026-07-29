# Continuum Service — Charter

> The nightly magic loop: distill each user's new life-stream + sessions into their personal
> model's weights, gated by evals, published through the model directory. Stable doc; working
> state lives in [HANDOFF.md](HANDOFF.md); system-wide architecture + contracts in
> [../../ARCHITECTURE.md](../../ARCHITECTURE.md).

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

**Status:** chartered · kicked off · learn-loop integration proven end to end, M0 met and the
Phase-3 data-processing dogfood returned *pipeline sound* · **Last updated:** 2026-07-25.
Lineage: [§How this charter got here](#how-this-charter-got-here).

## Mission

Own the periodic (nightly-ish) per-user fine-tuning loop that turns the product's core promise —
"infinite context" + true personalization — into weights. Each cycle: curate a training mixture
from the user's new `/context` and `/sessions` records since the last cycle (including mentor
traces, distilling mentor competence into the personal model), blend in an anti-forgetting replay
mixture, run a LoRA job over the base world model — v0 adapts **all layers of the language model,
vision towers excluded**; "all layers" everywhere remains the standing intent, and §Scope names the
gap — gate the candidate adapter on personal-recall *and* general-capability evals, and publish (or
roll back) through the model directory. The service is research-heavy by design: continual-learning
stability, recency vs long-term retention, self-distillation, and the LoRA → MoE-experts-per-user
scaling path live here.

## Scope — v0

> **Slimming (2026-07-23; RATIFIED 2026-07-26 — D18, which put the counterpart data jobs in
> storage's scope with contract IDs):** continuum is now the **Morpheus** nightly-consolidation
> engine — a lean **5-verb loop: fetch recipe · fetch day-log · amplify · finetune · gate · publish.**
> Day-log materialization, the recipe registry, and the reservoir move to **storage** (rows below +
> Out-of-scope). We *consume* those; we own the recipe-coupled training transforms.

| In scope | Notes | Card |
|---|---|---|
| Cycle data curation | fetch the day-log for the window via C10; mentor traces (C4) are first-class distillation targets | — |
| Anti-forgetting replay mixture | capability-aligned replay (text + vision) trained alongside the personal data; ratio and LR schedule are the levers | — |
| Amplification (train-time) | the nightly corpus build: styled retellings and deny-then-correct negatives, per the pinned recipe | [↓](#amplification-train-time) |
| Reservoir *write* | each night's amplified corpus goes to the storage-owned reservoir via C14, for audit and provenance | [↓](#reservoir-write) |
| Block rendering — the half that stays | `Profile.render_block` is recipe-coupled and stays here with the amplifier | [↓](#block-rendering--the-half-that-stays) |
| Day-log *construction* — left | `app/daylog.py` and `app/window.py`'s local-date arithmetic moved to storage | [↓](#day-log-construction--the-half-that-left) |
| LoRA training jobs | per-user LoRA over the language-model projection linears, vision towers excluded. Runs on the shared SLURM partition | [↓](#lora-training-jobs) |
| Pre-publish eval gates | personal-recall suite (does it know yesterday?) plus general-capability forgetting suite; no green, no publish | — |
| Publish / rollback | C5 adapter version entries in the model directory; one-command rollback to the previous active version | — |
| Per-user scheduling | cadence orchestration across pilot users; retries, idempotency, cost accounting | — |
| Deletion support primitive | the continuum leg platform's deletion orchestrator calls; the adapter *artifact* purge is storage's | [↓](#deletion-support-primitive) |
| Continual-learning research | stability, retention, self-distillation, recursive-training drift, the MoE-experts-per-user path | — |
| Observability | expose `/metrics` (batch and job counters, off the request path) and own a Grafana dashboard JSON | [↓](#observability) |

### Amplification (train-time)

**In one line.** The nightly corpus build: styled retellings and deny-then-correct negatives
generated *from* the day's faithful records, per the pinned recipe.

**Rules**

- The output is a training artifact. **Amplified or synthetic text never lands in `/context`** —
  grounding and paging read the faithful record only.

### Reservoir *write*
> `built` 2026-07-27 · [D18](../../DECISIONS.md) → C14

**In one line.** We write each night's amplified corpus to the storage-owned reservoir via API,
for audit and provenance.

**Rules**

- Deletion there is a privacy act, never housekeeping.

**Why it's this way**

- The reservoir is **not** on the replay hot path. Replay itself re-fetches prior **day-logs**,
  the raw source being a validated tie.

### Block rendering — the half that stays
> `built` · [D18](../../DECISIONS.md) · parity-locked against `b3c58e1`

**In one line.** `Profile.render_block` is recipe-coupled and stays here with the amplifier, while
the product renderer over C2 records left for storage.

**Rules**

- `Profile.render_block` (`app/morpheus/profiles/`) is the surface
  `tests/parity/test_render_block.py` locks **byte-identical** against the research line
  (`b3c58e1`, 1427/1427 blocks).
- The **trainer-seam file materialization** also stays: `app/renderer.py` →
  `segments.jsonl` / `blocks.jsonl` / `day.txt`. That is how *our* trainer consumes a day-log it
  fetched.

**Why it's this way**

- `app/morpheus/blocks.py:5-7` drew this boundary before the move was proposed — *"the only
  interface between ingest and consolidation… keeping that boundary narrow is what lets the day-log
  move behind a storage client without any kernel noticing."*

### Day-log *construction* — the half that left
> `built` 2026-07-27 · [D18](../../DECISIONS.md) · `1757efb`

**In one line.** `app/daylog.py` and `app/window.py`'s local-date arithmetic moved to storage.

**Rules**

- What moved: `build_daylog` plus the v0 labeled-lines `_render_block` over C2 records, and the
  local-date window arithmetic.
- `LocalDayLogClient` and the `RecordProvider` seam disappear, exactly as
  `clients/daylog_client.py:14-19` predicted.
- `app/window.py` shrinks to the `Window` value object storage returns.

**Why it's this way**

- **These are a different renderer from the parity surface above, and they have never had a
  research golden.** The research line never materialized the 10 s-segment / 2 min-block schema —
  zero producing code, and a research "block" is one 5-min description — so the move cannot break
  research parity.
- What it must prove instead is a **differential byte-equality** against our own current output.
  See the storage charter's M9 exit bar.

### LoRA training jobs
> `built` · as built at v0, not an architecture commitment

**In one line.** Per-user LoRA over the language-model projection linears — all 36 language-model
layers × 7 projections (`q,k,v,o,gate,up,down`) — with the vision towers deliberately excluded.

**Rules**

- Runs on the shared SLURM partition.
- Parity-proved **252/252 modules = 7 × 36, zero vision-tower**, against the research line's golden
  adapter tensor keys ([handoff/phase-2a-report.md](handoff/phase-2a-report.md):60).

**Why it's this way**

- The rationale is in the code that enforces it (`app/morpheus/train.py:27-32`,
  `lora_target_modules()` at `:89-100`): the day log reaches the model **as text**, so adapting the
  vision stack spends rank on modules that never see the training signal.

**Watch out for**

- **A known open item, not a contradiction.** [ARCHITECTURE §Founding posture](../../ARCHITECTURE.md)
  still records *"LoRA per user, all layers"* as the standing intent, sourced to `start.md` — an
  inherited founding assumption, never a ratified D-number. Both statements are true of different
  things: the intent is the research direction, and this card is the build.
- **Flipping to the towers is cheap** — a module-name filter change in `LM_PROJECTIONS` /
  `_LM_SCOPE` plus a re-parity run, *not* an architecture change. Nothing about writing today's
  behaviour down makes the flip harder.
- **The exclusion's premise is falsifiable and expires on its own.** It holds only while the day
  log reaches the trainer as text, so the day data-processing feeds the trainer pixels, the reason
  is gone and this card must be re-argued.

### Deletion support primitive

**In one line.** The continuum leg that platform's deletion orchestrator calls
([platform M2](../platform/CHARTER.md)).

**Rules**

- Halt the user's scheduled cycles, then retrain from retained records minus deletions — the v0
  default per [§Ownership splits](../../ARCHITECTURE.md).
- The adapter *artifact* purge itself is storage's primitive, not ours.

### Observability
> `designed` · [D9](../../DECISIONS.md)

**In one line.** Expose `/metrics` — batch and job counters, off the request path — and own a
Grafana dashboard JSON (`dashboards/*.json`).

**Rules**

- Emit training-job metrics (job status, GPU during training, step/loss, throughput), eval-gate
  pass/fail rates, cycle cadence, and publish/rollback counts.
- Platform runs the shared Prometheus + Grafana backbone. Shape:
  [§Observability](../../ARCHITECTURE.md).

### Out of scope

| Out of scope | Owner |
|---|---|
| Serving adapters / hot-swap in vLLM (C6 resolution) | Inference Service |
| Serve-time memory harness | Inference Service — see the card below |
| `/context` + `/sessions` storage engine and query APIs | Storage Service |
| **Day-log materialization** (scheduled C2 → segments/blocks + anchored text) | Storage Service — see the card below |
| **Recipe registry** (versioned recipe hosting) | Storage Service, C13 — we fetch, we do not host |
| **Reservoir custody** (amplified-corpus store) | Storage Service, C14 — we write to it, storage owns the store |
| Producing the records we consume | Data Processing Service (C2), Inference Service (C4) |
| Calling mentor models (traces reach us only as stored C4 records) | Inference Service (C7) |
| Model directory hosting/query (we only publish via C5) | Storage Service |
| Pretraining the base world model; its custody and serving | Out of company v0 scope; Inference Service |
| Capture devices, user-facing I/O | Recording / Input / Output Services |

**The serve-time memory harness** — fast-memory (SSM/mneme) runtime and per-user state, the
think-back paging executor, day-log-grounded answering, and memory routing — is inference's, by the
kickoff decision of 2026-07-22, pending founders'-board ratification. **We train and publish the
memory artifacts** (the mneme module, the reader-LoRA, the paging recipe); they execute them.

**Day-log materialization** is storage's, ratified by [D18](../../DECISIONS.md) and `built`
2026-07-27 (`a5a48fb` storage · `1757efb` continuum · `2698b63` data-processing). We fetch the
rendered day-log via the evolved C10, random-access by `(user_id, window_id)`. Note the boundary
precisely: what leaves is `daylog.py`'s *product* renderer over C2 records, while the parity-locked
`Profile.render_block` stays here, because it is recipe-coupled. The recipe registry and reservoir
moved on the same decision and the same commits; from us they are a fetch and a write.

## Position in the system

Upstream: **Storage Service** (we read via C10). Downstream: **model directory → Inference Service**
(we publish, they resolve). We sit entirely off the request path; nothing here is latency-critical.

| Contract | Direction | Our role (payloads defined in [../../ARCHITECTURE.md](../../ARCHITECTURE.md) § Contracts) |
|---|---|---|
| C10 | **consume** | a day-log fetch as of [D18](../../DECISIONS.md); `built` 2026-07-27. See the note below |
| C12 | **consume** | per-user profile read (`home_tz`). It replaces `nightly.py --tz`, so the cycle is no longer told a timezone by its caller |
| C13 | **consume** | recipe registry — the pinned training recipe and the separately-versioned gate policy, by id |
| C14 | **produce** | reservoir writes — each night's amplified corpus, append-only, for audit and provenance |
| C5 | **produce** | adapter version entries. Shape **not pinned** ([D19](../../DECISIONS.md)); see the card below |
| C6 | observe | inference resolves the latest *eligible* adapter per request; our C5 `status` is what makes one eligible |

**On C10.** We ask storage for "the day-log for `(user_id, window_id)`" and get rendered
segment/block rows back. We no longer build it, and we no longer pull raw records to do so. Also
consumed: window **enumeration** — which windows has this user consolidated, today inferred from
the reservoir ledger (`cycle.py:204`) — and the window-ledger open/close calls. The window itself
is storage's `[last_trained_t, now−δ)` ingest-time watermark, so we no longer compute it from a
local date, and `window_for()` / `closed_window_before()` are deleted. The `/sessions`
(mentor-trace) leg of this row is unchanged and remains **unbuilt**.

**On C12.** We use it for **nothing but** the scheduler's fire time. The window arithmetic needs no
zone, and each block's rendered anchor is resolved by storage from the record's own `device_tz`.

**On C13.** Our `LocalRecipeRegistry` is the reference implementation storage lifts.

**On C14.** It is **not** the replay path: replay re-reads prior day-logs via C10.

### C5 — the adapter publish, as built and not pinned
> `built` in code, shape **not pinned** · [D19](../../DECISIONS.md), 2026-07-27

**In one line.** What we write to the model directory when a night finishes — the adapter, its
lineage, and whether it may serve.

**Shape** — nine fields (`publish.py:83-99`): `contract:"C5"`, `user_id`, `adapter_version`,
`adapter_dir`, `base_model_hash`, `training_window`, `recipe_id`, `eval_report`, `status`.

**Rules**

- `status` has **three** values — `active` | `gate_failed` | `rolled_back`.
- A candidate the gate blocks is *recorded* rather than discarded (`record_gate_failure`,
  `publish.py:101-114`): the row is appended for audit and lineage with `adapter_dir` and
  `base_model_hash` NULL, and never becomes eligible.
- **One standing instruction for whoever does pin it: `training_window` must be pinned as an
  opaque token, never as a date**, or the id-parsing D18 just deleted (`Window.local_date`,
  `ReservoirEntry.local_window_date()`) grows straight back.

**Why it's this way**

- **The shape is deliberately not pinned.** C5's only consumer is inference via C6 resolve, and we
  are not building inference yet, so pinning now would cost a session and buy nothing. Continuum's
  local `var_dir/model_directory/entries.jsonl` carries the full lifecycle meanwhile.
- The deferral is *free precisely because C5's shape is unpinned* — D18 changed `training_window`'s
  format, which would otherwise have been a breaking edit.
- The `gate_failed` audit trail is exactly what a reader most needs to know exists, so it belongs
  in the field list and not only in the code.

**Watch out for**

- This card is described so the gap is visible, **not** to pin it. `app/publish.py:3-4` says the
  same in the source: *"C5's v0 shape is NOT pinned yet (needs inference at the table; founders
  ratify)"*.

Future scope (not v0): proactive/coach-mode triggers will involve us jointly with inference —
trigger ownership is tracked as output's proactive open question ([../output/CHARTER.md](../output/CHARTER.md)).

## v0 deliverables

| M | Deliverable | Exit criterion |
|---|---|---|
| M0 | **Recipe lock + Morpheus core** — done 2026-07-24. See the card below | met; a 32B adapter it trained published via C5 and loaded in vLLM (recall 0.267) |
| M1 | **Single-user cycle v1.** Watermarked reader over `/context` + `/sessions` (C10) → mixture builder → SLURM LoRA job → candidate adapter | a nightly cycle produces a candidate adapter from one pilot user's real day, idempotent and resumable across job failure |
| M2 | **Eval gates + publish/rollback.** Personal-recall suite auto-derived from the cycle window; general-capability forgetting suite; C5 publish on green only | a deliberately-degraded candidate is blocked; a green candidate goes live via C5 and resolves via C6; rollback restores the prior version in one command |
| M3 | **Replay v1 + mentor distillation.** Capability-aligned replay mixture in every cycle; loss-masked mentor-trace targets in the personal mix | the forgetting suite stays within its threshold band over 7 consecutive real cycles; the recall suite beats the Day-0 baseline on each cycle's window |
| M4 | **Fleet scheduler.** Cadence orchestration for all pilot users on the shared partition; failure isolation, min-data skip rule, missed-cycle alerting | all pilot users cycle nightly unattended for 14 days; every skip and failure is alerted with cause |
| M5 | **Longitudinal retention study.** Recency vs long-term retention measured across weeks of cycles; self-replay of past personal windows; tuned ratios | a retention report: week-old-day recall quantified, degradation bounded, mixture ratios re-tuned from evidence |
| Obs | **Metrics + dashboard**, per [§Observability](../../ARCHITECTURE.md) ([D9](../../DECISIONS.md)) | `/metrics` scraped by the shared Prometheus; the dashboard shows job status, throughput, step/loss, gate rates, cadence and publish counts |

### M0 — recipe lock and the Morpheus core
> `built` 2026-07-24

**In one line.** Recipe v1.0 is locked and the real Morpheus backend is ported and parity-proven.

**Shape** — recipe v1.0 is 48× amplification + 15% deny-then-correct + LoRA r128/α256 CPT + ~30%
raw-day-log replay + the eval gate.

**Rules**

- The mock nightly cycle sits behind the `TRAINER_BACKEND` seam
  ([ws-nightly-scaffold](handoff/ws-nightly-scaffold.md)).
- The real Morpheus backend is ported and parity-proven
  ([ws-morpheus-port](handoff/ws-morpheus-port.md)).

**Exit criterion — met.** Morpheus reproduces recipe-v1.0 numbers, with the ensemble
indistinguishable from the reference at p=0.82, through gate v1.1. A 32B adapter it trained was
published via C5 and **loaded in vLLM**, recall 0.267.

## Open questions

**Research**

1. **Recency vs retention.** Does nightly LoRA on the new window erode recall of older days? What
   ratio of *self-replay* (past personal windows) holds the line without drowning the new day?
   (Direct heir of live_stream_stability Phase-3.1/3.2.)
2. **LoRA capacity over months.** All-layer LoRA, cycled daily: merge-each-cycle vs stacked
   adapters vs periodic consolidation into a new per-user base? When does effective rank saturate?
   (recursive_finetuning_stability merges each round and tracks SVD effective rank — adopt the method.)
3. **Mentor-trace distillation shape.** Train on thinking tokens, plan, final answer, or all with
   loss masks? Filtered-by-outcome vs outcome-stamped? (The recursive POC's S/F arms + loss-mask
   collator are the live experiment.)
4. **Recursive drift.** Sessions used to train V_{n+1} were generated by V_n — the production loop
   *is* recursive self-SFT. Does the POC's verdict (KL anchor always to V0, replay, collapse
   auto-detection) transfer to the personal-model setting?
5. **Replay composition for a VLM.** The POC found vision (VQA/OCR/video) the most fragile,
   expensive-to-rebuild capability; does LoRA (vs the POC's full-parameter runs) soften or merely
   mask that? What does the v0 replay mix keep from Phase-3.1's buckets?
6. **Personal-recall eval generation.** Auto-deriving the day's question bank from C2/C4 records
   without leaking training targets — Phase-3.2's frozen-split + cross-family blind-judge design
   is the template; what changes when the corpus is one real day, not a 752-hour tour?
7. **LoRA → MoE-experts-per-user.** The scaling path to billions of users: experts routed per user,
   not per token. Untouched research; v0 only needs the adapter artifacts + evals designed so the
   substrate can swap later.
8. **Twin-emergence measurement.** The product narrative claims emergent behavioral mimicry — a
   digital twin — yet our gates measure only recall + forgetting. What eval detects the twin, e.g.
   behavior/preference prediction on held-out user actions? A future eval track beyond M2's gates.

**Engineering**

9. ~~**Watermark semantics (part of C10's design).** Late-arriving or reprocessed records
   (pipeline-version bumps) — does a cycle window close by wall-clock, by ingestion time, or both?~~
   **Resolved ([D18](../../DECISIONS.md), 2026-07-26) — by ingestion time.**

   **Why it's this way**

   - The window is `[last_trained_t, now−δ)` on **storage's `ingest_time`**, which dissolves the
     late-data question instead of answering it: a record's `ingest_time` is assigned at write, so
     it can never land below a closed boundary. **Late data cannot exist on this axis**, and a
     chunk captured Tuesday but uploaded Friday simply trains in Friday's window, in a block
     anchored to Tuesday.
   - What we own downstream of that: **`last_trained_t` advances if and only if we publish**
     *(refined 2026-07-27)*. Gate failure, freeze, crash, no data and **too little** data all leave
     it, so the next window is a strict superset of the failed one.
   - That is the design-of-record's **failed-day merge, obtained structurally**, and it demotes
     `_UserState.debt` (`cycle.py:88-118`) from mechanism to reporting.
   - Full statement: [../../ARCHITECTURE.md](../../ARCHITECTURE.md) § Contracts → *C10 evolved*.

10. **Cycle trigger.** Clock ("nightly", timezone-aware per user) vs data-volume threshold vs
    hybrid; what floor of new data makes a cycle worth running? **What remains open in OQ10 is only
    the trigger policy** — clock vs volume vs hybrid, and the min-data floor.

    **Why it's this way**

    - **The "timezone-aware" half is settled by [D17](../../DECISIONS.md)** (2026-07-26), and it is
      smaller than it looked. A timezone is needed for exactly one thing here — deciding when a
      user's cycle fires, their local ~04:00 — and that reads storage's per-user profile `home_tz`.
    - It is **not** needed to compute the window, once the window becomes the watermark range
      `[last_trained_t, now)` — a plain UTC duration query, which retires
      `window_for(user, local_date, tz)` and its whole local-date-arithmetic class of bugs (23 h and
      25 h days, a repeated local date across the dateline colliding `window_id`).
    - **That window change is `built` 2026-07-27** (`a5a48fb` storage · `1757efb` continuum ·
      `2698b63` data-processing): `window_for()` and `closed_window_before()` are deleted,
      `nightly.py` no longer calls them, and the window is storage's ingest-time watermark.
    - **`window_id` was settled by [D18](../../DECISIONS.md)** (2026-07-26): an opaque, path-safe,
      lexicographically-ordered token `w<YYYYMMDD>T<HHMMSS>Z`, minted once from the window's end
      instant, minted **only** by storage, and **parsed by nobody**.
    - That deletes `Window.local_date` and `ReservoirEntry.local_window_date()` and, with them,
      `cycle.py:217`'s reconstruction of prior windows under *tonight's* timezone. Prior windows are
      enumerated from storage instead. Also `built` 2026-07-27.
    - **Rendering** local times is not a scheduling concern at all — each record carries its own
      `device_tz`, so anchor lines are correct even for a day spent in another zone.
    - **Partly settled 2026-07-27 ([D19](../../DECISIONS.md)):** the trigger is a **cron per user at
      their `home_tz` boundary**, interval configurable in the service. A human-run CLI is the
      prototype stand-in, not the design.

    **Watch out for**

    - The **min-data floor** — the volume of new block text below which a night is not worth a GPU
      run — is designed as a **recipe knob** (`min_block_chars`), measured in *characters of
      eligible block text* rather than block count, because Phase-3 showed recall depends on
      retellings per unit of text.
    - **Correction, 2026-07-27: the mechanism does not exist.** D19 recorded that "the mechanism
      exists so the value becomes a config change". That was false and is retracted here rather
      than caveated.
    - `min_block_chars` appears in no `Recipe` field, no `recipe_from_dict`, no `recipes/*.json`
      and not in `contracts/c13_recipe.v0.json` — a grep returns zero hits in the whole repo.
    - Today only a genuinely empty window skips (`cycle.py:175`), so setting a floor is a
      three-file code change plus a schema edit, not a config edit.
    - This is exactly the "claiming something is built when it is ratified" failure D19's own
      §Stage banner forbids, caught by an adversarial round rather than by a test.
    - **Still the right design**, and it stays cheap to add because D20's advance-only-on-publish
      rule already makes a below-floor night carry forward for free. It is simply not built.

11. **GPU budgeting.** Per-user cycle cost on the shared 8-node partition, contended with research
    runs — priority classes, preemption checkpoints, nightly-window packing.
12. **Adapter artifact lifecycle.** Where per-user adapters live (GCS layout), retention of
    superseded versions, base-model-hash pinning in C5, rollback depth.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Catastrophic forgetting — the personal model gets dumber | breaks the core product promise; user trust gone | M2's forgetting gate blocks publish; M3 replay mixture; KL anchor to base; rollback via C5 |
| Recursive collapse from training on self-generated sessions | compounding quality drift across cycles | KL→V0 anchor (POC-locked default); mentor traces as grounded targets; trend monitoring with auto-pause |
| Eval false-green — the recall bank leaks targets, or the judge is gameable | bad adapters ship silently | held-out split frozen at source-record level (Phase-3.2 precedent, 0 leakage); cross-family blind judge panel |
| Sparse or noisy day — too little data to move weights usefully | wasted GPU; unstable updates | min-data skip rule (M4); accumulate multi-day windows before cycling |
| GPU contention on the shared partition | missed cycles; research and production starve each other | a nightly off-peak window; scheduler priority and preemption checkpoints; cost accounting per cycle |
| A base-model upgrade invalidates every per-user adapter | fleet-wide retrain | split pinned in [§Ownership splits](../../ARCHITECTURE.md); we pin the base-model hash in C5 and execute the migration |
| Deletion/privacy — a user's life is distilled into weights | right-to-delete cannot be met by weight surgery | the v0 **default** is full retrain from retained records minus deletions; final policy is open with platform/storage |

On the base-model row: inference owns custody and serving, and the fleet retrain is explicit, never
hot. On the deletion row: C2/C4 refs keep provenance, the open question is tracked in
[§Ownership splits](../../ARCHITECTURE.md), and machine unlearning is tracked as research.

## Team shape

v0 = **one lead session + on-demand workstream agents** (the POC operating model). As the service
grows, expected sub-teams: **training pipeline** (jobs, mixtures, artifacts — eng), **evals &
gates** (suites, judges, thresholds — research+eng), **data curation & distillation** (mixture
design, mentor-trace shaping — research), **scheduling & infra** (fleet orchestration, cost — eng),
**scaling research** (MoE-experts-per-user — research). Each sub-team follows the org documentation
protocol (manager notes + running logs) per [../../ORG.md](../../ORG.md).

## Related work

- **The consolidation research line (`nucleus-research` @ `b3c58e1`) — Morpheus's source.**
  Two-timescale memory on frozen Qwen3-VL, validated across a 32-day corpus: nightly consolidation
  into one standard PEFT life adapter (vLLM-servable as-is), fast memory + think-back paging on the
  serving side, raw day logs as fact authority. Recipe v1.0 + the eval gate are Morpheus's M0/M2
  substance; our port (clean reimplementation, parity-tested) is [handoff/ws-morpheus-port.md](handoff/ws-morpheus-port.md).
  Two laws inherited as design constraints: components compose by *routing*, never merging; and
  forgetting is *access* decay, not destruction — replay re-teaches, paging revives, raw logs are
  kept forever.
- **[poc/live_stream_stability](../../../poc/live_stream_stability/README.md)** — direct lineage.
  Phase-3.1 (capability-first anti-forgetting replay mixture, vision replay as the fragile bucket),
  Phase-3.2 (Day-0/Day-N personal-recall + general-forgetting eval suites; frozen held-out split;
  blind cross-family judging), Phase-4 (continual-pretrain recipe: describe-targets, vision
  positions masked, LR re-warm). Caveat: the POC trains full-parameter; v0 service is LoRA —
  translating its replay ratios and forgetting thresholds is open question #5. Live state:
  [HANDOFF.md](../../../poc/live_stream_stability/HANDOFF.md).
- **[poc/recursive_finetuning_stability](../../../poc/recursive_finetuning_stability/HANDOFF.md)** —
  the recursive loop V0→VN that our production cycle structurally is. Locked defaults to inherit as
  starting points: KL anchor always to V0, replay window over past rounds, LoRA merge-each-round,
  loss-mask design (train stamps/outcomes, mask boilerplate), collapse auto-detection. Also its
  weights-vs-context (CTRL) arm — the honest baseline for "did fine-tuning beat a context window?".
- **Outside precedents** (trail kept in the POC handoffs): Ibrahim 2024 (LR re-warm/decay + replay
  for continual pretraining); Shumailov 2024 / Gerstgrasser 2024 (collapse vs bounded accumulation);
  STaR/ReST-family filtered self-training; multi-LoRA serving (vLLM, S-LoRA, Punica) — serving side
  is Inference's, but adapter artifact shape must stay compatible.

## How this charter got here

- **2026-07-27 — the D18 re-cut is `built`.**
  - **Was** — the storage re-cut and the C10 evolution were ratified and not built, and this
    charter's status line said so.
  - **Changed** — C10-evolved, C12, C13 and C14 all landed.
  - **Now** — day-log construction, the recipe registry and the reservoir are storage's, and we
    consume them.
  - **Payoff** — continuum is the 5-verb loop the slimming described, rather than a service
    describing an intention.
- **2026-07-26 — D18 ratified the storage re-cut and the C10 evolution.**
  - **Was** — the 2026-07-23 slimming was a proposal with no contract ids behind it.
  - **Changed** — the founders' storage/C10 board ratified it and pinned C10-evolved, C12, C13 and
    C14.
  - **Now** — the counterpart data jobs sit in storage's scope, named by contract.
  - **Payoff** — the boundary is a contract rather than a convention.
- **2026-07-25 — the learn loop closed end to end.**
  - **Was** — Morpheus was ported but the loop had never run through the real services.
  - **Changed** — M0 was met, and the Phase-3 data-processing dogfood ran real data through
    recording → DP → storage → continuum.
  - **Now** — the Morpheus core is our nightly-consolidation engine
    ([handoff/ws-morpheus-port.md](handoff/ws-morpheus-port.md)); the serve-time memory harness went
    to inference; the day-log build, recipe registry and reservoir went to storage; and continuum is
    a 5-verb loop.
  - **Payoff** — the verdict was *pipeline sound*: our real services carry the learn loop without
    losing learnability.
