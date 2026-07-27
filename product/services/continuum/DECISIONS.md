# DECISIONS — continuum (service-local register)

> Decisions taken **inside this service's chartered autonomy**, numbered `C-n`, newest first.
> Anything that re-cuts a charter or a contract is **not** ours to settle: it is proposed here,
> escalated via [../../HANDOFF.md](../../HANDOFF.md) §Escalations, and ratified at the founders'
> board with a **D-number** in [../../DECISIONS.md](../../DECISIONS.md). The Status column says
> which state each row is in.
>
> **Stage: PROTOTYPE** ([D19](../../DECISIONS.md)) — these evolve. A decision is never rewritten
> to say something different; it is superseded by a new row and the old one marked. Only *status*
> changes in place.

---

| # | Decision | Status | Detail |
|---|---|---|---|
| **C-11** | **Contract consequences of the above** | **RATIFIED → [D18](../../DECISIONS.md)** (C10 evolved; C12/C13/C14 minted) | **Contract consequences (pin later):** **C10 evolves** to "fetch the day-log for a window" (not raw records); **new** recipe-registry + reservoir seams; C5 publish unchanged. |
| **C-10** | **Recipe v1.0 is the target; no over-calibration** | **partly superseded** — recipe **v1.1** was minted 2026-07-27, forking v1.0 on one knob (`replay.source` `amp` → `rawlog`). v1.0 is retained as the recipe the Phase-1/3 numbers were produced under | **Recipe v1.0 is the target; no over-calibration** — 40% neg-boost lobotomizes (recall→0.021); horizon trap-erosion is handled at the gate (≥0.40 blocks + refresher), `replay_neg_boost` a ≤10% tunable default-off. Replay source = **raw** (tie) → replay re-fetches prior day-logs. |
| **C-9** | **Naming: Morpheus** | **done** — `continuum/app/morpheus/` | **Naming: Morpheus** (`continuum/app/morpheus/`), versioned per method change. "Engram" dropped from our surface (provenance = commit `b3c58e1` only). |
| **C-8** | **Continuum slims to a 5-verb loop** | **done** — the 5-verb loop is what runs | **Continuum slims to a 5-verb loop:** fetch recipe · fetch day-log · **amplify** · **finetune** · gate · publish. Amplification stays here (recipe-coupled, synthetic-not-faithful); its output is written to the reservoir via a storage API. |
| **C-7** | **Storage owns the data jobs** | **RATIFIED → [D18](../../DECISIONS.md)**, and **BUILT 2026-07-27** | **Storage owns the data jobs** (re-cuts storage charter → board): **day-log materialization** (scheduled C2 → segments/blocks + `render_block`), the **recipe registry** (versioned; continuum *and* inference pull), the **training reservoir** (amplified-corpus write + replay read), plus the model directory. Continuum *consumes* all of these. |
| **C-6** | **Nomenclature — day-log terms as derived views** | **done** — C2 v0 stayed frozen throughout | **Nomenclature** — engram's day-log terms adopted as *derived views over C2* (our ~10 s client "segment" ≈ his segment span; day-log segment rows are a TIME-WINDOW join over C2 records, since audio chunks are 5–30 s VAD-carved and video captions per-keyframe). C2 v0 stays frozen; quality/entities land in the derived rows until additive C2 fields. |
| **C-5** | **Port, don't pin** | **done** — port parity-proven; source snapshot `b3c58e1` | **Port, don't pin** — research files are ported into `app/engram/` and adapted in place; Gnandeep works in our modules once the service runs E2E. Source snapshot `9711f4a` + divergence log in [handoff/ws-morpheus-port.md](handoff/ws-morpheus-port.md). |
| **C-4** | **Sequencing: nightly learn-loop first** | **done** — the nightly loop is closed; the serve-time path is still the next step | **Sequencing: nightly learn-loop first** (recording→DP→storage→continuum→C5); the serve-time path (router/slots/paging) is the NEXT step, co-designed with inference. |
| **C-3** | **Amplified/synthetic text NEVER lands in `/context`** | **standing invariant** (service-local). Reservoir custody ratified → [D18](../../DECISIONS.md) | **Amplified/synthetic text NEVER lands in `/context`** — the faithful-record invariant. Amplified corpora persist per (user, window, recipe) in the training **reservoir** (storage custody is the plan of record; scaffold keeps it under var/ meanwhile). |
| **C-2** | **DP owns the data heavy-lifting** | **pending founders' board** — re-cuts the DP charter; caption-spec feedback owed to DP | **DP owns the data heavy-lifting** — caption/chunking stages upgrade to the speed-data-grade dense-description spec (event verbs, structured fields, quality score); day-log derived views (`day_segments`/`day_blocks`) as **DB tables**, not node files (files rendered only at the trainer seam); fast-memory **slot generation as a DP stage** later (requires the order-independent `retrieve` write rule — serve-time step, deferred). *(Re-cuts DP charter → board; caption-spec feedback owed to DP.)* |
| **C-1** | **Serve-time memory harness lives in the INFERENCE service** | **pending founders' board** — re-cuts the inference charter + C5 shape | **Serve-time memory harness lives in the INFERENCE service** — fast-memory (mneme/SSM) runtime + per-user state, think-back paging executor, day-log-grounded answering, memory routing (today-path vs past-day). Continuum TRAINS and publishes the artifacts (nightly life adapter; mneme module + reader-LoRA as occasional jobs; paging recipe as versioned config); inference executes them. Same pattern as BWM custody. *(Re-cuts inference charter + C5 shape → board.)* Flagged in inference's HANDOFF. |

---

**Provenance.** `C-1`…`C-6` were the founder kickoff sessions of 2026-07-21/22; `C-7`…`C-11` the
cofounders' architecture session of 2026-07-23. Both were sections of the service canvas until
2026-07-27, when they moved here so the canvas could go back to being a board.
