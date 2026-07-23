# Continuum Service — Charter

> The nightly magic loop: distill each user's new life-stream + sessions into their personal
> model's weights, gated by evals, published through the model directory. Stable doc; working
> state lives in [HANDOFF.md](HANDOFF.md); system-wide architecture + contracts in
> [../../ARCHITECTURE.md](../../ARCHITECTURE.md).

**Status:** chartered; kicked off · **Last updated:** 2026-07-23 (Morpheus core = our
nightly-consolidation engine, per [handoff/ws-morpheus-port.md](handoff/ws-morpheus-port.md);
serve-time memory harness → inference; **day-log build + recipe registry + reservoir → storage**;
continuum slimmed to a 5-verb loop — see [HANDOFF.md](HANDOFF.md) § Architecture decisions.
Storage re-cut pending founders'-board ratification.)

## Mission

Own the periodic (nightly-ish) per-user fine-tuning loop that turns the product's core promise —
"infinite context" + true personalization — into weights. Each cycle: curate a training mixture
from the user's NEW `/context` and `/sessions` records since the last cycle (including mentor
traces, distilling mentor competence into the personal model), blend in an anti-forgetting replay
mixture, run a LoRA job over all layers of the BWM (base world model), gate the candidate adapter
on personal-recall AND general-capability evals, and publish (or roll back) through the model
directory. The service is research-heavy by design: continual-learning stability, recency vs
long-term retention, self-distillation, and the LoRA → MoE-experts-per-user scaling path live here.

## Scope — v0

> **Slimming (2026-07-23, pending board):** continuum is now the **Morpheus** nightly-consolidation
> engine — a lean **5-verb loop: fetch recipe · fetch day-log · amplify · finetune · gate · publish.**
> Day-log materialization, the recipe registry, and the reservoir move to **storage** (rows below +
> Out-of-scope). We *consume* those; we own the recipe-coupled training transforms.

| In scope | Notes |
|---|---|
| Cycle data curation | **Fetch the day-log** for the window via C10 (storage materializes it; we no longer build it client-side); mentor traces (C4) are first-class distillation targets |
| Anti-forgetting replay mixture | Capability-aligned replay (text + vision) trained alongside the personal data; ratio + LR schedule are the levers |
| Amplification (train-time) | The nightly corpus build: styled retellings + deny-then-correct negatives generated FROM the day's faithful records, per the pinned recipe. Output is a training artifact — **amplified/synthetic text never lands in `/context`** (grounding + paging read the faithful record only) |
| Reservoir *write* | We write each night's amplified corpus to the **storage-owned** reservoir via API (audit/provenance). Replay itself re-fetches prior **day-logs** (raw source is a validated tie), so the reservoir is not on the replay hot path. Deletion is a privacy act, never housekeeping |
| LoRA training jobs | Per-user LoRA over **all layers** of the BWM (v0 decision); runs on the shared SLURM partition |
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
| **Day-log materialization** (scheduled C2 → segments/blocks + `render_block`) | Storage Service (2026-07-23 decision, pending board) — we fetch the rendered day-log via C10 |
| **Recipe registry** (versioned recipe hosting; continuum *and* inference pull) | Storage Service (2026-07-23 decision, pending board) — we fetch the pinned recipe, we don't host it |
| **Reservoir custody** (amplified-corpus store + replay-sample read) | Storage Service (2026-07-23 decision, pending board) — we write to it, storage owns the store |
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
| C10 | **consume** | Time-ranged, watermarked training-window read of `/context` + `/sessions`: processed life-stream records (the experiential training data for each cycle window) + turn records incl. full mentor + tool traces (the distillation training data) |
| C5 | **produce** | Adapter version entry: user_id, adapter version, base-model hash, training window, eval report, status active/rolled-back |
| C6 | observe | Inference resolves the latest *eligible* adapter per request; our C5 `status` field is what makes an adapter eligible — we never touch serving |

Future scope (not v0): proactive/coach-mode triggers will involve us jointly with inference —
trigger ownership is tracked as output's proactive open question ([../output/CHARTER.md](../output/CHARTER.md)).

## v0 deliverables

| M | Deliverable | Exit criterion |
|---|---|---|
| M0 | **Recipe lock + Morpheus core.** Recipe v1.0 (48× amplification + 15% deny-then-correct + LoRA r128/α256 CPT + ~30% raw-day-log replay + eval gate). Mock nightly cycle behind the `TRAINER_BACKEND` seam — **done, [ws-nightly-scaffold](handoff/ws-nightly-scaffold.md)**. Real **Morpheus** backend ported + parity-proven per [ws-morpheus-port](handoff/ws-morpheus-port.md) (Speed reproduction landed 2026-07-22) | Morpheus reproduces the recipe-v1.0 numbers (seen-vs-heldout separation ≈+0.25) through our gate; resulting adapter loads in vLLM |
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
9. **Watermark semantics (part of C10's design).** Late-arriving or reprocessed records
   (pipeline-version bumps) — does a cycle window close by wall-clock, by ingestion time, or both?
   Pinned with storage in C10 ([../../ARCHITECTURE.md](../../ARCHITECTURE.md) § Contracts).
10. **Cycle trigger.** Clock ("nightly", timezone-aware per user) vs data-volume threshold vs
    hybrid; what floor of new data makes a cycle worth running?
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
