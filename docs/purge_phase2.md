# Old-world surface purge — Phase 2 worklog

> **Executed.** Phase 2 of 4. Does not start Phase 3.
>
> **Branch:** `cursor/purge-phase2-dp` · **Base:** `main` after merging Phase 1 gate doc.

---

## Authorities (quoted)

### Authority (i) — `docs/purge_classification.md` on `cursor/purge-phase1-classification-6ce5` (PR #1)

> This document is a gate, not an execution. … Every DELETE / REWRITE / SURGICAL EDIT below is a **proposal**; the founder and the verifying co-founder rule on each before Phase 2 touches anything.
>
> **Policy being applied** (founder, 2026-08-07): the working tree is the teaching surface for future newcomers; git history is the archive. … **Never touch `.git`, never rewrite history, never force-push.**

Merged to the running world as `072eb5f` (fast-forward of `origin/cursor/purge-phase1-classification-6ce5`).

### Authority (ii) — FOUNDER RATIFICATION (2026-08-08) — wins on conflicts

> Classification ratified with amendments.
> 1. `c2_processed_record.v0.json` is EXEMPT, not deleted — the parity proof's P1 precondition loads it by `$id` and a green storage test runs that proof in-process. Record the owed follow-up on storage's board: retire the whole v0 parity apparatus in ONE later act …
> 2. The parity apparatus is KEEP-EXEMPT with that retirement condition. `seam_check`'s v1 rewrite is ordinary engineering, not purge work — and fix ONLY its c2_record builder and POST sites; its C12/C14 v0 assertions are CURRENT contracts, do not touch them.
> 3. The append-only laws are NOT overruled. They are SUSPENDED FOR THIS STAGE … (D29) … The license LAPSES the day the loops run end to end in production …
> 4. Register corrections: D3 reclassed OLD-spent … D18 reclassed MIXED … fix D12's stale 'until C10 lands' watch-out; D26's rewrite must attribute `DP_DIALECT_FREEZE`'s retirement to the no-knobs law (L4/D25), not L9; D16's rewrite states BOTH halves … D15's rewrite keeps the clause the platform board cites; D28's keeps 'latest updated_at, rowid tiebreak' verbatim.
> 5. Census: add `ingest_time` (+beside-build, fresh-forward) as token families, with storage's live RENAME COLUMN migration line sweep-EXEMPT. Disposition `style_baseline.json` (regenerate at the end). Delete the untracked `platform/deploy/learn.env.pre-stage-f.bak` DELIBERATELY — recorded that git does not hold it and it is therefore gone for good; its rollback window is closed.
> 6. Rescues before removals: add the silent-overwrite rationale to DP charter L5 BEFORE any §Condensed history deletion … Name the four covered-in-place rationale homes as sweep-exempt …

---

## Housekeeping

| Step | Evidence |
|---|---|
| Onboarding edits (`field-guide.html`, `review_actions.md`) | Already landed on `main` at `afe0103` ("commit the DP field-guide + review_actions AS-IS"). Working tree had **no uncommitted delta** for those files at session start. Later surgically rewritten in WP-2B. |
| Merge PR #1 / Phase 1 branch | Fast-forward `main` ← `origin/cursor/purge-phase1-classification-6ce5` → `072eb5f` (`docs/purge_classification.md`). |
| Delete `product/services/platform/deploy/learn.env.pre-stage-f.bak` | **Deleted deliberately.** File was **untracked** — git never held it. Gone for good; rollback window closed. |

Fleet before work: all of `:8083 :8084 :8085 :8161 :8121–8152` → **200**.

---

## WP-2A — Founders' act (one commit)

**Commit:** `1197bc6` — `founders' act D29: suspend append-only for this stage`

- Verified next free number: **D29** (D28 was tip).
- Card written in STYLE idiom matching the absent-`frozen` clause (license / lapse / not a third status).
- Citing amendments landed simultaneously:
  - `DECISIONS.md` §Stage + pointer line
  - `ORG.md` doc-nature cell + writing-modes cell
  - `STYLE.md` §Growing a worklog (rule kept; suspended-for-this-stage clause added)
  - `storage/CHARTER.md` OQ preamble (subject matter left → remove whole, leave a hole)
  - `README.md` pointer line

Pointer line (verbatim):

> History before 2026-08-08 lives in git history, not in this tree. A skipped number is a removed row, not an error.

**Follow-on register commit:** `3d2739c` — ruling 4 corrections (D3 removed OLD-spent; D18 MIXED; D12/D15/D16/D26/D28; D23–D28 drop rebuild-plan pointers before DP doc deletes).

---

## WP-2B — The DP service

### (1) Rescues — `81d531e`

- L5 silent-overwrite rationale added **before** §Condensed history deletion.
- §4 transplants in DP files: OQ4 v0 aside removed; OQ10 OCR bar inlined (≥0.85 recall / ≤0.10 CER); OQ13 dropped `ws-async` pointer + stated both D16 halves; HANDOFF Gotchas inline-path reason kept.

### (2) `docs/` deletes — `81ea672`

Deleted (inert: no fleet/runtime import; no green-test import):

- `docs/refactor_dp_service.md`
- `docs/refactor_stage_{A..G}.md` (7)

### (3) CHARTER rewrite — `9990346`

