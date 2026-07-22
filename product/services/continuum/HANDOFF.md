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
| WS2 | Engram core port (real `TRAINER_BACKEND`); exit = one Speed-data night reproduces recipe-v1.0 numbers through our gate + C5 path | queued — blocked on the 5 lethal questions | [handoff/ws-engram-port.md](handoff/ws-engram-port.md) | — |
| WS3 | C10 v0 freeze (with storage; founders ratify) + real storage integration + watermark/late-data policy | queued | *(opens with the freeze session)* | — |
| WS4 | Eval gates v1: probe generation (generator ≠ corpus-generator), Gemini judge on our creds, the 3 unwired gate checks | queued — after WS2 | *(opens with work)* | — |

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

## Pending Gnandeep (the 5 lethal questions — full text in [ws-engram-port](handoff/ws-engram-port.md))
32B chain result + exact base id · production-night entrypoint + stable commit · blessed
replay knobs (frac/source/neg-boost) · env exports + judge-project OK · prompt/anchor
de-Speed-ification approach (blocks real-user nights only). Founder is sending these.

## Next
1. **WS2 port** the moment Q1/Q2/Q3 come back (Q4 gates the judge, Q5 gates real users).
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
