# HANDOFF — Continuum Service working canvas

> Single touch-point for any agent (or human) picking up work on this service.
> Read [CHARTER.md](CHARTER.md) first (mission/scope/interfaces), then this file — the
> volatile working record. Conventions: [../../ORG.md](../../ORG.md) § Documentation protocol.

**Status:** scaffold landed (WS1, 46 tests green); Speed-data reproduction **REPRODUCED ✅**
(Phase 1); Morpheus port **Phase 2a landed** — kernels + parity harness green, E2E seed ensemble
measured (WS2). Storage-expansion + continuum-slimming pending board ·
**Last updated:** 2026-07-23 (Morpheus 2a session)

## Workstream index
| WS | What | Status | Working file | Owner session |
|---|---|---|---|---|
| WS1 | Nightly-loop scaffold: mock cycle headless green (window→daylog→amplify→replay→train→gate→publish, journaled + idempotent) | **done** | [handoff/ws-nightly-scaffold.md](handoff/ws-nightly-scaffold.md) | this session |
| WS2 | **Morpheus port** (real `TRAINER_BACKEND=morpheus`); Phase 2a parity → 2b M0 → 2c lean/storage-seams; exit = Speed-data night reproduces recipe-v1.0 numbers through our gate + C5 path | **2a landed** (kernels + parity green; E2E ensemble measured) → 2b next | [ws-morpheus-port.md](handoff/ws-morpheus-port.md) · [phase-2a-report.md](handoff/phase-2a-report.md) | 2a: Morpheus session |
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

## Execution steps

0. **DONE:** re-pin source `9711f4a → b3c58e1`; flag serve-tier drift in inference (4-lane
   stack + page-weight cache).
1. **Phase 0 — DONE:** Speed data confirmed on the cluster (`descriptions/{1,5,10,20}min/`,
   `holdout_manifest.csv`); prebuilt corpora/adapters/results all present on the node.
2. **Phase 1 — DONE ✅ REPRODUCED:** ran his replay_f30 chain on our infra — seen-mean 0.286
   (== his seed-0), separation **+0.253** (in his +0.178…+0.269 spread), day-5 retention 1.00,
   corpus rebuild ratio 1.004. GO for Phase 2.
3. **Phase 2 — Morpheus port (WS2):** **2a DONE** — kernels reimplemented under
   `app/morpheus/` behind `TRAINER_BACKEND=morpheus`, parity harness green against the Phase-1
   goldens (`render_block` byte-identical on 1427/1427 blocks; replay+chunking fingerprint 18/18
   integers exact; LoRA target set 252/252 modules; judge summary exact on 35 suites × 4 runs),
   E2E seed ensemble run on the node. → 2b full cycle + M0 (adapter loads in vLLM) → 2c lean
   architecture + storage-client seams. Spec:
   [handoff/ws-morpheus-port.md](handoff/ws-morpheus-port.md) · results:
   [handoff/phase-2a-report.md](handoff/phase-2a-report.md).
4. **Phase 3 — DP dogfood (later):** records → storage day-log view → continuum; measures the
   shape-gap vs Phase 2 (the R1b domain-transfer result).

## Architecture decisions (cofounders, 2026-07-23) — pending board ratification where noted

- **Storage owns the data jobs** (re-cuts storage charter → board): **day-log materialization**
  (scheduled C2 → segments/blocks + `render_block`), the **recipe registry** (versioned; continuum
  *and* inference pull), the **training reservoir** (amplified-corpus write + replay read), plus
  the model directory. Continuum *consumes* all of these.
- **Continuum slims to a 5-verb loop:** fetch recipe · fetch day-log · **amplify** · **finetune** ·
  gate · publish. Amplification stays here (recipe-coupled, synthetic-not-faithful); its output is
  written to the reservoir via a storage API.
- **Naming: Morpheus** (`continuum/app/morpheus/`), versioned per method change. "Engram" dropped
  from our surface (provenance = commit `b3c58e1` only).
- **Recipe v1.0 is the target; no over-calibration** — 40% neg-boost lobotomizes (recall→0.021);
  horizon trap-erosion is handled at the gate (≥0.40 blocks + refresher), `replay_neg_boost` a
  ≤10% tunable default-off. Replay source = **raw** (tie) → replay re-fetches prior day-logs.
- **Contract consequences (pin later):** **C10 evolves** to "fetch the day-log for a window" (not
  raw records); **new** recipe-registry + reservoir seams; C5 publish unchanged.

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
   divergence log in [handoff/ws-morpheus-port.md](handoff/ws-morpheus-port.md).
6. **Nomenclature** — engram's day-log terms adopted as *derived views over C2* (our
   ~10 s client "segment" ≈ his segment span; day-log segment rows are a TIME-WINDOW join
   over C2 records, since audio chunks are 5–30 s VAD-carved and video captions per-keyframe).
   C2 v0 stays frozen; quality/entities land in the derived rows until additive C2 fields.

## Current state
- **WS1 scaffold is live** on `svc/continuum-scaffold`: full mock nightly cycle, 46 tests,
  `./run.sh` demos a synthetic night end-to-end (publish + reservoir admission + journal).
  Adversarial review round (26 confirmed findings → all fixed): details in
  [ws-nightly-scaffold](handoff/ws-nightly-scaffold.md).
- **Morpheus 2a is live** on `svc/continuum-morpheus-2a`: the real kernels under
  `app/morpheus/` (Profile seam · blocks · amplify+generate · replay · train · scorers · probes ·
  judge · eval · pinned-env exec), the `morpheus` backend behind the three-verb seam, and
  `tests/parity/` as the contract. `./scripts/run_parity.sh` runs both tiers;
  `scripts/morpheus_chain.py` runs a full chain and judges it. Env lockfiles in `env/`.
  Recipe knobs are now CONFIRMED against the goldens (frac 0.30 / source amp / neg_boost 0);
  the source flip to rawlog is a validated tie that forks `recipe_id` and lands with 2c.
- **Maturity read of the research repo is complete** (line-by-line: LOG, DESIGN_PROD, all
  of engram/code, speed-lora, continuum thread) — the kickoff brief's Q1–Q4 are resolved in
  session notes; key headline: the nightly product is a stock PEFT LoRA (vLLM-servable),
  0.26–0.35 judged recall, 6/6 days replicated; the serve-time tiers are where 0.324/66.7%
  quality lives (inference's future scope).
- **Code-vs-design divergences found during verification** (feed into Gnandeep asks):
  production design says per-segment mean-pooled slots, validated code stores per-token;
  design says Vertex amplify backend, code implements vllm/hf only (Gemini is only the judge).
- D9 observability obligation unchanged (metrics + dashboard, off the request path) — not started.

## Gnandeep answers (2026-07-22) — folded into [ws-morpheus-port](handoff/ws-morpheus-port.md)
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
