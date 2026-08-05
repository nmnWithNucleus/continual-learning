# DP Rebuild — Stage A worklog (Ratify & cut paper)

**Stage:** A — Ratify & cut paper · **Status:** IN_PROGRESS · **Date:** 2026-08-05
**Branch:** `dp-rebuild-v1` · **Plan:** [refactor_dp_service.md](refactor_dp_service.md) §8 Stage A
**Scope:** WP-A1 (D-rows D-R1…D-R6 · CHARTER §Slot Law · ARCHITECTURE C2/C10 cards),
WP-A2 (`contracts/c2_processed_record.v1.json` · `contracts/c10_daylog.v2.json`, README
contract-edit order respected). **Zero runtime change.**

---

## Pre-flight notes

- **No prior stage worklogs exist.** `docs/refactor_stage_*.md` matched nothing repo-wide;
  Stage A is the first executed stage, so there are no carried-over "Noticed for later
  stages" instructions to honour.
- The working tree carried two uncommitted files from another session
  (`onboarding/field-guide.html`, `onboarding/review_actions.md`). They are not part of
  Stage A and are left uncommitted and untouched.
- The plan doc itself was untracked; it is committed with this worklog's skeleton so the
  branch carries its own source of truth.

## Work packages

### WP-A1 — D-rows, Slot Law, ARCHITECTURE cards

| File | Action | Why |
|---|---|---|
| `product/DECISIONS.md` | modified | new "Drafted — awaiting ratification" section between the Stage note and the register: index table + six cards D-R1…D-R6 (plan §7) |
| `product/services/data-processing/CHARTER.md` | modified | §Record-vs-mutation law replaced by §Slot Law (L1–L12 + dead-concepts list, plan §1); status line stamped, dated bullet added |
| `product/ARCHITECTURE.md` | modified | C2 card restructured around the v1 shape (v0 named as the running wire); C10 card gains a v2 delta block; both gain a dated How-it-got-here entry; index rows + Last-updated stamped |
| `product/HANDOFF.md` | modified | escalation row + card **DP-A** — the Stage A sign-off ask (the ORG escalation path, and the "note in HANDOFF.md" ARCHITECTURE requires for §Contracts changes) |

### WP-A2 — contract schemas (README contract-edit order respected)

Order held: §Contracts cards first (WP-A1 commit), then `contracts/`, then the owning
canvases — all below in one commit.

| File | Action | Why |
|---|---|---|
| `product/contracts/c2_processed_record.v1.json` | created | C2 v1 per plan §2: one record per chunk, slots map, no discriminator/enrichments/kind, root modality, strict at every level |
| `product/contracts/c10_daylog.v2.json` | created | C10 day-log v2 per plan §5.2: slot-walk sources, `(chunk_id)`/`updated_at` dedup, `updated_at` window axis; `window_id` trap closure carried verbatim |
| `product/contracts/README.md` | modified | file count 12→14, two table rows, dated section explaining why these schemas legitimately precede their validators (ORG "contracts before fan-out") |
| `product/services/data-processing/HANDOFF.md` | modified | canvas leg of the contract-edit order: rebuild-in-flight note atop §Next; stamp |
| `product/services/storage/HANDOFF.md` | modified | canvas leg: §Incoming block for joint rows D-R5/D-R6; E-2 row cross-referenced; stamp |
| `product/services/continuum/HANDOFF.md` | modified | canvas leg: C10 v2 flag in §Cross-service flags (stamp-refusal is the transition net); stamp |

## In-session decisions (plan ambiguities)

