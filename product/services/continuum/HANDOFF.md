# HANDOFF — Continuum Service working canvas

> Single touch-point for any agent (or human) picking up work on this service.
> Read [CHARTER.md](CHARTER.md) first (mission/scope/interfaces), then this file — the
> volatile working record. Conventions: [../../ORG.md](../../ORG.md) § Documentation protocol.

**Status:** kickoff DONE (2026-07-21/22 founder sessions) — architecture split settled at
founder level, scaffold landed (WS1, 46 tests green incl. an adversarially-reviewed fix
round), real-backend port queued on Gnandeep's answers · **Last updated:** 2026-07-22
(kickoff + scaffold session)

## Workstream index
| WS | What | Status | Working file | Owner session |
|---|---|---|---|---|
| WS1 | Nightly-loop scaffold: mock cycle headless green (window→daylog→amplify→replay→train→gate→publish, journaled + idempotent) | **done** | [handoff/ws-nightly-scaffold.md](handoff/ws-nightly-scaffold.md) | this session |
| WS2 | Engram core port (real `TRAINER_BACKEND`); exit = Speed-data night reproduces recipe-v1.0 numbers through our gate + C5 path | **unblocked** (Gnandeep answered 2026-07-22); sequenced behind the Phase-1 baseline | [handoff/ws-engram-port.md](handoff/ws-engram-port.md) | — |
| WS3 | C10 v0 freeze (with storage; founders ratify) + real storage integration + watermark/late-data policy | queued | *(opens with the freeze session)* | — |
| WS4 | Eval gates v1: probe generation (generator ≠ corpus-generator), Gemini judge on our creds, the 3 unwired gate checks | queued — after WS2 | *(opens with work)* | — |

## Validation strategy — replay Speed's data to prove the port (2026-07-22)

The learn-loop gets validated on Speed's existing 35-day-tour data *before* new capture
feeds it. **Key provenance finding** (full trace in this session): the recipe-v1.0 numbers
(0.26–0.35 recall, +0.33 separation, traps 0.50) came from the **engram** path —
pre-existing **5-min Gemini descriptions** → `build_day_corpus` → 48× amplify → CPT → judge.
The speed-lora **RUN2/PROJECT_REPORT** docs describe the *failed* QA-SFT branch (null
separation through full-FT) — valuable as diagnosis (why the recipe has amplification +
deny-then-correct negatives), NOT a path to port. The DESIGN_PROD **10s-segment/2min-block
schema was never materialized** (zero producing code); a research "block" = one 5-min
description. Two separable exercises, only the first judged by "matches his numbers":

- **Exercise 1 (port fidelity):** reproduce his numbers on **his data shape** (5-min
  descriptions → engram chain), standalone — **no product pipeline**. Clean A/B (his code vs
  our port on identical input). This is the M0 exit + "perfect port" proof.
- **Exercise 2 (DP dogfood):** push Speed data through **our** recording→DP→storage→continuum
  (injecting the stored ASR/caption stage outputs), producing the product-shape day-log.
  Validated as **plumbing + the first real data on the domain-transfer question** (DESIGN_PROD
  R1b) — **cannot** be judged by exact numbers (different data shape; an open research question).

Out of scope for both: **fast-memory slots** (serve-time only; the learn loop never touches
them; recall is pure life-adapter CPT). **Records stay** the faithful substrate — day-log
segments/blocks are a *derived view*, not a replacement (the serve loop + paging depend on
C2 records).

## Execution steps (locked 2026-07-22)

0. **DONE this session:** re-pin the engram port source `9711f4a → b3c58e1` ("32B chain
   verdict"; ws-engram-port); flag the serve-tier drift in inference's HANDOFF (the harness
   is now a **4-lane** stack — Council added — + page-weight cache, richer than the "3 lanes"
   recorded at kickoff).
1. **Phase 0 (founder, no blockers):** locate/confirm Speed data on the cluster —
   `descriptions/{1,5,10,20}min/`, `holdout_manifest.csv`, WhisperX/pyannote ASR, frame grids.
   (Local `poc/live_stream_stability/.../ishowspeed/tour` holds the *collection* code + manifests;
   the 61k descriptions live on the cluster at `~/speed_lora/data/descriptions/`.)
2. **Phase 1 — Exercise 1a (baseline, his code on our infra):** run `build_day_corpus →
   gen_narrative --variants 48 --neg-frac 0.15 → phase_d_driver --arm replay --replay-frac 0.30
   → judge_exact` on ~3 probe-dense days; confirm ≈0.26–0.35 / +0.33 / traps ≈0.50. **Standalone,
   not through the product.** Blocked only on lethal-Q4 (envs export + our judge creds).
3. **Phase 2 — Exercise 1b (WS2 port):** port module-by-module behind `TRAINER_BACKEND=engram`,
   parity-checked against Phase 1 at each stage. Exit = parity = perfect port = M0 done.
4. **Phase 3 — Exercise 2 (DP dogfood):** the throwaway processor through our DP → derived
   day-log view → continuum nightly loop; measure the shape-gap vs Phase 2 (the R1b result).

## Kickoff decisions (founder, 2026-07-21/22 sessions)

Numbered locally; where a decision re-cuts a charter or contract it is **pending
founders'-board ratification** (D-numbers to be minted there) — flagged per item.

1. **Serve-time memory harness lives in the INFERENCE service** — fast-memory (mneme/SSM)
   runtime + per-user state, think-back paging executor, day-log-grounded answering,
   memory routing (today-path vs past-day). Continuum TRAINS and publishes the artifacts
   (nightly life adapter; mneme module + reader-LoRA as occasional jobs; paging recipe as
   versioned config); inference executes them. Same pattern as BWM custody. *(Re-cuts
   inference charter + C5 shape → board.)* Flagged in inference's HANDOFF.