First charter of the only world: no §Condensed history, no transition changelog, Slot Law as the law, OQ3 `:8161` owed edit landed, OQ10/12/13/14 timeless, L5 silent-overwrite kept.

### (4) `handoff/` deletes + fresh board — `ad61898` + `a90b4aa`

Deleted (inert): `handoff/worklog.md` + ten `ws-*.md` files.

`HANDOFF.md` rewritten today-state; Next includes client live-stream testing + parity-retirement **pointer** to storage §Next 7.

**Storage board** (`storage/HANDOFF.md` §Next 7): owed one-act retirement of the v0 parity apparatus recorded per ruling 1.

### (5) Onboarding — `a90b4aa`

`field-guide.html` + `review_actions.md`: transition framing and dead stage-doc links stripped; teach the running world.

### (6) scripts / tests / servers — `8633a66`, `b6843a6`, `9dbfd2f`

| Delete | Inertness |
|---|---|
| `scripts/calibrate_delta.py` | No imports; knob gone (L4); `delta.py` docstring reworded |
| `scripts/vlm_probe.py` | Standalone; successor `test_vlm_boot_probe.py` |
| `servers/drill_stage_b.py` | Stage docs co-deleted |

Comment/docstring sweep cleared narrative tokens. A bad paren-eating transform briefly corrupted `servers/*.py`; restored from pre-sweep and re-applied comment-only cleanups (`9dbfd2f`). `dashboards/` KEEP. `readings/` KEEP (flag).

---

## Exit criteria

### Token census inside `product/services/data-processing/`

Narrative families at **ZERO** (excl. `LOCK.json`, `readings/`):  
`Stage [A-G]`, `WP-[A-G]n`, `WS-*`, `ingest_time`, `beside-build`, `fresh-forward`, `VidProc`, `refactor_*`, `dp-rebuild-v1`, `OD-[123]`, emission-law, `ProcessedUnit`, pre-rebuild/pre-cutover/v0-world.

### Exemptions remaining (ruled)

| Exemption | Where / why |
|---|---|
| Parity apparatus + `c2_processed_record.v0.json` | Outside DP tree; KEEP-EXEMPT (ruling 1). Not touched. |
| `app/vision/prompts/LOCK.json` | Content-addressed lock archive — KEEP |
| `readings/*.md` | Source material (generic "keyframe" usage) — KEEP |
| `dashboards/*.json` | Census-clean / current — KEEP |
| Negative guards / resurrection lists | `discriminator`, `enrichments`, `processed_at`, `SlotView`, `mutable_slots`, `best_effort`, `sidecar`, `R1_EXEMPT_*` in schemas/tests/stage.py — current prohibitions |
| L4 inert-env matrix literals | `VIDEO_*`, `INGEST_ISOLATION`, `DP_DIALECT_FREEZE` string literals in `test_t1_determinism.py` (+ tombstones in `main.py`/`config.py`) |
| L12 "keyframe-like structures" | Current law text (`CHARTER`, `test_t4_slot_law.py`) |
| Journal `processed_at` column name | Live SQL column in journal tests / `journal.py` (not the retired C2 field) |
| Metric / fixture names `dp_video_*`, `_VIDEO_FIXTURE` | Runtime identifiers, not dead knobs |
| Four covered-in-place rationale homes | journal / dedup / ingest_queue epoch-guard comments — explanations kept, stage refs reworded |
| Storage RENAME COLUMN `ingest_time` migration | Outside DP; sweep-EXEMPT (ruling 5) |

### Suite tails

| Suite | Tail | Notes |
|---|---|---|
| data-processing | **569 passed, 4 skipped** | Unchanged count — no co-deleted DP tests this phase (platform `test_cutover_wipe` is Phase 3) |
| servers/common | **30 passed** | Recreated local `.venv` after accidental corruption; not committed |
| storage | **354 passed** | Untouched-green |
| continuum | **264 passed, 7 skipped** | Untouched-green |
| recording | **144 passed** | Untouched-green |

### Style ratchet

`python3 product/scripts/style_check.py --baseline` → regenerated.  
`python3 product/scripts/style_check.py` → **no regressions**.

### Fleet after

All of `:8083 :8084 :8085 :8161 :8121–8152` → **200**.

---

## Register corrections — scope note

Ruling 4 applied in this phase (`3d2739c`). Remaining DECISIONS pointers into `handoff/engineering.md` (D10/D17/D19/D20/…) belong with the product-level purge (Phase 3+), recorded here so they are not lost.

---

## STOP items

None. No disposition was disagreed with; ratification won on D26 attribution (L4/D25, not L9) and on c2 v0 EXEMPT.

---

## Commits on `cursor/purge-phase2-dp` (after Phase 1 merge)

1. `1197bc6` founders' act D29  
2. `3d2739c` register corrections (ruling 4)  
3. `81d531e` DP rescues  
4. `81ea672` delete rebuild docs  
5. `ad61898` delete handoff ws-* + worklog  
6. `9990346` rewrite CHARTER  
7. `a90b4aa` fresh board + onboarding + storage Next 7  
8. `8633a66` delete inert scripts  
9. `b6843a6` surgical comment sweep  
10. `9dbfd2f` restore servers/ + style baseline (partial)  
11. *(this session close)* style README polish + final baseline + this worklog  

Phase 3 **not started**.