- **D-rows land as a separate `drafted` section, not register rows.** DECISIONS.md admits five
  status words and founders-only rows; the plan (§7, a founders'-session artifact) instructs
  drafting at Stage A. Resolution: a clearly-delimited "Drafted — awaiting ratification"
  section above the register, statuses `drafted`, with a banner saying the rows bind nothing
  until a founders' session ratifies and re-numbers them. Honors both "status must never
  over-claim" and the plan's instruction.
- **`D-R` ids kept verbatim.** The plan names them D-R1…D-R6; final D-numbers are the
  founders' to assign at ratification. The drafted-section banner says so.
- **CHARTER §Slot Law replaces §Record-vs-mutation law on the branch** rather than sitting
  beside it: two live laws in one charter would contradict each other, the branch is itself
  the pending-sign-off artifact, and the retired statement survives verbatim in
  `docs/record-emission-law.md` until Stage G (D-R1's own watch-out). The section banner
  states drafted/not-yet-executable status explicitly.
- **ARCHITECTURE cards describe both worlds.** Versioning ceremony ("bump version, new
  `*.vN.json`, edit the card") normally fires when a version ships; here paper leads build
  (ORG "contracts before fan-out"). Cards mark v1/v2 `designed` and v0/v1 `built`-and-running
  until Stage F, so no status over-claims.
- **CHARTER §On C2 left untouched.** It describes the v0 wire, which is literally true today
  (D10's shape clause is superseded only on ratification). The full charter rewrite is
  Stage G's; noted below for that stage.
- **`device_clock` added to C2 v1 `source{}`.** The plan says the D17 trio rides `source{}`
  verbatim, and the DP charter's D17 rules name the trio as `device_clock` + `device_tz` +
  `device_utc_offset_minutes` — yet the v0 schema omitted `device_clock` (a standing
  prose/schema gap). v1's "provenance verbatim from C1 (minus modality)" is read as closing
  it. Founder can strike the field at sign-off if the omission was intended.
- **`source{}` excludes C1's transport-only fields** (`sequence`, `codec`, `blob_sha256`,
  `blob_bytes`), matching v0's precedent; "verbatim from C1" is read as the provenance
  fields, not the transport envelope. `device_location` passthrough kept from v0.
- **Root `modality` keeps the full C1 enum** (`audio|image|video|text`) although the v1 stage
  set covers audio + video: the enum mirrors C1's single-modality chunks, and image/text
  become producible additively when their pipelines land (D15 deferral unchanged).
- **The six §2-pinned slot sub-schemas are typed now** (`asr`, `diarization`, `transcript`,
  `acoustic`, `caption`, `ocr`): the plan's example pins their shapes, Stage C builds against
  them, and `additionalProperties:false` on the map is what makes an unknown slot fail
  closed. Later slot types still land additively per the plan's own rule. A `$defs.ref_slot`
  pins the L5 binary-rides-refs shape without naming a slot (no producer exists).
- **Every slot carries its producer's dialect segment as `version`** (as in the plan's §2
  example), with a shared `$defs.slot_version` grammar — deliberate redundancy with
  `pipeline_version` so a consumer holding one slot can name its producer.
- **Fixed-width trap closures carried forward:** `record_id` gets 64-char bounds (the c10
  `window_id` precedent from contracts/README); `pipeline_version` is variable-width, so the
  schema documents that enforcing implementations must `fullmatch`. The regex pins segment
  grammar only; sortedness is T-3's job (not expressible in a pattern).
- **An empty `content.slots` map is legal.** All-optional-failure under L7 ships a record
  whose dialect states what was attempted; forbidding `{}` would force a fabricated slot.

## Deviations from the plan

- **DEVIATION (additive): canvas + founders'-board edits.** §8 Stage A names only D-rows,
  §Slot Law, the two cards and the two schemas — but WP-A2's own parenthetical invokes the
  README contract-edit order, whose third leg is "then both canvases", and ARCHITECTURE's
  header requires a HANDOFF.md note for §Contracts changes. Both obligations were honoured
  (three service canvases + the DP-A escalation card). Nothing else on those boards was
  touched.
- **DEVIATION (deferred obligation, plan-sanctioned): D22 same-session teaching-view
  correction.** The onboarding field guide still teaches the emission-law world and now lags
  the drafted §Slot Law. D22 demands same-session correction; the plan schedules the rewrite
  at Stage G instead. Flagged here rather than silently accepted — D22's repo-wins rule
  covers the gap, and the guide is mid-edit by another session (uncommitted changes left
  alone).

## Design questions for the founder

- **Does the v2 day-log want an `asr` fallback when `slots.transcript` is a permanent
  hole?** §5.2 routes speech lines from `slots.transcript.splits[]` only. A chunk whose
  dialect attempted `speaker_align` but holed it — including a heal-budget-exhausted
  permanent hole — renders with no Heard lines even though `slots.asr` carries the text.
  That is consistent with L11 (a renderer must not fabricate from a slot the record does not
  carry — the schema documents exactly this), but it silently drops real speech from the
  training corpus in the permanent-hole case. Not improvised around; Stage E should build
  whatever is ruled. The v2 schema needs no change either way.

## Test evidence

Schema validity + example/negative-case validation (scratchpad script
`validate_stage_a_schemas.py`, run with storage's venv — jsonschema Draft 2020-12):

```
$ product/services/storage/.venv/bin/python …/scratchpad/validate_stage_a_schemas.py
[PASS] c2 v1 is a valid Draft 2020-12 schema
[PASS] c10 v2 is a valid Draft 2020-12 schema
[PASS] plan §2 audio example record validates against c2 v1
[PASS] video example (caption + ran-and-empty ocr) validates
[PASS] all-optional-failed record (empty slots) validates
[PASS] discriminator is rejected
[PASS] enrichments is rejected
[PASS] content.kind is rejected
[PASS] unknown slot name fails closed
[PASS] modality inside source is rejected (root-level now)
[PASS] record_id with trailing newline is rejected (64-char bounds)
[PASS] uppercase/short record_id rejected
[PASS] unsorted-grammar garbage pipeline_version rejected
[PASS] stage editing another's slot shape (transcript missing speaker) rejected
[PASS] sample v2 day-log body validates against c10 v2
[PASS] version '1' body rejected by v2 schema
[PASS] window_id with trailing newline rejected (17-char bounds)

17 passed, 0 failed
```

Zero-runtime-change proof — no code file touched (`git diff --name-only main...` shows only
`*.md` + `*.json` under `product/`), and all three affected services' suites re-run green
with the new schema files in place (storage's loader glob-scans `contracts/*.json`, so the
additive files do load):

```
$ cd product/services/storage        && .venv/bin/python -m pytest -q
310 passed, 1 warning in 14.73s
$ cd product/services/data-processing && .venv/bin/python -m pytest -q
788 passed, 21 skipped, 1 warning in 67.28s (0:01:07)
$ cd product/services/continuum       && .venv/bin/python -m pytest -q
262 passed, 7 skipped in 13.47s
```

(The DP board's "765 (+21)" count predates Stage A; 788 is main's current count, unchanged
by this branch.)

## Noticed for later stages

- **Stage C** — `schemas.py` mirrors are cut there ("one change, four parts"); the six slot
  sub-schemas in `c2_processed_record.v1.json` are the pinned shapes to mirror. Sortedness
  of `pipeline_version` is unenforceable in the schema regex: T-3 must own it. Mock backends
  appear by name in the version string (plan §3), so the determinism matrix (T-1) needs
  mock-dialect fixtures distinct from real ones.
- **Stage E** — storage pins the `window_id` length-bounds trap "in one test" across three
  schema files; `c10_daylog.v2.json` is now a fourth carrier and must join that test. The
  renderer must answer the Heard-lines design question above before WP-E2. E-2's
  whole-record redesign supersedes the kind-aware design named on storage's board §Next
  item 1 (cross-referenced there).
- **Stage F** — pick the actual bumped `daylog_format_version` / `recipe_id` values at
  cutover; continuum's stamp-refusal (built, F3) is the net. The wipe (OD-2) covers
  `/context` + DP journal only; `/raw` and `/sessions` stay.
- **Stage G** — CHARTER §On C2, §Position table, §Scope rows and the OQ texts still describe
  the v0/kind world (left deliberately; true until cutover). ARCHITECTURE §Vocabulary rows
  (`record`, `dialect`, `discriminator`) likewise. The onboarding field guide rewrite (D22).
  `docs/record-emission-law.md` deletes with condensed history; decide then whether the v0
  schema files (`c2_processed_record.v0.json`, `c10_daylog.v1.json`) retire with it — the
  README's "never mutate in place" rule doesn't say when a superseded file may leave.
- **Ratification bookkeeping** — on sign-off, the founder assigns final D-numbers to
  D-R1…D-R6, moves the cards into the register proper, and the `(DECISIONS.md §Drafted)`
  cross-references in ARCHITECTURE/CHARTER/canvases should be re-pointed in the same
  session.

## Exit criteria (§8 Stage A)

| Criterion | Status | Evidence |
|---|---|---|
| Founder sign-off on the plan doc + schemas | pending | — |
