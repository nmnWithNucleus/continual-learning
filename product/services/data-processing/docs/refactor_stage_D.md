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

## WP-D2 — the L8 claim tree + seam wiring

| File | Action | Why |
|---|---|---|
| `app/dedup.py` | REWRITTEN (the §9 verdict) | the five-verdict tree (`Claim` dataclass + `classify`): no row → fresh; stored pv ≠ current → version_forward; green ∨ done_final ∨ budget-exhausted ∨ pre-D NULL-statuses → skip; holes ∧ budget → heal; the async guard owns case 5 (inflight). Decided ONLY from the journal's done-row (`row_lookup`), never a storage read. `_done` now caches STABLE skips only — a healable chunk is re-judged from the ledger every delivery. `claim_for_async(chunk_id, current_pv)` returns a `Claim`; `put` gains `green=` (holey ships aren't skip-cached); the one-lock discipline (`lock_for` inline, guard async) unchanged |
| `app/ingest_core.py` | edited | `process_chunk` gains `heal=` (routes the receipt through the journal's budget arithmetic; the run itself is UNCHANGED — full graph off this delivery's envelope, one POST, L3 re-derives the same record_id so the upsert replaces holey with fuller); NEW `heal_chunk` — the inline containment wrapper (any failure → `journal.heal_failed` → 200 + existing ids, never a raise); `_note_heal_outcome` shared hook (WP-D3 wires metrics into it); GREEN-only dedup caching |
| `app/ingest_queue.py` | edited | heal jobs keep the worker's transient-retry loop; at the give-up point they branch to `_heal_failed` (budget charge + epoch-guarded pending clear + claim release — never `_dead_letter`, never a continuity `gaps` mark); one heal claim charges at most ONE increment (attempts count this chunk's own failed heals, never deliveries or retries); falls back to the normal taxonomy only when no done-row exists |
| `app/main.py` | edited | `DedupStore(row_lookup=journal.done_row)` (the old `done_fallback`+`_current_pv` version-compare callback deleted — the tree owns it with the handler's freshly-resolved pv); inline handler routes skip (fast-path + under-lock re-judge) / heal (`heal_chunk`, contained, 200) / fresh+version_forward (unchanged path); async handler routes skip/inflight/claimed off the `Claim` and enqueues `heal:` flagged jobs; `_redrive_pending` claims with the current pv and re-drives a crashed heal AS a heal |
| `tests/test_dedup_claim.py` | REWRITTEN (TDD) | 14 tests: every tree branch (incl. tree-order — version check beats holes/final; legacy NULL statuses; current_pv None posture; budget-exhausted belt), stable-skip caching vs heal re-judging (lookup call counts), green-vs-holey `put`, and the async claim lifecycle ported to `Claim` (heal claims ride the same inflight discipline — a mid-heal redelivery re-ACKs 202) |
| `tests/test_heal_seam.py` | NEW (TDD) | 6 seam tests: inline heal fills the hole with the SAME record_id then skips; inline heal failure (required stage down) answers 200 + existing id, charges budget, never dead-letters; exhaustion finalizes then pure-skips (no POST, no blob pull); still-holey heal re-POSTs byte-identical wire bytes (the §5.1 no-op upsert input); async heal rides the queue with the D16 202 body byte-identical; async heal failure — no dead-letter, no gap, claim released, next redelivery heals |
| `tests/test_t5_ledger_flows.py` | edited (1 test) | the Stage C-era skip test ran against this module's always-failing optional stage — under L8 that redelivery is now correctly a HEAL, so the skip pin patches the stage healthy first (case 3 demands all-green); noted in the test docstring |

In-session decisions:

- **The claim's `row` is advisory; budget arithmetic lives in the journal's own
  transactions.** A heal claim raced by another worker's completion re-runs against the
  CURRENT prior row inside `mark_processed`/`heal_failed` (BEGIN IMMEDIATE), so a stale
  claim can inflate nothing.
- **Async heal failures keep the worker's transient-retry loop** before charging the
  budget: one delivered heal claim = at most one `heal_attempts` increment, after its
  own retries exhaust. Inline heals run once (parity with the inline fresh path, which
  also never retries).
- **A heal's reply rides D16 unchanged**: inline heal success AND failure both answer
  `200 {ok, record_ids:[...]}` — `dp_acked ⇔ durably written` holds on both (the record
  IS durably written; a failed heal's answer is the existing record). Async heal claims
  ACK the byte-identical `202 {ok, accepted, chunk_id}`; mid-heal redeliveries the
  byte-identical duplicate 202.
- **`dp_ingest_total{result}` vocabulary unchanged** (skip → `deduped`, heal completion
  → `processed`): heal visibility arrives with WP-D3's dedicated families rather than a
  new result label — dashboards are Stage F's, and the existing labels stay honest.

Evidence: TDD red first — the claim-tree unit tests fail collection against the shipped
`dedup.py` (ImportError `Claim`), which masked the seam tests' own red, so the seam file
was additionally verified by stash-revert of the four app files: `6 failed` against the
pre-D2 seam (heal not routed: reprocess-not-skip, 500-not-200, dead-letter-not-contain),
`6 passed` restored. Full DP suite → `551 passed, 3 skipped` (+16 net: 14 rewritten
dedup tests replacing 5, +6 seam, +1 t5 edit).

