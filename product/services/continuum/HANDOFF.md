# HANDOFF — Continuum Service working canvas

> Single touch-point for any agent (or human) picking up work on this service.
> Read [CHARTER.md](CHARTER.md) first (mission/scope/interfaces), then this file — the
> volatile working record. Conventions: [../../ORG.md](../../ORG.md) § Documentation protocol.

**Status:** ✅ **LEARN-LOOP INTEGRATION COMPLETE.** Phase 2 (Morpheus port: kernels byte-identical;
ensemble indistinguishable p=0.82; **M0** — a 32B adapter our pipeline trained → gate v1.1 → C5 →
**vLLM**; gate **v1.1 RATIFIED**) · Phase 2c (lean 5-verb loop over storage client seams) · **Phase 3
(DP dogfood): PIPELINE SOUND** — parity content through the **real** recording→DP→storage→continuum
services reproduces the baseline separation (0.137 vs 0.179, p=0.148 same distribution). Our real
services carry the learn loop without losing learnability.
**Open (NOT integration defects):** (1) recipe/dose — amplification must scale with block-text at our
native cadence → **Gnandeep's knob** (cofounder to raise); (2) ~~storage-expansion + C10-evolution →
founders' board~~ **RATIFIED 2026-07-26 → D18** (decided, not built — see below); (3) storage
server-side (day-log materialization / recipe registry / reservoir) → storage workstream, now with
contracts C10-evolved/C12/C13/C14; (4) serve-time memory harness → inference, a separate future
phase. ·
**Last updated:** 2026-07-26 (**D18** — C10 evolution ratified)

## D18 (2026-07-26) — what changes HERE, once the storage build lands

**Decided, not built. No continuum code has changed.** The board ratified the C10 evolution and the
storage scope expansion; our side of it:

- **`app/daylog.py` and `app/window.py`'s local-date arithmetic LEAVE** for storage. `window_for()`
  and `closed_window_before()` are **deleted**; `window.py` shrinks to the `Window` value object
  storage returns. `LocalDayLogClient` + the `RecordProvider` seam disappear exactly as
  `clients/daylog_client.py:14-19` predicted, replaced by `HttpDayLogClient`.
- **`Profile.render_block` STAYS.** The board corrected a premise here: the parity-locked renderer
  (`morpheus/profiles/speed.py:89`, 1427/1427 vs research goldens, over 5-min description dicts) is
  **recipe-coupled** and is *not* what moves. What moves is `daylog.py:183 _render_block`, the
  product renderer over C2 records, which never had a research golden. `morpheus/blocks.py:5-7` had
  already drawn this line. **The move cannot break research parity**; what it must clear is a
  differential byte-equality against our current output (storage CHARTER M9), and **our local path
  is not deleted until that diff is green**.
- **`nightly.py --tz` is retired** in favour of the **C12** profile read. The cycle stops being told
  a timezone by its caller; it uses `home_tz` for **nothing but** the scheduler's fire time.
- **`window_id` becomes opaque** — `w<YYYYMMDD>T<HHMMSS>Z`, minted by storage, **parsed by nobody**.
  That deletes `Window.local_date` (`window.py:44`), `ReservoirEntry.local_window_date()`
  (`reservoir.py:65-69`) and `cycle.py:217`'s reconstruction of prior windows under *tonight's*
  timezone. Prior windows come from storage's **enumeration** read instead — which is why that read
  is load-bearing and not a convenience. **The training seed changes** (`cycle.py:147` hashes
  `window_id`), so a night re-run across the cutover is **not apples-to-apples**; `tests/parity/`
  is unaffected (own harness, own seeds). `scripts/m0_smoke.py:133`'s `w-day5` moves onto the
  minter — it breaks the total order twice over today and was ruled a mess, not a precedent.
- **`_UserState.debt` demotes to reporting.** `last_trained_t` advances only on
  `published`/`skipped_no_data`, so a failed night is absorbed by the next window automatically —
  the design-of-record's failed-day merge, obtained structurally. Strike counting is unaffected
  (each failed night is a distinct, larger window, so each strikes once) and `active_before` still
  resumes from the last `active` entry.

