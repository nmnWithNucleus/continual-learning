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
> unrecorded, silent breakage, or calling a thing BUILT when it is only DECIDED.
> Full posture + what changes at dev/prod: [ARCHITECTURE.md](../../ARCHITECTURE.md) §Stage.


**Status:** chartered; kicked off; **learn-loop integration PROVEN end-to-end (M0 met; Phase-3 DP
dogfood PIPELINE SOUND)** · **Last updated:** 2026-07-25 (Morpheus core = our nightly-consolidation
engine, per [handoff/ws-morpheus-port.md](handoff/ws-morpheus-port.md); serve-time memory harness →
inference; **day-log build + recipe registry + reservoir → storage**; continuum slimmed to a 5-verb
loop — see [HANDOFF.md](HANDOFF.md). **Storage re-cut + C10 evolution RATIFIED 2026-07-26 — D18**; contracts C10-evolved/C12/C13/C14 pinned, and all of it is **BUILT 2026-07-27** *(was: decided-not-built)*.)

## Mission

Own the periodic (nightly-ish) per-user fine-tuning loop that turns the product's core promise —
"infinite context" + true personalization — into weights. Each cycle: curate a training mixture
from the user's NEW `/context` and `/sessions` records since the last cycle (including mentor
traces, distilling mentor competence into the personal model), blend in an anti-forgetting replay
mixture, run a LoRA job over the BWM (base world model) — v0 adapts **all layers of the language
model, vision towers excluded**; "all layers" everywhere remains the standing intent, and §Scope
names the gap — gate the candidate adapter
on personal-recall AND general-capability evals, and publish (or roll back) through the model
directory. The service is research-heavy by design: continual-learning stability, recency vs
long-term retention, self-distillation, and the LoRA → MoE-experts-per-user scaling path live here.

## Scope — v0

