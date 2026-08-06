# DP Rebuild — Stage D worklog (Ledger v2)

**Stage:** D — Ledger v2 · **Status:** IN PROGRESS · *Dated:* 2026-08-06
**Branch:** `dp-rebuild-v1` · **Plan:** [refactor_dp_service.md](refactor_dp_service.md) §8 Stage D
**Laws this stage:** §1 L7/L8 (ratified as D27) — the done-ledger, the four verdicts,
heal budgets, heal containment. §4's crash table becomes fully executable in T-5.
**Scope:** WP-D0 (inherited nits from Stage C's verification) · WP-D1 (journal done-row
v2) · WP-D2 (dedup L8 claim tree + seam wiring) · WP-D3 (heal/hole/server-call metrics +
full T-5 + e2e heal drill).

Carried-over instructions honoured this stage (from Stage C "Noticed" + its cleanup
round): `GraphResult.statuses` already speaks L8's `ok|failed|cancelled` and is dropped
at the seam — WP-D1 persists it; the journal model is the CORRECTED one (v0 journals in
BOTH modes; pending rows are async-only — `journal.accept` runs only on the async path);
the ledger touchpoints are `journal.accept/unaccept/mark_processed/mark_dead_letter/
processed_record_ids` + `DedupStore.claim_for_async`'s trichotomy; server-call metrics
are WP-D3's (cleanup §C "Owner assigned"); `test_t5_ledger_flows.py` holds the Stage C
subset and names what this stage owes.

---

## Pre-flight notes

- v0 lives in its worktree (`/home/ubuntu/nmn/dp-v0-live`, `:8085`) and is untouched all
  stage; the processes on `:8083`/`:8084`/`:8097` likewise. The two stray onboarding
  files stay uncommitted.
- The journal schema is DP-internal (`var/` is data, not contract): the done-row
  extension needs no contract ceremony. The D16 wire (202/200/503 shapes) is untouched
  by construction — asserted per WP.
- `HEAL_MAX_ATTEMPTS` is a code pin (3), never an env knob; the T-1 operational-env
  allowlist test must stay green unchanged.

## WP-D0 — inherited nits from Stage C's verification (one mechanical commit)

| File | Action | Why |
|---|---|---|
| `docs/refactor_stage_C.md` | edited (append-only) | dated corrections section: "all three real-stage matrices" → two (the first matrix is mock-registry); "matrix-proven inert" for `VLM_*` → allowlisted-not-matrix-varied (no cell varies them); "31 fields" → v0's `VisionSettings` has 49, enumeration covered 46 + the non-field `VIDEO_CLIP_PROMPT`; the missing `VIDEO_OCR_URL/_TIMEOUT/_THREADS` disposition added (subsumed by manifest endpoints + `client_timeout_s` + the ocr server's 4-thread code pin). Plus a dated "Noticed" bullet pointing at cleanup §C's server-call → WP-D3 assignment |
| `tests/test_clipcap.py` | edited | the `{pass="caption"}` gap closed: `test_render_truncates_at_the_span_budget_on_a_sentence` now asserts the truncated flag `True` once (and `False` under-cap); NEW `test_caption_truncation_increments_the_real_family` drives the real stage at span 2 (cap 32 < the 79-char canned reply) and asserts `dp_video_truncated_total{pass="caption"} 1` renders under main.py's exact declaration |
| `tests/test_t1_determinism.py` | edited (comment only) | the allowlist comment carried the same "matrix-proven" over-claim — now states allowlisted, not matrix-varied |
| `app/vision/delta.py` | edited (docstring only) | the `Delta.accum` note cited the deleted `dp_video_delta_peak`; now names its real consumer (`scripts/calibrate_delta.py`) and the deletion |
| `app/stagegraph/executor.py` | edited | the synthesized body-CancelledError RuntimeError now chains `__cause__` from the caught CancelledError, so the traceback names the stage's actual raise site |
| `CHARTER.md` | edited | §Slot Law L5's ALL-CAPS "EMITTED" → STYLE-rule-5 italics; Last-updated stamped + dated changelog bullet (wording only, no rule change) |