2. **DP owns the data heavy-lifting** — caption/chunking stages upgrade to the
   speed-data-grade dense-description spec (event verbs, structured fields, quality score);
   day-log derived views (`day_segments`/`day_blocks`) as **DB tables**, not node files
   (files rendered only at the trainer seam); fast-memory **slot generation as a DP stage**
   later (requires the order-independent `retrieve` write rule — serve-time step, deferred).
   *(Re-cuts DP charter → board; caption-spec feedback owed to DP.)*
3. **Amplified/synthetic text NEVER lands in `/context`** — the faithful-record invariant.
   Amplified corpora persist per (user, window, recipe) in the training **reservoir**
   (storage custody is the plan of record; scaffold keeps it under var/ meanwhile).
4. **Sequencing: nightly learn-loop first** (recording→DP→storage→continuum→C5); the
   serve-time path (router/slots/paging) is the NEXT step, co-designed with inference.
5. **Port, don't pin** — research files are ported into `app/engram/` and adapted in place;
   Gnandeep works in our modules once the service runs E2E. Source snapshot `9711f4a` +
   divergence log in [handoff/ws-engram-port.md](handoff/ws-engram-port.md).
6. **Nomenclature** — engram's day-log terms adopted as *derived views over C2* (our
   ~10 s client "segment" ≈ his segment span; day-log segment rows are a TIME-WINDOW join
   over C2 records, since audio chunks are 5–30 s VAD-carved and video captions per-keyframe).
   C2 v0 stays frozen; quality/entities land in the derived rows until additive C2 fields.

## Current state
- **WS1 scaffold is live** on `svc/continuum-scaffold`: full mock nightly cycle, 46 tests,
  `./run.sh` demos a synthetic night end-to-end (publish + reservoir admission + journal).
  Adversarial review round (26 confirmed findings → all fixed): details in
  [ws-nightly-scaffold](handoff/ws-nightly-scaffold.md).
  Recipe pinned at `recipes/consolidation-v1.0.json` (engram v1.0 knobs; replay/source
  values pending Gnandeep confirmation).
- **Maturity read of the research repo is complete** (line-by-line: LOG, DESIGN_PROD, all
  of engram/code, speed-lora, continuum thread) — the kickoff brief's Q1–Q4 are resolved in
  session notes; key headline: the nightly product is a stock PEFT LoRA (vLLM-servable),
  0.26–0.35 judged recall, 6/6 days replicated; the serve-time tiers are where 0.324/66.7%
  quality lives (inference's future scope).
- **Code-vs-design divergences found during verification** (feed into Gnandeep asks):
  production design says per-segment mean-pooled slots, validated code stores per-token;
  design says Vertex amplify backend, code implements vllm/hf only (Gemini is only the judge).
- D9 observability obligation unchanged (metrics + dashboard, off the request path) — not started.

## Gnandeep answers (2026-07-22) — folded into [ws-engram-port](handoff/ws-engram-port.md)
- **32B ≈ 8B (a TIE):** 0.083 vs 0.092 on identical probes → consolidation is **write-bound,
  not capacity-bound**; 8B is his serving substrate. We still train **32B** adapters (BWM=D6;
  adapter must match the served base; recipe is base-agnostic + 32B chain proven), paying 32B
  compute for serve-quality not memory-quality — an 8B memory substrate is a later founders' call.
- **Entrypoint:** the phased/replay chain (`phased_run.sh`/`submit_chain.sh` → `build_day_corpus
  → gen_narrative → phase_d_driver --arm replay → judge_exact`); `phase_d_driver`'s replay arm
  IS the production night. Stable snapshot = `b3c58e1` (re-pinned).
- **Knobs:** replay-frac **0.30**; replay-source **raw is a tie → acceptable + simpler** (may let
  v0 replay from retained raw day logs instead of an amplified reservoir); neg-boost = read off
  the chain args (no default on faith). Confirm all three at the actual invocation.
- **Envs/judge:** `speedlora`+`vllm23` exports coming to `research/engram/envs/`; judge =
  Gemini-2.5-flash via litellm/Vertex; **our own GCP creds via IAM** (his project is his billing).
- **Still open (real-user nights only):** de-Speed the prompt/anchor scheme.

## Next
1. **Run the Execution steps above** (Phase 0 → 1 → 2 → 3). Phase 1 is the immediate action
   once the founder confirms the cluster data + we have envs/judge creds (lethal-Q4).
2. **C10 freeze session** with storage (founders ratify) — first contract act, per D15.
   Propose: beta range read + `pipeline_version`/modality filters + (t_start, record_id)
   ordering + cursor; watermark/late-data policy rides along (charter OQ9).
3. **Founders'-board ratification** of the kickoff decisions that re-cut charters/contracts
   (memory harness → inference; DP data ownership + caption spec; C5 bundle shape when the
   memory artifacts ship; reservoir custody in storage; retention/deletion policy — the
   research design's raw-A/V ≤72 h + day-logs-forever + 14-night hard-delete stance is a
   PRODUCT decision to take explicitly).
4. Then M1: real C10 reader against storage, SLURM submission, node-7 off-peak window.

## Cross-service flags (no unilateral pinning — informational until ratified)
- **storage:** day-log derived views + reservoir custody + model-directory hosting are all
  headed their way; C10 freeze is the first joint act.
- **data-processing:** caption-spec upgrade (event-verb dense descriptions, quality score,
  eval-only QA field), segment/block consolidation stages, later an `amplify` batch stage
  option and slot-generation stage — all queued behind the board session.
- **inference:** memory harness incoming (noted in their HANDOFF § Incoming); C5 entries are
  already being produced by the scaffold's local outbox (their M1 hot-swap consumes these
  once the model directory is storage-hosted).
