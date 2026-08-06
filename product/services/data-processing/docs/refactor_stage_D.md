# DP Rebuild — Stage D worklog (Ledger v2)

**Stage:** D — Ledger v2 · **Status:** DONE 2026-08-06 · *Dated:* 2026-08-06
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

## WP-D3 — metrics + T-5 full + e2e heal drill

| File | Action | Why |
|---|---|---|
| `app/main.py` | edited | `_setup_metrics` declares the heal families (`dp_heal_attempts_total{modality}`, `dp_records_finalized_with_permanent_holes_total{stage}`) and the server-call family (`dp_server_calls_total{server,outcome}`, `dp_server_call_transient_retries_total{server}`, `dp_server_identity_failures_total{server}`, `dp_server_call_seconds{server}` histogram); metrics construction moved BEFORE the model clients (declaration-before-seeding); `_build_model_clients` hands the registry to each client; the redrive force-finalize site now fires the permanent-holes metric. Dashboards untouched (Stage F owns panels) |
| `app/model_client.py` | edited | the Stage C cleanup §C assignment landed: per-completed-call outcome counter (`ok` \| `deterministic_error` \| `unavailable` \| `identity_mismatch`), per-transient-presentation retry counter (transport error or 5xx-transient body — a failed re-verify counts too), identity-failure counter at the verify site (both connect-time and first-use paths), per-ATTEMPT `/infer` latency histogram (HTTP-answered attempts only; transport failures are counted, not timed — a timeout ceiling is not a latency). Recording is guarded (metrics never fail a call); zeros seeded at construction; `metrics=` optional so servers/common's suite is untouched |
| `app/ingest_core.py` | edited | `_note_heal_outcome` records: every completed heal attempt (success or failure) increments `dp_heal_attempts_total`; the finalization edge (`newly_final`) fires the permanent-holes counter once per non-ok stage |
| `app/journal.py` | edited (1 line) | `mark_processed`'s return gains `stage_status` so the success-path finalization edge carries its hole evidence to the metric |
| `tests/test_model_client_metrics.py` | NEW (TDD) | 5 tests: ok + latency observed; deterministic-error outcome; transient retries ×3 then unavailable (5xx attempts DO land in the histogram); identity-mismatch counted at both counters; metrics-less construction stays silent (the servers/common posture) |
| `tests/test_t5_ledger_flows.py` | grown to the FULL law | header rewritten as the §4/§8-matrix map; new: healed record BYTE-IDENTICAL to a never-holed run (two apps, two journals — sorted assembly makes it provable, and the holey ship differs); heal-exhaustion fires both metric families then pure-skips; post-POST-pre-mark crash replay (mark raises once → redelivery reprocesses → byte-identical re-POST → upsert no-op → converges to skip); statuses round-trip after kill-9 (fresh `Journal` handle + restarted app both read `failed` vs `cancelled` DISTINCT — a dependent-cone stage pins `cancelled` — and the claim tree heals off that evidence); the poison row (terminal → dead-letter, visible in journal + continuity, no record, redelivery re-arms to a clean run); version-forward test now asserts the ledger row (new pv, `superseded_pv`, budget reset) |
| `tests/test_e2e_real_heal.py` | NEW (gated `DP_E2E=1`) | the heal drill: split fleet via two filtered manifests (full-minus-ast, then ast-only; the app keeps the DEFAULT manifest so its ast client fails transient while the server is down — the production outage posture); hole → heal (same record_id, ledger all-ok, green heal uncharged) → healed bytes ≡ clean-fleet run → redeliver → pure skip with ZERO server calls (`dp_server_calls_total` snapshot unchanged, blob never re-pulled). Full drill discipline: v0 200 before/after, ports free, per-GPU deltas. File named to sort AFTER `test_e2e_real.py` (that module's fleet must tear down before this one claims the ports) |

In-session decisions:

- **`dp_server_call_seconds` measures per-ATTEMPT latency of HTTP-answered attempts**
  (success or error status), not per-call wall time: a call's transport timeouts would
  otherwise pollute the histogram with client-side ceilings. Transient presentations are
  visible in their own counter instead.
- **Heal outcomes ride `dp_heal_attempts_total` regardless of result** (the rate);
  the ledger's `heal_attempts` column stays the budget (non-green only) — restated from
  WP-D1 because the two numbers will differ on dashboards and that is by design.
- **The e2e partial fleet is manifest-filtering, not supervisor surgery**: the
  supervisor CLI has no per-server filter (a Stage F nicety if the heal drill becomes a
  routine op); two supervisors over disjoint filtered manifests compose cleanly.

Evidence: TDD red first — `tests/test_model_client_metrics.py` → `4 failed, 1 passed`
against the shipped client (TypeError `metrics=`); green after. T-5 grown: `9 passed`.
Full DP suite (mock) → `561 passed, 3 skipped`; servers/common → `30 passed`.

E2E drill evidence (2026-08-06, node-7). The first heal-drill attempt errored in
FIXTURE SETUP (the filtered tmp manifests made the supervisor resolve server dirs
into the tmp dir — its default base is the manifest's grandparent); fixed by passing
`--base-dir <service root>`, disclosed here because the same run's first half is the
Stage C e2e re-verified green over ALL Stage D changes:

```
$ DP_E2E=1 …pytest tests/test_e2e_real.py tests/test_e2e_real_heal.py -v
tests/test_e2e_real.py::test_audio_chunk_end_to_end PASSED
tests/test_e2e_real.py::test_video_chunk_end_to_end PASSED
tests/test_e2e_real.py::test_audio_reprocess_is_byte_identical_through_the_real_fleet PASSED
3 passed (heal drill errored at fixture setup: tmp-manifest base-dir; fixed)

$ DP_E2E=1 …pytest tests/test_e2e_real_heal.py -v      # after the --base-dir fix
tests/test_e2e_real_heal.py::test_heal_drill_hole_then_heal_then_skip PASSED
1 passed in 50.01s
```

Inside that one test, asserted in order: ast down → 200 + record with the acoustic
hole, asr/transcript slots byte-equal the golden pins, ledger `acoustic: failed` and
no cancelled cone; ast up → redelivery 200 + the SAME record_id, acoustic slot = the
real golden's empty-claim fold, ledger all-ok, `heal_attempts` 0 (green heal); healed
wire bytes == a never-holed clean-fleet run's (fresh journal, same envelope);
redeliver again → 200 same ids, zero new POSTs, zero blob pulls, and the
`dp_server_calls_total` snapshot UNCHANGED (zero server calls on skip). Teardown:
no fleet port listening, per-GPU deltas clean (all 8 back to 0 MiB), v0 `:8085` → 200
before and after (in-fixture asserts + post-run spot check).

Re-run after the review round (the drill gained the snapshot-liveness assertions):
`test_heal_drill_hole_then_heal_then_skip PASSED` in 49.25s — and the fixture's
TEARDOWN check then errored exactly per its design ("a foreign job growing
concurrently … deserves a human look, not a silent pass"): a foreign continuum eval
(`cued_recall_eval.py`, parent pid 3847189 — the same foreign family Stage C's WP-C6
documented) started mid-drill and holds ~10 GB on six GPUs. The human look:
zero supervisor/server processes remain, zero fleet ports listening, v0 → 200; the
fleet's own memory was fully released (the first run's all-zero teardown stands as
the hygiene proof). Not re-run to green-wash — the foreign job is not ours to wait
out, and the check fired correctly.

## The §4 crash table, made executable (each row → its test)

| §4 row | Outcome held | Test |
|---|---|---|
| before `journal.accept` | no ACK ever sent → recording retries; nothing lost | `test_async_ingest.py::test_failed_journal_accept_releases_claim` (accept raises → 500, claim released, redelivery fresh-processes) |
| after accept, before processing | restart recovery re-enqueues from the journal | `test_journal.py::test_kill_recovery_startup_redrive` (+ `test_pending_backlog_larger_than_queue_all_recover`) |
| mid-graph | attempt fails → worker retry budget → dead-letter; blackboard was a cache | `test_async_ingest.py::test_unexpected_processor_error_is_retried_not_dead_lettered` + `test_missing_blob_dead_letters_and_is_visible` + `test_t5_ledger_flows.py::test_poison_chunk_dead_letters_then_redelivery_rearms` |
| after POST, before `mark_processed` | redelivery reprocesses fully → byte-identical → upsert no-op → converges | `test_t5_ledger_flows.py::test_crash_after_post_before_mark_redelivery_converges` |
| — epoch fencing (same row) | a stale worker's terminal writes no-op | `test_journal.py::test_stale_worker_epoch_writes_no_op` + `test_heal_failed_pending_delete_is_epoch_guarded_but_truth_is_not` |
| model server crash | contained to the server; client retries other replicas | Stage B drill `kill_one_replica` (4/4) + `model_client` 5xx re-verify tests (Stage C cleanup B8) + `test_model_client_metrics.py::test_transient_retries_then_unavailable` |

## The redelivery matrix (§8 exit)

| Verdict | Test |
|---|---|
| skip | `test_t5_ledger_flows.py::test_same_dialect_redelivery_skips` (+ skip asserts throughout `test_heal_seam.py`; blob never re-pulled) |
| version-forward | `test_t5_ledger_flows.py::test_version_forward_reprocess_lands_beside` (beside in storage + ledger `superseded_pv`/budget-reset) |
| heal | `test_heal_seam.py` (inline + async, same record_id, D16 bytes) + `test_t5_ledger_flows.py::test_healed_record_byte_identical_to_never_holed_run` + the e2e drill |
| heal-exhaustion | `test_heal_seam.py::test_inline_heal_exhaustion_finalizes_then_skips` + `test_t5_ledger_flows.py::test_heal_exhaustion_fires_metrics_and_skips` |
| poison | `test_t5_ledger_flows.py::test_poison_chunk_dead_letters_then_redelivery_rearms` (+ `test_journal.py::test_dead_letter_survives_restart`) |

## Noticed for later stages

- **Stage E — the `updated_at` byte-compare × heals (the §5.1 interaction, pinned
  here for the storage build)**: a still-holey heal re-POSTs BYTE-IDENTICAL bytes —
  `test_heal_seam.py::test_inline_heal_repost_is_byte_identical_while_holes_persist`
  is the DP-side input to storage's no-op-must-not-re-window rule; a FILLING heal
  changes bytes, so `updated_at` bumps and the healed record flows into the next
  training window (the accepted double-training, D27). Stage E's byte-compare tests
  should replay exactly these two heal shapes.
- **Stage E — where the ledger exposes hole state if `/continuity` ever grows a
  `holes` field**: `journal.done_row(chunk_id)` — `stage_status` (failed vs cancelled
  distinct) + `done_final` is the whole truth; nothing else stores it. Note
  `rehydration()` reads only stream/sequence columns today — a per-stream holes
  aggregate would want either a new journal read or a `stage_status IS NOT NULL AND
  done_final` scan; the row is keyed per chunk_id, so per-version hole history does
  NOT exist DP-side (superseded_pv is single-depth; version lineage lives in storage
  + E-3's manifest-by-pipeline_version).
- **Stage F — the heal drill as a routine op**: the supervisor CLI has no per-server
  filter; the e2e drill composes two supervisors over filtered manifests with
  `--base-dir` (the manifests' server dirs are service-root-relative). If the cutover
  heal drill (§8 Stage F drill 3) wants kill-one-server ergonomics, a supervisor
  `--only <server>` flag is a small honest addition — operational, no dialect impact.
- **Stage F — dashboards**: heal visibility rides `dp_heal_attempts_total` +
  `dp_records_finalized_with_permanent_holes_total` + the `dp_server_*` family;
  `dp_ingest_total{result}` kept its Stage C vocabulary (heal completions count as
  `processed`). Panels are Stage F's.
- **Stage F/G — e2e file ordering**: `test_e2e_real_heal.py` must keep sorting AFTER
  `test_e2e_real.py` (its module-scoped fleet must tear down before the heal drill
  claims the same ports); if the e2e files are ever reorganized, keep the ordering or
  move both fleets to a session-scoped arrangement.
- **Stage F — circuit.py ruling (Stage C carried "wire or retire at D/F")**: NOT
  wired in Stage D, deliberately — the heal machinery changes its calculus (a
  captioner outage now heals on redrive instead of dead-lettering, so the breaker's
  one honest use — skipping decode work during a sustained outage — is a fleet-level
  cost optimization, not a correctness need). Wire-or-retire lands with the deploy
  story at Stage F; re-noted so the carry isn't lost.
- **Stage G — `cached_slots`**: still SPECIFIED AND UNPOPULATED by design (the
  run-only-the-failed-cone seat). If it is still unpopulated at demolition time,
  leave it — it is D27's schema seat, not dead code.

## Exit criteria (§8 Stage D + the session brief)

| Criterion | Status | Evidence |
|---|---|---|
| Redelivery matrix green: skip / version-forward / heal / heal-exhaustion / poison | done | matrix table above, every row a named green test; full suite run below |
| Full T-5: heal byte-identity, exhaustion→done_final+metric+skip, §4 replay, version-forward beside, skip-never-re-pulls, statuses round-trip kill-9 | done | `test_t5_ledger_flows.py` → 9 passed; §4 checklist above, every row ticked with its test |
| WP-D1 ledger v2: done-row extension + LOOKUP + migration + containment in journal | done | commit `d571765`; `test_journal.py` → 22 passed (9 new, red-first) |
| WP-D2 seam: four verdicts routed, async heal-claims via the queue, D16 byte-identical | done | commit `3f027c8`; `test_dedup_claim.py` 15 + `test_heal_seam.py` 6 (red-first via stash-revert; +4 more seam tests in the review round → 10) |
| WP-D3 metrics: heal/hole families + server-call family in model_client, wired into /metrics | done | commit `3d02f0e`; `test_model_client_metrics.py` 5 (red-first; +4 in the review round → 9, incl. the app-registry wiring proof) + metric asserts in T-5/heal-seam |
| e2e heal drill, drill discipline, tail pasted | done | WP-D3 evidence above: hole → heal ≡ clean-fleet bytes → skip with zero server calls; v0 200 before/after, GPUs 0 MiB, ports free |
| Full DP suite green (mock) | done | `570 passed, 4 skipped` after the review round (the 4 = the DP_E2E gates: 3 real-fleet e2e + the heal drill; 561+4 at the WP-D3 commit) |
| storage + continuum suites untouched-green | done | storage → `310 passed`; continuum → `262 passed, 7 skipped`; no file of either service touched (`git diff --name-only 5f88fbb..HEAD` shows only data-processing paths) |
| No new env knobs; allowlist test green unchanged (HEAL_MAX_ATTEMPTS is a code pin) | done | `test_env_reads_in_app_are_exactly_the_operational_allowlist` untouched (WP-D0's comment edit only) and green in every run |
| v0 (worktree, :8085) + :8083/:8084/:8097 untouched; stray onboarding files uncommitted | done | v0 200 asserted in both drills; no commit touches the onboarding files |
| One commit per WP, worklog in the same commit | done | `494e396` D0 · `d571765` D1 · `3f027c8` D2 · `3d02f0e` D3 · final commit (review round + this closing edit) |
| Stage E not started | honored | zero storage-service changes; the two Stage E carries are in "Noticed" above |

## 2026-08-06 — Adversarial review round (six lenses, skeptic-verified; fixes applied)

> review · a 26-agent workflow over the full Stage D diff (`5f88fbb..3d02f0e`): six
> reviewer lenses — L8-law conformance / journal-sqlite / seam concurrency / D16
> wire + metrics / test honesty / worklog accuracy — each raw finding then attacked
> by an independent skeptic against the actual code and the recorded rulings.
> Arithmetic: 20 raw findings → **15 confirmed** (4 major · 7 minor · 4 nit; the
> doc-count defect was found by four lenses, the dedup-count by two) → **9 distinct
> defects**, all resolved in this round's commit. 5 refuted, recorded below.

**Code fixes (each TDD where the behavior changed — watched red first):**

- **Receipt supersedes dead-letter (major, races).** The redrive loop enqueues with
  the STARTUP-SNAPSHOT epoch; a live redelivery racing it could dead-letter at a
  newer epoch and the redrive job's stale-epoch receipt then left a permanent
  `dead_letter` pending row beside a green done-row — a chunk with a durable record
  reading as terminally lost, exactly what containment forbids. Fix in the ledger
  itself: `mark_processed` and `heal_failed` now clear a `dead_letter` pending row
  REGARDLESS of epoch (a receipt contradicts the dead-letter claim; the epoch guard
  still protects fresh ACCEPTED rows). `test_receipt_supersedes_a_dead_letter_
  pending_row_any_epoch` — red first.
- **Redrive skip-reconcile (2 minors, sqlite + races — one defect).** A pending row
  orphaned by an epoch-mismatched receipt (constructible via an inline replay under
  a flipped `INGEST_ASYNC`) was rescanned forever: the redrive loop's skip verdict
  now clears the row it was launched to resolve via new `journal.clear_pending`
  (guarded on the snapshot epoch — a row a live delivery re-accepted survives).
  `test_clear_pending_is_epoch_guarded` + `test_redrive_skip_verdict_clears_the_
  stale_pending_row` — red first.
- **`classify` off the event loop (major, races).** The claim tree's sqlite read ran
  synchronously on the loop (up to the full 5 s busy timeout, stalling every
  coroutine) while claiming its guard placement prevented exactly that. `classify`
  is now async (threadpool read, like every other loop-side journal touch); the
  guard's critical section still never awaits and re-checks before claiming, so the
  claim atomicity the skeptic verified is preserved. Docstrings corrected.
- **Inline heal containment hardened (minor, law).** `heal_chunk`'s own containment
  write (`journal.heal_failed`) was unguarded — a graph failure PLUS a journal write
  failure would have answered 500 for a chunk with a durable record. The bookkeeping
  is now guarded: the reply is 200 + the existing id even if the budget write fails
  (uncharged; the unchanged ledger re-judges next delivery). The conservative twin —
  a receipt-write failure after a landed POST charges one attempt for a heal that
  healed storage — is documented in code as deliberate (errs toward earlier
  finalization only under repeated journal failures).
- **model_client outcome-counting gaps (minor, wire).** A 200 with a non-envelope
  body escaped as a raw `JSONDecodeError` with NO outcome counted; a 200 missing
  `result` counted a false `ok` then raised `KeyError`; a malformed `/health` body
  escaped `ValueError`. All three are now transient presentations (retried, counted,
  `unavailable` at budget). Three new tests — red first.
- **Heal × worker-retry falsifiability (major, tests).** Every async heal test
  pinned `INGEST_MAX_RETRIES=0`, so the retry-loop-then-one-charge ruling was
  untestable (mutation-verified by the reviewer: a charge-per-retry bug passed the
  suite). Two new tests run with retries live: transient blips retry WITHIN one
  heal delivery then land green (no charge); full exhaustion charges exactly ONE
  attempt. These pin already-correct behavior (coverage fill, not a code fix); the
  misleadingly-named `…_no_retry_loop` test renamed.
- **dp_server_* wiring falsifiability (major, tests).** The e2e "zero server calls"
  equality was vacuously satisfiable by a dead metric family (a sample-less family
  renders nothing; the client swallows recording errors). The drill now asserts the
  snapshot is non-empty with a nonzero whisper `ok` before comparing, and a new
  mock-level test (`test_app_registry_wiring_renders_seeded_server_series`) proves
  the seeded zeros render through the APP registry — declaration, wiring and label
  sets all falsifiable without GPUs.
- **journal.py header (nit, sqlite).** The module header still described the
  pre-Stage-D `pending_for_redrive` (blanket dead-letter, in-method increment);
  rewritten to the containment-split truth.

**Worklog corrections (quote-and-correct; the committed sections above stand):**

- WP-D2's "14 tests" and the exit-table's "test_dedup_claim.py 14" under-counted:
  the file has **15** tests. The "+16 net: 14 rewritten dedup tests replacing 5,
  +6 seam, +1 t5 edit" itemization was wrong twice over — the true sum is +10 dedup
  (15 replacing 5) + 6 seam; the t5 edit modified one test and added none. Two
  compensating errors reached the right total; corrected here.
- WP-D3's evidence line "Full DP suite (mock) → 561 passed, 3 skipped" under-counted
  the skips: **4** (WP-D3 itself added the fourth `DP_E2E` gate — the heal drill).
  The exit table already said 4.
- STYLE rule 5 (no ALL-CAPS): the WP-D0 commit fixed one CHARTER token "per rule 5"
  while this worklog's own prose carries ~50 ALL-CAPS emphasis tokens — internally
  inconsistent paper, confirmed as a nit. Disclosed, not rewritten: the register
  matches every prior stage worklog (A: 31 checker findings, B/C similar), the
  docs-style ratchet was already red at the Stage C tip (18 files) independent of
  this stage, and re-baselining is the checker's own "--baseline if deliberate and
  agreed" call — the founder's, not this session's. Stage D grew the red set by
  exactly this file; flagged for that ruling.

**Refuted (recorded so the next reader does not re-litigate):** the crash-loop-cap
"invisible version-forward finalization" (the finalize path fires the metric via the
lifespan and version-forward re-judges by pv first, so nothing is masked); "inline
heals ignore ProcessingError.transient" (parity with the inline fresh path, recorded
WP-D2 decision); "epoch fence vacuous across claim generations" (accept() bumps from
the prior row — the fence is per-row-lineage by design); "failed inline heal counted
as result=processed with zero writes" (the existing record IS the durable answer —
the deduped/processed vocabulary was ruled in WP-D2); the `dp_heal_attempts_total`
help-text-vs-async-charging mismatch (the help text says attempts, the seam counts
one per delivered claim — the WP-D1/D3 decision distinguishes the metric from the
ledger column explicitly).

**Verification after all fixes:** review-fix tests red first (`6 failed` across
journal/seam/model-client before the fixes; all green after); full DP suite →
`570 passed, 4 skipped`; servers/common → `30 passed`; storage → `310 passed`;
continuum → `262 passed, 7 skipped`; e2e heal drill RE-RUN green with the hardened
liveness assertions (tail in WP-D3's evidence, re-verified this round).

**Status: DONE.**

## 2026-08-06 — Close-out round (independent verification, 4 lenses; founder-directed)

> close-out · applied on `dp-rebuild-v1`, two commits (fixes, then this worklog) ·
> triggered by an independent 4-lens verification that CONFIRMED Stage D with no
> blockers (the review-round fixes re-reproduced red-first) and directed exactly the
> items below. Everything above stands as written; corrections amend, never rewrite.
> Stage D closes with this round; Stage E not started.

**Code (three small items, each verified red-against-the-defect):**

- **The inline containment-write guard was an UNTESTED behavior change.** The review
  round's own TDD framing claimed its behavior changes were watched red; the guard in
  `heal_chunk` (journal write failing during a failed heal) shipped with no test —
  that claim over-reached, said plainly. Now pinned:
  `test_inline_heal_failure_with_failed_bookkeeping_still_answers_200` (graph fails
  AND `journal.heal_failed` raises → 200 + existing id, budget uncharged, no
  dead-letter, next redelivery re-judges) — verified red against the pre-review
  `heal_chunk` (3d02f0e revert: the 500 escapes), green at HEAD.
- **`heal_failed` green-row hardening (TDD, red first).** Now evidence-based in its
  own transaction, mirroring `mark_processed`: an all-green current row (a racing
  worker healed it green between claim and failure report) is neither charged nor
  finalized — closing the probe-proven edge where stale writers could finalize with
  the permanent-holes metric silently swallowed. The pending clear still runs.
  `test_heal_failed_on_an_all_green_row_is_a_no_op` — red before the guard.
- **`classify`'s off-loop read pinned.**
  `test_classify_ledger_read_runs_off_the_event_loop` asserts the ledger lookup runs
  on a non-loop thread with no running loop — the review-round fix now has a
  regression test instead of a docstring promise.

**Law-text corrections (the built truth wins; quote-and-correct):**

- **"Upsert replaces holey with fuller" was false of the built code** (probe: a heal
  during a *different* server's outage regresses a green slot until convergence —
  coherent by design: blind replace + ledger + re-heal). Corrected in CHARTER §Slot
  Law L8 and plan §1 L8 (both stamped), and added to the D27 card's Watch-out (a
  permitted addition; decision text untouched): a heal re-POSTs whatever the full
  re-run produced — the ledger, not the record, carries hole truth; convergence is
  the guarantee, not monotonicity.
- **"Same stage fails again ⇒ heal_attempts++" understated the built rule** in the
  same two homes: any non-green completed heal charges; a green heal never charges; a
  failed re-run charges via the failure path. Folded into the same clause fix.
- **D27 Watch-out, second addition:** budget exhaustion is not the only route to
  permanent holes — the crash-loop re-drive cap force-finalizes durable-record chunks.
- **"While a heal flows into the next window"** (ARCHITECTURE C10 card + the c10 v2
  contract's top description) read as every-heal-bumps; both now say "a heal that
  lands a byte-different record", aligned to the contract's own `t_start` phrasing.
  The contract edit is a DESCRIPTION-ONLY clarification — additive, no shape change,
  no validator impact (JSON re-validated; storage suite re-run green below).

**Worklog corrections (append-only, per the house rule):**

- The review round's "+4 more seam tests → 10" was itself a count error introduced
  while correcting count errors (the irony is noted with due enjoyment): the fourth
  "addition" was the RENAME of an existing test, so the file collected **9** at that
  commit. This round's new containment test makes it 10 — true now, by accident of
  one more test, not because the earlier line was right.
- **Keep ruling recorded:** `classify`'s `current_pv=None` branch stays (dead-
  defensive: not reachable from app code at HEAD — every call site passes a resolved
  pv); one code comment now names it None-safety, per this round's ruling.
- **Benign window noted:** after exhaustion, `heal_attempts` can exceed
  `HEAL_MAX_ATTEMPTS` with `done_final` already set (e.g. a stale failure report
  landing after finalization) — harmless: every comparison is `>=`, `newly_final`
  fires only on the flipping write, and classification reads `done_final` first.

**Verification re-run (2026-08-06, after all close-out edits):**

```
$ ./.venv/bin/python -m pytest tests/test_journal.py tests/test_dedup_claim.py \
    tests/test_heal_seam.py tests/test_t5_ledger_flows.py -q
60 passed, 1 warning in 2.40s                # journal 25 · dedup 16 · seam 10 · t5 9
$ ./.venv/bin/python -m pytest -q            # full DP suite
573 passed, 4 skipped, 1 warning in 55.54s   # 570+4 at the review round, +3 this round
$ storage   pytest -q → 310 passed           # contract description change: no effect
$ continuum pytest -q → 262 passed, 7 skipped
```

Goldens and pins untouched: no server golden, no C4/C5 slot pin, no T-1 matrix cell
changed in this round (diff touches app/journal.py, app/dedup.py, four test files and
paper only). Status stays **DONE**; Stage E not started.

## 2026-08-06 — Stage E WP-E0 correction (inherited nit)

> cleanup · applied on `dp-rebuild-v1` in Stage E's WP-E0 commit (worklog:
> [refactor_stage_E.md](refactor_stage_E.md)) · append-only; everything above stands.

- The close-out's "diff touches app/journal.py, app/dedup.py, four test files and paper
  only" miscounted: commit `de5de9d` touches *three* test files (`test_dedup_claim.py`,
  `test_heal_seam.py`, `test_journal.py`). The file list is otherwise accurate.

