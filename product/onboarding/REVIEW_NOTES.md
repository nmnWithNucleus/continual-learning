# LEARN_LOOP.md — accuracy review, running notes

> Working file for the founders' review of [LEARN_LOOP.md](LEARN_LOOP.md). Findings are removed
> from this file as they are fixed, so **whatever is still written here is still outstanding.**
> Delete this file when it empties.

**Review pass 1:** 2026-07-26 — read the doc end to end, then re-derived its claims from source.
Every `file:line` citation resolved and read; every quoted measurement traced to its report; every
computable value computed. **Pass 1 fixes applied to LEARN_LOOP.md: 2026-07-26.**

**Closed and removed from this file (the record lives where the decision lives, not here):**

**O-2 · O-3 · O-4 — closed 2026-07-26**, in one session but at **three different ceremony levels**,
which was the point of batching them:
- **O-2** (C5's field list short in four places, and missing the `gate_failed` status) — an
  **incomplete description**: one truth written down only partially. Filled in at all four sites,
  each labelled **"as built, not frozen"** in its own text. **C5 was NOT frozen** — that session
  still needs inference at the table. The `gate_failed` consequences that outlive documentation
  (three-value enum · nullable `adapter_dir`/`base_model_hash` · eligibility-by-log-replay) are
  written into the [storage charter](../services/storage/CHARTER.md) model-directory row, since
  storage is where they land. No D-number.
- **O-3** ("LoRA over all layers") — an **intent/build gap**, not an error: the intent stands in
  [ARCHITECTURE.md](../ARCHITECTURE.md) §Decisions, the build (LLM projections only, vision towers
  excluded, 252/252 parity) is stated in [continuum's charter](../services/continuum/CHARTER.md),
  and the gap is named as a deliberate open item — with the two facts that shape any revisit
  (flipping back is cheap; the exclusion's premise expires if DP ever feeds the trainer pixels).
  No D-number: a build was documented against a standing intent, nothing decided or reversed.
  **The O-3 inventory was short by two:** [VISION.md](../VISION.md):68 and :104 also said "all
  layers", and :68 — *"v0 mechanism (**locked**)"* — was the strongest as-though-built assertion in
  the repo. Both fixed in the same pass. A `grep -rn "all layers" --include=*.md` would have caught
  them at pass 1; pass-2 inventories should sweep the repo, not just the engineering docs.
- **O-4** (C2 `record_id` discriminator) — **prose lagging its own authoritative schema**. The
  frozen schema was already correct; only ARCHITECTURE's summary lagged. **Not a contract change**,
  and not ceremonied as one. No D-number.

Full write-ups in LEARN_LOOP §3 (C2 + C5) and §8 items 3 and 12; session record in
[handoff/engineering.md](../handoff/engineering.md).

Also closed: the pass-1 doc-defect table (all ten fixed in LEARN_LOOP.md) and **O-1, timezone** — decided *and
built* on 2026-07-26 as **D17**: the capturing device reports `device_tz` per chunk on C1 → carried
verbatim by DP into C2 → storage columns → continuum's renderer; storage's per-user `home_tz` is
scheduling + fallback only; UTC stays canonical. See [HANDOFF.md](../HANDOFF.md) Decisions log
**D17**, [handoff/engineering.md](../handoff/engineering.md), and LEARN_LOOP §8.4. That work also
closed storage OQ3, DP OQ4, DP OQ9 (substantially), continuum OQ10's timezone half, and E-4's
premise.

**Suite numbers, current as of 2026-07-26 post-D17 — quote these, not older figures:**
DP **770 passed + 21 skipped** · continuum **189 passed + 7 skipped** · recording **144** ·
storage **32** · extension deno **11**.

---

## ⬜ OPEN — service charter / canvas hygiene

Found underneath the review. None are LEARN_LOOP's fault; all are real.

| # | File | Defect |
|---|---|---|
| **O-5** | [data-processing/CHARTER.md](../services/data-processing/CHARTER.md) | Header says **"Last updated: 2026-07-09"** but the file carries a section stamped *"Ratified 2026-07-25 (WS-VC)"* plus rewritten OQ10/OQ13/OQ14. Violates [ORG.md](../ORG.md) §Rules "Stamp your work". |
| **O-6** | [data-processing/CHARTER.md:64](../services/data-processing/CHARTER.md) | Contract table says `record_id` deterministic on `(chunk_id, pipeline_version)` while **line 100 of the same file** states `sha256(chunk_id ␀ pipeline_version [␀ discriminator])`. Self-contradictory. |
| **O-7** | [data-processing/docs/record-emission-law.md:3-4](../services/data-processing/docs/record-emission-law.md) | Status reads *"ready for the lead to fold into `CHARTER.md`"* — **the fold already happened** (CHARTER §Record-vs-mutation law). |
| **O-8** | [data-processing/HANDOFF.md:151-153](../services/data-processing/HANDOFF.md) | Says *"**widen** the production `dp_caption_ungrounded_quote_total` counter"*, implying wired-but-narrow. It is declared + seeded to zero and **incremented nowhere** (`app/main.py:179-184`, `:223`; no `.inc()` in `app/`). Should read "**wire**". |
| **O-9** | [platform/HANDOFF.md](../services/platform/HANDOFF.md) | **Last updated 2026-07-09** — oldest canvas in the repo. Zero mention of the D9 observability backbone D15 assigned to platform, zero mention of E-3(b) naming platform as owner. ORG's cold-start guarantee fails here. |
| **O-10** | [recording/HANDOFF.md](../services/recording/HANDOFF.md) + [HANDOFF.md:17](../HANDOFF.md) | Both say **"120 tests"**; actual is now **144** (was 133 at pass 1; D17 added 11). Fix both canvases to 144. |
| **O-11** | [HANDOFF.md](../HANDOFF.md) (board) | The service-status row says **"DP suite 765"**, an older current-state entry says **"DP 173"**, and the 2026-07-26 D17 entry says **770** — one board, three numbers. **770 + 21 skipped is the correct current figure**; reconcile all three sites. |

---

*(**O-12 fixed 2026-07-26** — D17's headline overstated one clause. The row now splits its status
explicitly: the **timezone split is BUILT + verified**, the **watermark-window clause is DECIDED,
NOT BUILT** (`window_for()`/`closed_window_before()` are still local-date and `nightly.py` still
calls them). [ARCHITECTURE.md](../ARCHITECTURE.md)'s C10 row was reworded the same way. Both now
carry the blocking design question the reviewer asked for: `window_id` is the local start date and
keys the day-log, the cycle journal, C5's `training_window`, and publish's alias monotonicity — the
natural watermark key is the window's **end instant** (monotone per user, no dateline case), but that
changes the `w2026-07-21` format and forks adapter lineage, so it is a board call, not a refactor.
Routed to the storage/C10 board session with the day-log move.)*

---

## Coverage limits of pass 1

Stated so breadth isn't mistaken for completeness:

- Phase-3 and parity **statistics** are traced to their reports, not recomputed.
- `ws-video-clip.md` is 2,513 lines; the ~15 claims LEARN_LOOP draws from it were verified, not
  the whole document.
- Not exercised at pass 1: any real capture, GPU, or node-7 fleet state. *(Partially superseded
  2026-07-26: the D17 session restarted the node-7 learn fleet, verified both SQLite migrations
  against the live DBs, and drove a real `--smoke` capture run end to end. GPU and real client
  capture remain unexercised.)*
- Read but not line-audited: input/output/inference charters, the extension and phone clients,
  `stagegraph/executor.py` internals (SlotView, permit-at-dispatch), §7's decision narrative.
