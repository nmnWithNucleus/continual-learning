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
founders' board~~ **RATIFIED 2026-07-26 → D18; continuum side BUILT 2026-07-27** (see below); (3) storage
server-side (day-log materialization / recipe registry / reservoir) → storage workstream, now with
contracts C10-evolved/C12/C13/C14; (4) serve-time memory harness → inference, a separate future
phase. ·
**Last updated:** 2026-07-27 (**D18 BUILT** — continuum cut over to storage's HTTP surface; two HIGH
review defects fixed — the crash-close that minted a second window, and the wrong-time-axis default)

## D18 (2026-07-26) — what changed HERE ✅ **BUILT 2026-07-27**

**Continuum is cut over to storage's HTTP surface.** Suite **237 passed + 7 skipped** (was 189 + 7,
then 232 + 7 at the cutover; +5 for the two review fixes below);
`tests/parity/` untouched and green in both tiers (tier A 77+7, tier B 83+1). What landed:

- **`HttpDayLogClient`** (C10 v1 `GET /training/daylog?user_id=&window_id=`), **`HttpWindowLedger`**
  (open / enumerate / close), **`HttpProfileClient`** (C12), **`HttpRecipeRegistry`** (C13),
  **`HttpReservoirClient`** (C14) — all behind the existing protocols, selected by
  **`CONTINUUM_STORAGE_CLIENTS=local|http`**. **`http` is the default** *(corrected 2026-07-27; this
  line said `local`, and `local` was the shipped default until then — see the F2 fix below)*. The
  local day-log path is **not** retired: it stays the parity reference storage's narrowed M9 diff is
  measured against, and it is reached the way that diff reaches it — with records in hand.
- **Deleted:** `window_for()`, `closed_window_before()`, `Window.local_date`,
  `ReservoirEntry.local_window_date()`, and `cycle.py`'s reconstruction of prior windows under
  tonight's timezone. Prior windows now come from the **enumeration** read. A test walks `app/`'s AST
  and fails on any window-id parsing that reappears.
- **`nightly.py --tz` and `--date` are gone.** `home_tz` comes from C12; a 404 exits **2** with an
  operator message and runs nothing. The window is opened by storage and **closed with the cycle's
  outcome** — *and a CRASH is not one of those outcomes* (corrected 2026-07-27; see F1 below).
- **Verified against the real storage service** (started read-only on a spare port with a throwaway
  DB, no storage file touched): 20/20 wire checks, then a full `python -m app.nightly` in HTTP mode
  → `published`, and a two-night `rawlog` run where night 2's rehearsal is night 1's day-log
  re-fetched over C10 with the prior window supplied by enumeration.
- **Named consequence, not a gap: `replay.source="amp"` has no HTTP implementation.** C14 serves the
  **ledger, not the corpora** (by design), and `amp` pools corpus bodies. The HTTP client returns ""
  where the local one would (frac 0, or an empty reservoir) and otherwise **raises**, naming
  `rawlog`. Recipe **v1.0 pins `amp`**, so a v1.0 night over HTTP with history will fail loudly —
  correctly. Closing this is a **recipe** decision (flip the shipped recipe to the locked `rawlog`
  architecture, forking `recipe_id`), not a code one.
- **Not done, deliberately:** `scripts/m0_smoke.py:133`'s `w-day{n}` still bypasses the minter — the
  minter is storage's and that script never talks to storage; `_UserState.debt`'s demotion to
  reporting is untouched (it is a cycle-semantics change, not part of the transport cutover).
  `run.sh`'s demo night now **requires a running storage** (it skips with instructions otherwise) —
  the window is minted there and there is no local minter.

### F3 + F5: the version stamps nobody read, and a demo whose own instructions crashed (2026-07-27)

Suite: **251 passed + 7 skipped** (was 240 + 7). Live seam check: **PASS — 10 steps, 151 checks**
(was 148; the three new ones are the F3 proof, below). No contract moved and no schema changed.

