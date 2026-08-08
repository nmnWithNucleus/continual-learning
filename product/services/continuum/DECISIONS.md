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
| **C-10** | **No over-calibration** | **standing** — the pinned recipe is `consolidation-v2.0`; every earlier version is retained, immutable under its id, as the recipe its numbers were produced under | **No over-calibration.** 40% neg-boost lobotomizes (recall→0.021); horizon trap-erosion is handled at the gate (≥0.40 blocks + refresher), `replay_neg_boost` a ≤10% tunable default-off. Replay source is `rawlog` — a measured tie with `amp`, and the only one C14 can serve, since the ledger carries no corpus bodies. |
| **C-9** | **Naming: Morpheus** | **done** — `continuum/app/morpheus/` | **Naming: Morpheus** (`continuum/app/morpheus/`), versioned per method change. "Engram" dropped from our surface (provenance = commit `b3c58e1` only). |
| **C-8** | **Continuum slims to a 5-verb loop** | **done** — the 5-verb loop is what runs | **Continuum slims to a 5-verb loop:** fetch recipe · fetch day-log · *amplify* · *finetune* · gate · publish. Amplification stays here (recipe-coupled, synthetic-not-faithful); its output is written to the reservoir via a storage API. |
| **C-5** | **Port, don't pin** | **done** — port parity-proven against snapshot `b3c58e1` | **Port, don't pin** — research files are ported into `app/morpheus/` and adapted in place; Gnandeep works in our modules once the service runs E2E. The snapshot `b3c58e1` is the anchor every parity golden is measured against; the divergence log is in [handoff/ws-morpheus-port.md](handoff/ws-morpheus-port.md). |
| **C-3** | **Amplified/synthetic text never lands in `/context`** | **standing invariant** (service-local). Reservoir custody ratified → [D18](../../DECISIONS.md) | **Amplified/synthetic text never lands in `/context`** — the faithful-record invariant. Amplified corpora persist per (user, window, recipe) in the training *reservoir* (storage custody is the plan of record; scaffold keeps it under var/ meanwhile). |
| **C-2** | **DP owns the data heavy-lifting** | **pending founders' board** — re-cuts the DP charter; caption-spec feedback owed to DP | **DP owns the data heavy-lifting** — caption/chunking stages upgrade to the speed-data-grade dense-description spec (event verbs, structured fields, quality score); day-log derived views (`day_segments`/`day_blocks`) as *DB tables*, not node files (files rendered only at the trainer seam); fast-memory *slot generation as a DP stage* later (requires the order-independent `retrieve` write rule — serve-time step, deferred). *(Re-cuts DP charter → board; caption-spec feedback owed to DP.)* |
| **C-1** | **Serve-time memory harness lives in the inference service** | **pending founders' board** — re-cuts the inference charter + C5 shape | **Serve-time memory harness lives in the inference service** — fast-memory (mneme/SSM) runtime + per-user state, think-back paging executor, day-log-grounded answering, memory routing (today-path vs past-day). Continuum trains and publishes the artifacts (nightly life adapter; mneme module + reader-LoRA as occasional jobs; paging recipe as versioned config); inference executes them. Same pattern as BWM custody. *(Re-cuts inference charter + C5 shape → board.)* Flagged in inference's HANDOFF. |

---

**Numbering holes are deliberate.** C-4, C-6, C-7 and C-11 were spent — sequencing that has
happened, vocabulary for a record shape that no longer exists, and two rows the founders absorbed
whole into [D18](../../DECISIONS.md), which now carries the decision. They were removed under
[D29](../../DECISIONS.md) rather than left as tombstones; a number is never reused, and the full
text is in git history.
