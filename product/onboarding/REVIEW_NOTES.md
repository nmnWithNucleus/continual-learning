# LEARN_LOOP.md — accuracy review, running notes

> Working file for the founders' review of [LEARN_LOOP.md](LEARN_LOOP.md). Findings are removed
> from this file as they are fixed, so **whatever is still written here is still outstanding.**
> Delete this file when it empties.

**Review pass 1:** 2026-07-26 — read the doc end to end, then re-derived its claims from source.
Every `file:line` citation resolved and read; every quoted measurement traced to its report; every
computable value computed. **Pass 1 fixes applied to LEARN_LOOP.md: 2026-07-26.**

**Closed and removed from this file (the record lives where the decision lives, not here):**
the pass-1 doc-defect table (all ten fixed in LEARN_LOOP.md) and **O-1, timezone** — decided *and
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

## ⬜ OPEN — doc fixes waiting on the C5 freeze session

Both are "one phrase, several files". Cash them together when C5 freezes.

### O-2 — C5's field list is short in four places

Code writes nine fields (`publish.py:86-90`): `contract, user_id, adapter_version, adapter_dir,
base_model_hash, training_window, recipe_id, eval_report, status`.

| File | Current text |
|---|---|
| [ARCHITECTURE.md](../ARCHITECTURE.md) C5 row | "user_id, version, base-model hash, training window, eval report, status" |
| [continuum/CHARTER.md:68](../services/continuum/CHARTER.md) | "user_id, adapter version, base-model hash, training window, eval report, status" |
| [storage/CHARTER.md:46](../services/storage/CHARTER.md) | "version, base-model hash, training window, eval report, active/rolled-back" |
| [continuum/app/publish.py:5-7](../services/continuum/app/publish.py#L5-L7) | its own docstring omits `adapter_dir` + `recipe_id` |

### O-3 — "LoRA over all layers" is wrong in three places

Code targets only the LLM projection linears, vision towers excluded (252 modules = 7 projections
× 36 layers) — [morpheus/train.py:29-31](../services/continuum/app/morpheus/train.py#L29-L31).

- [ARCHITECTURE.md](../ARCHITECTURE.md) §Decisions — "Personalization | **LoRA per user, all layers**"
- [continuum/CHARTER.md](../services/continuum/CHARTER.md) mission line 20 — "a LoRA job over all layers of the BWM"
- [continuum/CHARTER.md](../services/continuum/CHARTER.md) scope line 38 — "Per-user LoRA over **all layers** of the BWM (v0 decision)"

### O-4 — ARCHITECTURE's C2 prose omits the discriminator (minor)

The frozen schema already mandates it; only the prose summary still says "deterministic on
`(chunk_id, pipeline_version)`". Schema is authoritative, so nothing is unfrozen — fold this edit
in with O-2.

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