> **Slimming (2026-07-23; RATIFIED 2026-07-26 — D18, which put the counterpart data jobs in
> storage's scope with contract IDs):** continuum is now the **Morpheus** nightly-consolidation
> engine — a lean **5-verb loop: fetch recipe · fetch day-log · amplify · finetune · gate · publish.**
> Day-log materialization, the recipe registry, and the reservoir move to **storage** (rows below +
> Out-of-scope). We *consume* those; we own the recipe-coupled training transforms.

| In scope | Notes |
|---|---|
| Cycle data curation | **Fetch the day-log** for the window via C10 (storage materializes it; we no longer build it client-side); mentor traces (C4) are first-class distillation targets |
| Anti-forgetting replay mixture | Capability-aligned replay (text + vision) trained alongside the personal data; ratio + LR schedule are the levers |
| Amplification (train-time) | The nightly corpus build: styled retellings + deny-then-correct negatives generated FROM the day's faithful records, per the pinned recipe. Output is a training artifact — **amplified/synthetic text never lands in `/context`** (grounding + paging read the faithful record only) |
| Reservoir *write* | We write each night's amplified corpus to the **storage-owned** reservoir via API (**C14**, D18; audit/provenance). Replay itself re-fetches prior **day-logs** (raw source is a validated tie), so the reservoir is not on the replay hot path. Deletion is a privacy act, never housekeeping |
| Block rendering — **the half that stays** (D18) | **`Profile.render_block`** (`app/morpheus/profiles/`) is **recipe-coupled** and stays here with the amplifier: it is the surface `tests/parity/test_render_block.py` locks **byte-identical** against the research line (`b3c58e1`, 1427/1427 blocks). `app/morpheus/blocks.py:5-7` drew this boundary before the move was proposed — *"the ONLY interface between ingest and consolidation… keeping that boundary narrow is what lets the day-log move behind a storage client without any kernel noticing."* Also staying: the **trainer-seam file materialization** (`app/renderer.py` → `segments.jsonl`/`blocks.jsonl`/`day.txt`), which is how *our* trainer consumes a day-log it fetched |
| Day-log *construction* — **LEFT** (D18; done 2026-07-27, `1757efb`) | `app/daylog.py` (`build_daylog` + the v0 labeled-lines `_render_block` over C2 records) and `app/window.py`'s local-date arithmetic move to **storage**. **These are a different renderer from the parity surface above and have never had a research golden** — the research line never materialized the 10 s-segment/2 min-block schema (zero producing code; a research "block" is one 5-min description), so the move cannot break research parity. What it must prove instead is a **differential byte-equality** against our own current output; see the storage charter's M9 exit bar. `LocalDayLogClient` + the `RecordProvider` seam disappear exactly as `clients/daylog_client.py:14-19` predicted; `app/window.py` shrinks to the `Window` value object storage returns |
| LoRA training jobs | **AS BUILT (v0): per-user LoRA over the LLM projection linears — all 36 language-model layers × 7 projections (`q,k,v,o,gate,up,down`) — with the VISION TOWERS DELIBERATELY EXCLUDED.** Rationale in the code that enforces it (`app/morpheus/train.py:27-32`, `lora_target_modules()` at `:89-100`): the day log reaches the model **as text**, so adapting the vision stack spends rank on modules that never see the training signal. Parity-proved **252/252 modules = 7 × 36, zero vision-tower**, against the research line's golden adapter tensor keys ([handoff/phase-2a-report.md](handoff/phase-2a-report.md):60). **KNOWN OPEN ITEM, not a contradiction:** [ARCHITECTURE §Founding posture](../../ARCHITECTURE.md) still records **"LoRA per user, all layers"** as the standing *intent* (sourced to `start.md`, an inherited founding assumption, never a ratified D-number). Both statements are true of different things — the intent is the research direction, this row is the build. Two things to hold when revisiting: (1) **flipping to the towers is cheap** — a module-name filter change in `LM_PROJECTIONS`/`_LM_SCOPE` plus a re-parity run, *not* an architecture change, so nothing about writing today's behaviour down makes the flip harder; (2) **the exclusion's premise is falsifiable and expires on its own** — it holds only while the day log reaches the trainer as text, so the day DP feeds the trainer pixels, the reason is gone and this row must be re-argued. Runs on the shared SLURM partition |
| Pre-publish eval gates | Personal-recall suite (does it know yesterday?) + general-capability forgetting suite (did it get dumber?); no green, no publish |
| Publish / rollback | C5 adapter version entries in the model directory; one-command rollback to the previous active version |
| Per-user scheduling | Cadence orchestration across pilot users; retries, idempotency, cost accounting |
| Deletion support primitive | The continuum leg platform's deletion orchestrator calls ([platform M2](../platform/CHARTER.md)): halt the user's scheduled cycles, then retrain-from-retained-records-minus-deletions (v0 default per [§Ownership splits](../../ARCHITECTURE.md)); adapter *artifact* purge itself is storage's primitive |
| Continual-learning research | Stability, retention, self-distillation, recursive-training drift, MoE-experts-per-user path |
| Observability (`/metrics` + dashboard) | D9 obligation ([§Observability](../../ARCHITECTURE.md)): expose `/metrics` (batch/job counters — off the request path) + own a Grafana dashboard JSON (`dashboards/*.json`). Emit training-job metrics (job status, GPU during training, step/loss, throughput), eval-gate pass/fail rates, cycle cadence, publish/rollback counts. Platform runs the shared Prometheus + Grafana backbone |

| Out of scope | Owner |
|---|---|
| Serving adapters / hot-swap in vLLM (C6 resolution) | Inference Service |
| Serve-time memory harness — fast-memory (SSM/mneme) runtime + per-user state, think-back paging executor, day-log-grounded answering, memory routing | Inference Service (kickoff decision 2026-07-22, pending founders'-board ratification; we TRAIN and publish the memory artifacts — mneme module, reader-LoRA, paging recipe — they execute them) |
| `/context` + `/sessions` storage engine and query APIs | Storage Service |
| **Day-log materialization** (scheduled C2 → segments/blocks + anchored block text) | Storage Service (**RATIFIED D18**, **BUILT 2026-07-27** (`a5a48fb` storage · `1757efb` continuum · `2698b63` DP)) — we fetch the rendered day-log via the evolved **C10**, random-access by `(user_id, window_id)`. Note the boundary precisely: what leaves is `daylog.py`'s *product* renderer over C2 records; the **parity-locked `Profile.render_block` stays here**, because it is recipe-coupled |
| **Recipe registry** (versioned recipe hosting; continuum *and* inference pull) | Storage Service (**RATIFIED D18** → **C13**, **BUILT 2026-07-27** (`a5a48fb` storage · `1757efb` continuum · `2698b63` DP)) — we fetch the pinned recipe + the separately-versioned gate policy, we don't host them |
| **Reservoir custody** (amplified-corpus store) | Storage Service (**RATIFIED D18** → **C14**, **BUILT 2026-07-27** (`a5a48fb` storage · `1757efb` continuum · `2698b63` DP)) — we write to it, storage owns the store. Replay does **not** read it: it re-reads prior day-logs via C10 |
| Producing the records we consume (stream + session processing) | Data Processing Service (C2), Inference Service (C4) |
| Calling mentor models (traces reach us only as stored C4 records) | Inference Service (C7) |
| Model directory hosting/query (we only publish via C5) | Storage Service |
| Pretraining the BWM; BWM custody + serving | Out of company v0 scope; Inference Service ([§Ownership splits](../../ARCHITECTURE.md)) |
| Capture devices, user-facing I/O | Recording / Input / Output Services |

## Position in the system

Upstream: **Storage Service** (we read via C10). Downstream: **model directory → Inference Service**
(we publish, they resolve). We sit entirely off the request path; nothing here is latency-critical.

| Contract | Direction | Our role (payloads defined in [../../ARCHITECTURE.md](../../ARCHITECTURE.md) § Contracts) |
|---|---|---|
| C10 | **consume** | **A DAY-LOG FETCH as of D18 (2026-07-26) — **BUILT 2026-07-27** (`a5a48fb` storage · `1757efb` continuum · `2698b63` DP).** We ask storage for "the day-log for `(user_id, window_id)`" and get rendered segment/block rows back; we no longer build it, and we no longer pull raw records to do so. Also consumed: window **enumeration** (which windows has this user consolidated — today inferred from the reservoir ledger, `cycle.py:204`) and the window-ledger **open/close** calls. The window itself is storage's `[last_trained_t, now−δ)` **ingest-time watermark**, so we no longer compute it from a local date and **`window_for()` / `closed_window_before()` are deleted**. The `/sessions` (mentor-trace) leg of this row is unchanged and remains **unbuilt** |
| C12 | **consume** | Per-user profile read — `home_tz`. It replaces `nightly.py --tz`: the cycle stops being told a timezone by its caller. We use it for **nothing but** the scheduler's fire time; the window arithmetic needs no zone, and each block's rendered anchor is resolved by storage from the record's own `device_tz` |
| C13 | **consume** | Recipe registry — the pinned training recipe and the separately-versioned gate policy, by id. Our `LocalRecipeRegistry` is the reference implementation storage lifts |
| C14 | **produce** | Reservoir writes — each night's amplified corpus, append-only, audit/provenance. **Not** the replay path: replay re-reads prior day-logs via C10 |
| C5 | **produce** | **SHAPE NOT PINNED (D19, 2026-07-27):** C5's only consumer is inference via C6 resolve, and we are not building inference yet, so freezing now would cost a session and buy nothing. Continuum's local `var_dir/model_directory/entries.jsonl` carries the full lifecycle meanwhile. The deferral is *free precisely because C5's shape is unpinned* — D18 changed `training_window`'s format, which would otherwise have been a breaking edit. **One standing instruction for whoever does pin it: `training_window` must be pinned as an OPAQUE token, never as a date**, or the id-parsing D18 just deleted (`Window.local_date`, `ReservoirEntry.local_window_date()`) grows straight back. Shape below — Adapter version entry — **as built, NOT pinned** (`app/publish.py:3-4`: "C5's v0 shape is NOT pinned yet (needs inference at the table; founders ratify)"); described here so the gap is visible, **not** to pin it. Nine fields (`publish.py:83-99`): `contract:"C5"`, `user_id`, `adapter_version`, `adapter_dir`, `base_model_hash`, `training_window`, `recipe_id`, `eval_report`, `status`. **`status` has THREE values — `active` \| `gate_failed` \| `rolled_back`** — because a candidate the gate blocks is *recorded* rather than discarded (`record_gate_failure`, `publish.py:101-114`): the row is appended for audit + lineage with `adapter_dir`/`base_model_hash` NULL, and never becomes eligible. That audit trail is exactly what a reader most needs to know exists, so it belongs in the field list and not only in the code |
| C6 | observe | Inference resolves the latest *eligible* adapter per request; our C5 `status` field is what makes an adapter eligible — we never touch serving |

Future scope (not v0): proactive/coach-mode triggers will involve us jointly with inference —
trigger ownership is tracked as output's proactive open question ([../output/CHARTER.md](../output/CHARTER.md)).

## v0 deliverables

| M | Deliverable | Exit criterion |
|---|---|---|
| M0 | **✅ DONE (2026-07-24). Recipe lock + Morpheus core.** Recipe v1.0 (48× amplification + 15% deny-then-correct + LoRA r128/α256 CPT + ~30% raw-day-log replay + eval gate). Mock nightly cycle behind the `TRAINER_BACKEND` seam ([ws-nightly-scaffold](handoff/ws-nightly-scaffold.md)); real **Morpheus** backend ported + parity-proven ([ws-morpheus-port](handoff/ws-morpheus-port.md)) | **Met:** Morpheus reproduces recipe-v1.0 numbers (ensemble indistinguishable from reference, p=0.82) through gate v1.1; a 32B adapter it trained published via C5 and **loaded in vLLM** (recall 0.267) |
| M1 | **Single-user cycle v1.** Watermarked reader over `/context` + `/sessions` (C10) → mixture builder → SLURM LoRA job → candidate adapter artifact | Nightly cycle produces a candidate adapter from one pilot user's real day, idempotent + resumable across job failure |
| M2 | **Eval gates + publish/rollback.** Personal-recall suite auto-derived from the cycle window; general-capability forgetting suite; C5 publish on green only | A deliberately-degraded candidate is blocked; a green candidate goes live via C5 and resolves via C6; rollback restores the prior version in one command |
| M3 | **Replay v1 + mentor distillation.** Capability-aligned replay mixture in every cycle; loss-masked mentor-trace targets in the personal mix | Forgetting suite stays within its threshold band over 7 consecutive real cycles; recall suite beats the Day-0 baseline on each cycle's window |
| M4 | **Fleet scheduler.** Cadence orchestration for all pilot users on the shared partition; failure isolation, min-data skip rule, missed-cycle alerting | All pilot users cycle nightly unattended for 14 days; every skip/failure is alerted with cause |
| M5 | **Longitudinal retention study.** Recency vs long-term retention measured across weeks of cycles; self-replay of past personal windows; tuned ratios | Retention report: week-old-day recall quantified, degradation bounded, mixture ratios re-tuned from evidence |
| Obs | **Metrics + dashboard.** `/metrics` (batch/job counters) + Grafana dashboard JSON, per [§Observability](../../ARCHITECTURE.md) (D9) | Service `/metrics` scraped by the shared Prometheus; dashboard shows training-job status/throughput + step/loss, eval-gate pass/fail, cycle cadence, publish/rollback counts (batch metrics, not request rate/latency — off the request path) |

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
   **RESOLVED (D18, 2026-07-26) — by ingestion time.** The window is
   `[last_trained_t, now−δ)` on **storage's `ingest_time`**, which dissolves the late-data question
   instead of answering it: a record's `ingest_time` is assigned at write, so it can never land
   below a closed boundary — **late data cannot exist on this axis**, and a chunk captured Tuesday
   but uploaded Friday simply trains in Friday's window, in a block anchored to Tuesday. What we
   own downstream of that: **`last_trained_t` advances if and only if we PUBLISH** *(refined
   2026-07-27)* — gate failure, freeze, crash, no data and **too little** data all leave it, so the
   next window is a strict superset of the failed one. That is the design-of-record's **failed-day merge, obtained
   structurally**, and it demotes `_UserState.debt` (`cycle.py:88-118`) from mechanism to reporting.
   Full statement: [../../ARCHITECTURE.md](../../ARCHITECTURE.md) § Contracts → *C10 evolved*.
10. **Cycle trigger.** Clock ("nightly", timezone-aware per user) vs data-volume threshold vs
    hybrid; what floor of new data makes a cycle worth running? *(The **"timezone-aware" half is
    settled by D17**, 2026-07-26, and it is smaller than it looked. A timezone is needed for
    exactly one thing here — **deciding when a user's cycle fires** (their local ~04:00) — and that
    reads storage's per-user profile `home_tz`. It is **not** needed to compute the window once the window
    becomes the watermark range `[last_trained_t, now)` — a plain UTC duration query, which retires
    `window_for(user, local_date, tz)` and its whole local-date-arithmetic class of bugs
    (23 h/25 h days, a repeated local date across the dateline colliding `window_id`). **That window
    change is **BUILT 2026-07-27** (`a5a48fb` storage · `1757efb` continuum · `2698b63` DP): `window_for()` and `closed_window_before()` are DELETED, `nightly.py` no
    longer calls them, and the window is storage's ingest-time watermark** — it rides the storage/C10 board session,
    where `window_id`'s fate has to be settled first because C5's `training_window`, the cycle
    journal and publish's alias monotonicity all key on it. **`window_id` was settled by D18**
    (2026-07-26): it becomes an opaque, path-safe, lexicographically-ordered token
    `w<YYYYMMDD>T<HHMMSS>Z` minted once from the window's end instant, minted **only** by storage,
    and **parsed by nobody** — which deletes `Window.local_date` and
    `ReservoirEntry.local_window_date()` and, with them, `cycle.py:217`'s reconstruction of prior
    windows under *tonight's* timezone. Prior windows are enumerated from storage instead. Now **BUILT 2026-07-27** (`a5a48fb` storage · `1757efb` continuum · `2698b63` DP). **Rendering** local times is not a scheduling concern at all — each record carries its own
    `device_tz`, so anchor lines are correct even for a day spent in another zone. What remains open
    in OQ10 is only the **trigger policy** — clock vs volume vs hybrid, and the min-data floor.
    **Partly settled 2026-07-27 (D19):** the trigger is a **cron per user at their `home_tz`
    boundary**, interval configurable in the service — a human-run CLI is the prototype stand-in,
    not the design. The **min-data floor** — the volume of new block text below which a night is
    not worth a GPU run — lands as a **recipe knob** (`min_block_chars`), measured in *characters of
    eligible block text* rather than block count, because Phase-3 showed recall depends on
    retellings per unit of text. **CORRECTION (2026-07-27): the mechanism does NOT exist.** D19 recorded that
    "the mechanism exists so the value becomes a config change"; that was false and is retracted
    here rather than caveated. `min_block_chars` appears in no `Recipe` field, no `recipe_from_dict`,
    no `recipes/*.json` and not in `contracts/c13_recipe.v0.json` — a grep returns zero hits in the
    whole repo. Today only a genuinely empty window skips (`cycle.py:175`), so setting a floor is a
    three-file code change plus a schema edit, not a config edit. This is exactly the
    "claiming something is built when it is decided" failure D19's own §Stage banner forbids, caught
    by an adversarial round rather than by a test. **Still the right design** — characters of
    eligible block text, not block count, because Phase-3 showed recall tracks retellings per unit
    of text — and it stays cheap to add because D20's advance-only-on-publish rule already makes a
    below-floor night carry forward for free. It is simply not built.)*
11. **GPU budgeting.** Per-user cycle cost on the shared 8-node partition, contended with research
    runs — priority classes, preemption checkpoints, nightly-window packing.
12. **Adapter artifact lifecycle.** Where per-user adapters live (GCS layout), retention of
    superseded versions, base-model-hash pinning in C5, rollback depth.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Catastrophic forgetting — personal model gets dumber | Breaks the core product promise; user trust gone | M2 forgetting gate blocks publish; M3 replay mixture; KL anchor to base; rollback via C5 |
| Recursive collapse from training on self-generated sessions | Compounding quality drift across cycles | KL→V0 anchor (POC-locked default); mentor traces as grounded targets; trend monitoring across cycles with auto-pause |
| Eval false-green — recall bank leaks targets or judge is gameable | Bad adapters ship silently | Held-out split frozen at source-record level (Phase-3.2 precedent, 0 leakage); cross-family blind judge panel |
| Sparse/noisy day — too little data to move weights usefully | Wasted GPU; unstable updates | Min-data skip rule (M4); accumulate multi-day windows before cycling |
| GPU contention on the shared partition | Missed cycles; research and production starve each other | Nightly off-peak window; scheduler priority + preemption checkpoints; cost accounting per cycle |
| BWM upgrade invalidates every per-user adapter | Fleet-wide retrain | Split pinned in [§Ownership splits](../../ARCHITECTURE.md): inference owns BWM custody/serving; we pin the base-model hash in C5 and execute the upgrade migration (fleet retrain) — explicit, never hot |
| Deletion/privacy — a user's life is distilled into weights | Right-to-delete cannot be met by weight surgery | v0 **default** is full retrain from retained records minus deletions (C2/C4 refs keep provenance); final policy is an open question with platform/storage ([§Ownership splits](../../ARCHITECTURE.md)); machine unlearning tracked as research |

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
  into ONE standard PEFT life adapter (vLLM-servable as-is), fast memory + think-back paging on the
  serving side, raw day logs as fact authority. Recipe v1.0 + the eval gate are Morpheus's M0/M2
  substance; our port (clean reimplementation, parity-tested) is [handoff/ws-morpheus-port.md](handoff/ws-morpheus-port.md).
  Two laws inherited as design constraints: components compose by ROUTING, never merging; forgetting
  is ACCESS decay, not destruction (replay re-teaches; paging revives; raw logs kept forever).
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