- **F3 (MEDIUM) — `daylog_format_version` and `recipe_id` reached no consumer.** Storage stamped
  both onto every C10 body and continuum read neither, which made them decoration: the whole point
  of a recipe-versioned day-log (ARCHITECTURE §"C10 evolved", D20) is that a format change is
  ANNOUNCED, and an announcement nobody reads is a silent change. **Fix: `HttpDayLogClient` REFUSES**
  a body whose `daylog_format_version` is not in `SUPPORTED_DAYLOG_FORMAT_VERSIONS` or whose
  `recipe_id` is not the one this night trains under (`DayLogDialectMismatch`); `nightly.py` turns it
  into a one-line `MISCONFIGURED` operator message, **leaves the window OPEN** (the F1 rule) and
  exits 2, so the retry after the pin is fixed resumes the same window.
  - **Refuse, not warn**, and the argument is the asymmetry: the mismatch is silent by construction
    (the body parses, the night trains, publish records CONTINUUM's `recipe_id` in C5 — so the
    artifact is MIS-LABELLED, and the label is the only evidence that would have survived), a
    warning on an unattended nightly is read after the adapter is already published, and refusing
    costs one attempt because the window stays open and the watermark does not move.
  - **The two pins are independent** — `STORAGE_DAYLOG_RECIPE_ID` and `CONTINUUM_RECIPE_ID` — so a
    half-finished re-pin is an ordinary deployment slip. It is the exact step `seam_check.py`
    STEP 7c performs, and it now asserts the refusal live, between the two re-pins.
  - **Which dialects are readable is CODE, not config**: `SUPPORTED_DAYLOG_FORMAT_VERSIONS` is a
    literal tuple with no env override, because an operator who could wave a new dialect through
    with an env var would be shipping a corpus change as a config change.
  - Measured live against the real service: storage re-pinned to `consolidation-v1.1` with continuum
    still on `v1.0` → `MISCONFIGURED — REFUSING TO TRAIN`, exit 2, ledger row still
    `state=open, outcome=null`; retry with the pin fixed → **same** `window_id`, `published`.
    *(Honest scope note: v1.0 and v1.1 have identical `corpus` knobs, so for THAT pair the damage is
    purely the lineage label. For a pair like `consolidation-test-1min-v1.0` — `segment_seconds=60`,
    `block_segments=5` — the day-log's SHAPE differs too.)*
- **F5 (LOW) — `run.sh`'s printed demo instructions ended in an unhandled traceback, and the flag
  they recommended trained on nothing.** Reproduced verbatim: profile + `--synthetic` →
  `httpx.HTTPStatusError: Client error '409 Conflict' for url .../training/windows`, because a
  window starts at the user's earliest `ingest_time` and a user with no `/context` records has none.
  `--synthetic` does not substitute — it replaces the DAY-LOG, not the WINDOW. Three fixes:
  - **`window_client.open()` raises `WindowNotOpenable`** carrying storage's own reason, and
    `nightly.py` prints `NO WINDOW TO CONSOLIDATE: <reason>` and exits 2. Both 409s it covers are
    ordinary (`no ingest history`; everything ingested inside storage's `delta`, which fixes itself).
  - **`run.sh` now establishes all three preconditions** (profile, ingest history, elapsed `delta`)
    and runs the REAL path instead of `--synthetic`, so the demo exercises the C10 fetch the cutover
    is about. The no-storage branch prints instructions that were executed verbatim and work.
  - **`app/synth.py`'s placement was stale.** It hard-coded a 4 h lead-in — right for the 24 h
    `window_for(date, tz)` D18 deleted, wrong for an ingest-time window that is routinely minutes
    long. Measured: `--synthetic` on a 66 s first window rendered **0 segments, 0 blocks** and the
    night reported `skipped_no_data` and **exit 0** — trained on nothing, called it success. Offsets
    are now fractions of the window's own span (`min(4h, 0.2·span)`, `min(60, 0.05·span)`, so a 24 h
    window is arithmetically unchanged and every fixture is byte-identical). Its records also now
    satisfy C2 (`blob_ref` was `""` — a 422 at `POST /context/records`) and carry unique
    `record_id`/`chunk_id` (the offset-derived ids collided on short windows, and storage UPSERTs on
    `record_id`).

### Two HIGH defects found by adversarial review and FIXED (2026-07-27)

Both were found by probes driving the **real** storage service as a subprocess, and both are fixed
with a regression test that was proved to fail without its fix. Suite: **237 passed + 7 skipped**
(was 232 + 7). No contract moved and no schema changed — in both cases the written design was
already right and the code disagreed with it.

- **F1 — a retry after a crash minted a SECOND `window_id`.** `nightly.py`'s `except` handler called
  `ledger.close(win, "crashed")` and re-raised. `close()` is **terminal**, and storage's
  `POST /training/windows` is a get-or-create of the user's **open** window — so the retry found none
  open and minted a fresh id, hence a fresh `journal/{user}/{window_id}.json`, a fresh `cycles/` dir,
  a fresh seed, a full re-train, a second C5 entry and a second reservoir admission. That is exactly
  what ARCHITECTURE's C10 row says the idempotent open exists to prevent, reached by a different
  route. **Fix: a crash LEAVES THE WINDOW OPEN**, prints an operator line on stderr, and re-raises;
  the retry re-opens the same window and the journal resumes. Measured on the real service, before →
  after: window closed `crashed` and the immediate retry **409**ed on the id-collision guard (a
  second later it would have minted a second id) → same `window_id`, `stages_skipped =
  ['daylog','amplify','replay_mix']`, **1** C5 entry, **1** reservoir admission. `crashed` stays in
  the outcome enum as the **operator's** deliberate abandon verb — abandoning a night is a decision
  with a human behind it, not something an `except` clause makes on the way past. The watermark was
  never at risk either way (only `published` advances it), so what this cost was journal/lineage/
  compute churn, not data.
- **F2 — the DEFAULT nightly path queried the WRONG TIME AXIS and trained on an empty day-log.**
  `Window.start_utc/end_utc` are **ingest**-time bounds; the local day-log client's default record
  provider passed them to `GET /context/records`, which filters `t_start` — **event** time. With
  `storage_clients` defaulting to `local`, that was the shipped nightly. Measured on the real
  service: storage had a perfectly good day-log for the window (6 segments, 1 block) and the night
  returned **`skipped_no_data`** with no corpus written. Fixed in both halves:
  **(a) the default is now `http`** — the seam is what D18 built and what `scripts/seam_check.py`
  proves end to end; a default that bypasses it ships a configuration nobody exercises.
  **(b) the local path REFUSES** (`IngestWindowNotReadable`) when asked to source a training
  window's records, instead of silently returning none. It was not fixed by adding an `axis=ingest`
  filter to the range read, and that was a deliberate call: storage's own `list_context_by_ingest`
  docstring argues against an axis flag on that endpoint ("overloading one endpoint with an axis
  flag would make it possible to ask the wrong question by typo"), and more decisively the axis is
  not the only thing wrong — `build_daylog` re-filters on `t_start`, so a backlog record
  (ingested inside the window, captured before it) is **dropped outright**. *(As first written this
  sentence also said `_bucket_index` is window-relative and goes negative on such a record. That
  was true then and is not now: **F4** moved continuum's local grid to the same global epoch grid
  storage uses. The event-time re-filter is what still makes the local path the wrong answer for an
  ingest-time window, and it is deliberate — see `app/daylog.py`'s header.)* Making the local path
  source its own records is re-implementing
  storage's materializer inside continuum, i.e. the O(days²) duplication D18 rejected. **Tradeoff,
  named:** the local backend can no longer run a night on its own — it answers only "here are the
  records, render them". That costs nothing real: the M9 parity script hands it a fixed record list,
  and so do `--synthetic` and `scripts/phase3_daylog.py` (which builds its own **event**-time window
  on purpose, and is the one honest consumer of the range read).

The board ratification, for the record:

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
| WS3 | C10 **evolution** + real storage integration + watermark/late-data policy | **continuum side DONE ✅ (2026-07-27)** — five HTTP clients behind the existing protocols, `--tz`/`--date` retired, window-id parsing deleted; verified against the live storage service. Local backends retained as the M9 parity reference | [HANDOFF §D18](#d18-2026-07-26--what-changed-here--built-2026-07-27) | this session |
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