## Workstream index
| WS | What | Status | Working file | Owner session |
|---|---|---|---|---|
| WS1 | Nightly-loop scaffold: mock cycle headless green (window→daylog→amplify→replay→train→gate→publish, journaled + idempotent) | **done** | [handoff/ws-nightly-scaffold.md](handoff/ws-nightly-scaffold.md) | this session |
| WS2 | **Morpheus port** (real `TRAINER_BACKEND=morpheus`); exit = Speed-data night reproduces recipe-v1.0 numbers through our gate + C5 path | **2a + 2b DONE ✅** (port proven; 32B M0 published + served) | [ws-morpheus-port.md](handoff/ws-morpheus-port.md) · [phase-2a-report.md](handoff/phase-2a-report.md) · [overnight-2-report.md](handoff/overnight-2-report.md) | Morpheus sessions |
| WS2c | **Lean storage seams** — 5-verb loop over three storage CLIENT interfaces (day-log fetch / recipe registry / reservoir), local impls; daylog/window/renderer migrated behind the day-log client (byte-identical); raw-source replay wired | **done ✅** (185 tier-A + 83 tier-B green; cycle.py fetches + keys on the day-log fingerprint) | [ws-morpheus-port.md](handoff/ws-morpheus-port.md) §7 (2c) | 2c: Morpheus session |
| WS-P3 | **Phase 3 — DP dogfood**: Speed data through the real recording→DP→storage→continuum pipeline. 3a bridged 209.7 h of real audio; 3b's 1-min rule-bend collapsed on **dose**; the **decomp (parity content) reproduced the baseline separation** → **PIPELINE SOUND** | **done ✅** — learn-loop integration proven end-to-end | [handoff/ws-phase3-dogfood.md](handoff/ws-phase3-dogfood.md) · [phase-3-decomp-report.md](handoff/phase-3-decomp-report.md) | Phase-3 sessions |
| WS3 | C10 **evolution** + real storage integration + watermark/late-data policy | **UNBLOCKED — contract ratified (D18, 2026-07-26), build not started** | *(opens with the build slice)* | — |
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
   [handoff/phase-2a-report.md](handoff/phase-2a-report.md),
   [handoff/overnight-diagnosis-report.md](handoff/overnight-diagnosis-report.md).
   **2a signed off by the cofounders 2026-07-24** — the overnight run closed the last unverified
   kernel surface (rehearsal sampler **byte-identical, 5 nights × 14 seeds**) and cleared the
   single-night trainer (reference's 0.45 = the **70th percentile of our own 8 draws**).
   - **Gate policy RATIFIED:** traps ≥0.15 interim / ≥0.25 at ~150 probes; heldout over all 222
     probes via a one-sided exact test against each run's own base control (α=0.01), 0.15 backstop;
     `min_probes` 150→**148**. All three prior values blocked ~everything *including the validated
     recipe's own output* — the traps floor alone blocked 71% of reference nights, and its
     night-to-night sd equals binomial noise at n=28 to three decimals.
   - **Structural (do with the ratification):** split **gate policy** from the **training recipe**.
     `cycle.py` hashes `recipe_id` into the amplify/train stage keys, so editing a publish-policy
     threshold would fork `recipe_id`, invalidate hours of GPU cache, and falsely imply the trained
     artifact changed. Only the training recipe may enter a stage key.
   - **2b prerequisite (measured):** 32B training needs **≥2 GPUs** — a 32B forward OOMs on one
     H100 at any batch size. 32B *serving* (base + our recipe-shaped LoRA) already proven.
   - **Open, non-blocking:** seed 0 under-performs via **retention, not acquisition**; next test is
     a zero-GPU rehearsal-composition count (did its draws under-sample day-5 paragraphs?).
   - **Capacity unblocked 2026-07-24:** node-7's GPUs are all free (confirmed with Gnandeep) — the
     co-tenant constraint that limited the overnight run is gone.
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