Evidence: `pytest tests/test_clipcap.py -q` → `19 passed` (was 18: +1 caption-metric
test; the flag assertions ride the existing truncation test); full DP suite →
`526 passed, 3 skipped` (Stage C exit was 525+3; the skips are the DP_E2E gate).
No red-first ceremony here: the `{pass="caption"}` producer already existed (wired in
Stage C's cleanup) — the nit was the missing TEST, and both new assertions pin behavior
that now exists (one first-run failure was mine, not the code's: the under-cap assertion
forgot `render_caption` prefixes app/activity; fixed to `endswith`).

## WP-D1 — journal done-row extension (ledger v2)

| File | Action | Why |
|---|---|---|
| `app/journal.py` | extended | `processed` gains `stage_status` (JSON, the L8 map verbatim — failed vs cancelled distinct), `heal_attempts`, `done_final`, `cached_slots` (SPECIFIED AND UNPOPULATED — the run-only-the-failed-cone seat, schema only), `superseded_pv`; `HEAL_MAX_ATTEMPTS = 3` code pin; `_migrate` (additive ALTER TABLE for pre-D DBs); `mark_processed` grows `statuses=`/`heal=` and returns the budget state `{heal_attempts, done_final, newly_final}` with all heal/vf arithmetic in its one transaction; NEW `heal_failed` (increment + finalize-at-budget + epoch-guarded pending clear, truth unguarded); NEW `done_row` (the claim tree's single lookup); `processed_record_ids` reimplemented over `done_row` (one lookup path); `pending_for_redrive` returns `(rows, finalized)` — over-cap chunks WITH a durable record are FINALIZED (pending cleared, `done_final` set), never dead-lettered |
| `app/ingest_core.py` | edited (2 lines) | `process_chunk` persists `result.statuses` into the done-row — the `GraphResult.statuses` dropped-at-the-seam carry closed |
| `app/main.py` | edited (lifespan) | unpacks the new tuple; force-finalized chunks logged loudly (WP-D3 wires the permanent-holes metric at this site) |
| `tests/test_journal.py` | extended (TDD) | 9 new tests: done-row v2 fields; legacy NULL-statuses row; heal increments only while holes remain (green heal: no increment); budget exhaustion finalizes with `newly_final` exactly once; `heal_failed` never regresses done + finalizes at budget + no-row → None; epoch-guarded pending clear with unguarded truth; version-forward `superseded_pv` + budget reset (latest supersession only); pre-D in-place migration; redrive-cap containment (durable-record chunk finalized, record-less poison still dead-letters) |

In-session decisions (the brief's explicit your-call items, logged):

- **Done-row keying: per `(chunk_id)`, latest attempt wins, superseded dialect recorded
  in the row** (`superseded_pv`, latest supersession only — the full lineage lives in
  storage's records and git history; a `(chunk_id, pv)` key would grow the ledger per
  deploy and buy nothing the claim tree reads). L8's own text says the new record lands
  beside the old IN STORAGE; the ledger is a claim tree input, not an archive.
- **Schema evolution: migrate (additive ALTER TABLE), not recreate.** `var/` is
  disposable pre-cutover, but dropping `processed` rows would un-SEE intact history at
  continuity rehydration — fabricated gaps, the exact lie the journal exists to prevent.
  A pre-D row reads `stage_status` NULL = no hole evidence = green (skip) when the
  dialect matches; any v0-dialect row version-forwards anyway.
- **Ledger `heal_attempts` counts non-green heals only** (a fully green heal clears to
  skip-state without an increment; a failed re-run and a still-holey re-run both count).
  The WP-D3 metric `dp_heal_attempts_total` counts ATTEMPTS (every heal re-run) — the
  two deliberately differ; the ledger column is the budget, the metric is the rate.
- **Crash-loop cap × heal containment**: `pending_for_redrive`'s over-cap flip now
  splits on "does a durable record exist" — a crash-looping heal claim force-finalizes
  (holes permanent, visible, budget spent) instead of dead-lettering a chunk whose
  record exists. A record-less poison chunk dead-letters exactly as before. This is the
  containment rule "a heal never dead-letters a chunk that has a durable record"
  applied to the one code path that could violate it.
- **`heal_failed`'s increment is deliberately not epoch-guarded** (only the pending
  delete is): a failed heal truly ran regardless of which delivery's worker ran it —
  the same truth-is-unguarded posture `mark_processed`'s INSERT already has.

DEVIATION (packaging, loud): the brief's WP-D1 bullet includes the dedup claim tree;
plan §8 puts the claim tree in WP-D2 beside the seam wiring. Followed §8 — the tree and
its routing are one seam and land together in WP-D2, keeping every WP commit green
(dedup.py is untouched by this commit and the old trichotomy still runs against the
extended journal via the kept `processed_record_ids`). Substance unchanged.

Evidence: TDD red first — `pytest tests/test_journal.py -q` → `10 failed, 12 passed`
against the shipped journal (TypeError on `statuses=`, ImportError `HEAL_MAX_ATTEMPTS`,
AttributeError `done_row`, tuple-unpack on `pending_for_redrive`); after implementation
→ `22 passed`. Full DP suite → `535 passed, 3 skipped`.

