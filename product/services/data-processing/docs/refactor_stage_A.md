# DP Rebuild — Stage A worklog (Ratify & cut paper)

**Stage:** A — Ratify & cut paper · **Status:** BLOCKED, all WPs complete, awaiting founder
sign-off (founders' board escalation DP-A) · *Dated:* 2026-08-05 · cleanup round 2026-08-06
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
- **Resolved 2026-08-06, ruled in.** Speech lines render from `slots.transcript`; when that
  slot is absent they fall back to `slots.asr`, speakers unlabeled. Stage E builds it — see
  §Cleanup round below.

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

(The DP board's "765 (+21)" count predated Stage A; 788 is main's current count, unchanged
by this branch. The board's stale figure was corrected in the review round below.)

Adversarial review round (multi-agent: six reviewer dimensions — decisions / charter / C2
pair / C10 pair / cross-doc / scope — each raw finding independently re-verified by a
skeptic agent against the plan, the worklog and the house rules):

```
14 raw findings → 7 confirmed, 7 refuted
```

Confirmed and fixed before the final commit (all paper, zero runtime):

- DECISIONS.md D-R2 index lineage cell used one verb for three fates; now reads
  "supersedes D10 (shape clause) · restates D16 (fan-out clause) · retires D19
  (discriminator clause)" per plan §7 — the superseded/RETIRED distinction is load-bearing
  in the register's own vocabulary. The same cell's three bold spans also broke STYLE
  rule 5; now one.
- `c2_processed_record.v1.json` slots description had restated L12 without its
  "keyframe-like structures" qualifier, turning it into a blanket rule the file's own
  `splits[]` sub-schemas would break; qualifier restored with the splits distinction made
  explicit. Validation re-run: 17 passed, 0 failed.
- `product/HANDOFF.md` Last-updated stamp had not been bumped for the DP-A row (board
  claimed 2026-07-27 currency while carrying a 2026-08-05 item); stamped.
- `product/HANDOFF.md` §Escalations preamble claimed every row was WS-VC-opened and "not a
  build blocker", both false for DP-A; preamble now separates the two origins.
- DP board status line carried the stale "765 tests" figure through a rewrite of that very
  line while the founders' board said 788 in two places; corrected to 788 (+21 skipped,
  re-run 2026-08-05).

Refuted (recorded so the next reader does not re-litigate them): the `drafted` status word
(it is the plan's own vocabulary, quarantined outside the register's five-word table); the
charter banner's "retired law" phrasing (attributive, directly under a "Drafted…awaiting
ratification" opening); record-emission-law.md's header still naming the charter section it
extracts (frozen transitional state, Stage G's to collapse); the continuum flag glossing C2
v1 under a D-R5/D-R6 citation; `$defs.ref_slot.meta` being an open object (validation-inert
until a named slot references it — tightening is the referencing producer's additive edit);
`transcript.splits[].speaker` nullability (typed sub-schema elaboration is exactly Stage A's
assigned work); and the stage worklog living in `docs/` beside the plan rather than in
`handoff/worklog.md` (the timeline entry is owed when the item leaves the board, per that
file's own header).

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
- **Ratification bookkeeping (added 2026-08-06)** — at sign-off, also stamp
  `docs/record-emission-law.md`'s Status line superseded → CHARTER §Slot Law, so the extract
  stops describing a charter section that no longer exists on this branch.
- **Stage C (added 2026-08-06)** — extend T-3/T-4: each emitted slot's `version` must equal
  its stage's segment of `pipeline_version`, so the schema's deliberate redundancy can never
  drift.

## 2026-08-06 — Cleanup round (independent review + founder rulings)

> cleanup · applied on `dp-rebuild-v1`, two commits (fixes, then this worklog) · triggered by
> an independent 12-agent review (6 lenses, skeptic-verified) plus four founder rulings.
> Everything above this section stands as written; corrections below amend, never rewrite.

**Founder rulings applied**

- **`processed_at` dropped from C2 v1** (schema `required` + `properties`, plan §2 example,
  C2 card shape and rules): a wall-clock field inside the record breaks the §5.1 byte-compare
  (every reprocess would re-window) and makes T-1 unpassable. Processing latency moves to DP
  `/metrics`. Plan §4 now states the byte-identical claim holds by construction; recorded on
  the D-R2 card.
- **Heard-lines fallback ruled in**: speech lines render from `slots.transcript`; when that
  slot is absent, from `slots.asr` (speakers unlabeled). Added to the c10 v2 schema (root
  description + `segments.asr`), the D-R6 card, plan §5.2 and the C10 card's v2 deltas;
  §Design questions above is resolved by it. Stage E builds it.
- **`device_clock` stays** in C2 v1 `source{}`; the D-R2 card now names the D17 trio riding
  verbatim, citing the charter's D17 rules — the register-vs-charter/schema drift is closed
  on the card, not just in the schema.
- **Empty `content.slots` stays legal** — the in-session decision above is endorsed; no
  change.

**Review findings applied** (by file)

- Plan §7 D-R2 gains D8's disposition: partially supersedes the shipped two-record shape, the
  specialist-OCR-feeds-the-caption one-liner surviving; mirrored in the drafted index cell
  and the card status line (two bolds).
- DECISIONS.md: D-R3/D-R4 no longer quote the law verbatim — both bullets now summarize and
  cite L4/L9, so D-R1's "restated nowhere" claim is true of its own section; D-R4's lineage
  cell names `isolation.py`; D-R2's slots bullet split so `modality`-to-root is its own idea.
- CHARTER.md: §On C2 repointed to the v0 schema and the retired law's extract (its old target,
  §Record ids, was replaced by §Slot Law); L5/L7/L9 overflow clauses split into sub-bullets.
- ARCHITECTURE.md: the C10 v2 block folded into that card's **Rules** with `designed` in place
  of an ad-hoc "drafted" status word; the E-2 and D-R2-header double-em-dash sentences fixed;
  the C2 source rule now names the transport-field exclusion; C2 shape and rules drop
  `processed_at`; the C2 watch-out's dead-concepts claim narrowed (same defect as review
  item 22, third site — disclosed here since the item named only the schemas).
- Boards: founders' §Where-we-are names DP-A as the one blocker (was "Nothing is blocking");
  DP-A's one-liner de-em-dashed; the DP board's two remaining 765 figures dated as at-the-merge
  values; the DP rebuild bullet split to one bold per bullet; storage's §Incoming D-R6 bullet
  split and its §Next item 1 cell un-widened; continuum's flag bullet split; the DP-A card
  gains a How-it-got-here entry noting this cleanup.
- Schemas: c2's two dead-concepts claims narrowed to what the charter list actually names
  (discriminator, the enrichments block — with `content.kind`/`content.text` dying alongside
  the per-kind model, not on the list); c10's two double-em-dash sentences fixed (root
  description, `t_start`).
- contracts/README.md: the fourteen-files bullet's comma splice repaired by splitting it in
  two.

**Corrections to earlier entries in this worklog**

- The WP-A2 claim "Nothing else on those boards was touched" was false: continuum's
  pre-existing storage flag bullet also gained a clause ("redesigned whole-record by the
  drafted D-R6"). The edit was accurate; the disclosure was missing. Disclosed now.
- "The board's stale figure was corrected in the review round" over-claimed: one of three
  sites was (the status line). The WS-index VC row and a §Next bullet kept 765 until this
  round dated them as at-the-merge figures.
- The D22 deferral's basis is restated: not "plan-sanctioned" (the plan's own banner
  disclaims binding force until ratified) but this — the branch is unmerged, so the field
  guide still truthfully teaches the running v0 service; the rewrite lands with Stage G,
  before cutover.
- The review arithmetic, enumerably: 14 raw findings → 7 confirmed + 7 refuted; the 7
  confirmed describe 6 distinct defects (the DP-board 765 figure was found by two lenses)
  across 5 edit sites (the D-R2 lineage cell carried two defects, verb precision and bold
  budget, fixed in one edit).
- "README contract-edit order" in the WP-A2 heading means `product/README.md`'s conventions
  row ("A contract changes in §Contracts first, then `contracts/`, then both canvases"), not
  `contracts/README.md`.

**Verification re-run (2026-08-06)**

```
$ …/scratchpad/validate_stage_a_schemas.py   # processed_at fixture removed; rejected-case added
18 passed, 0 failed
$ storage        .venv/bin/python -m pytest -q → 310 passed
$ data-processing .venv/bin/python -m pytest -q → 788 passed, 21 skipped
$ continuum       .venv/bin/python -m pytest -q → 262 passed, 7 skipped
```

STYLE §Self-check re-run over every touched file (bold density, widest cell, restatement
grep, per-sentence em-dash scan): the restatement grep for the two law sentences now hits
only the charter (the home) and the plan doc (the dated design source). Residual scanner
flags are pre-existing legacy cells and status lines outside this round's scope, false
positives on law-name labels ("L3 — Identity."), and three 21–31-word why-cells in this
worklog's own WP tables, kept as contemporaneous record. This file's status line was brought
under budget.

## Exit criteria (§8 Stage A)

§8 names one exit criterion; the table below splits it into what this session could complete
and what only the founder can do. That remainder is why the status line reads BLOCKED, not
DONE — sign-off is an act, not a work product.

| Criterion | Status | Evidence |
|---|---|---|
| WP-A1: D-rows D-R1…D-R6 drafted into `product/DECISIONS.md` | done | commit `04a6828`; §Drafted section, six cards |
| WP-A1: CHARTER §Slot Law written | done | commit `04a6828`; L1–L12 + dead-concepts, drafted banner |
| WP-A1: ARCHITECTURE C2/C10 cards updated | done | commit `04a6828`; v1/v2 `designed`, running wire named |
| WP-A2: `contracts/c2_processed_record.v1.json` | done | commit `b5f39c8`; 17/17 validation checks green (§Test evidence) |
| WP-A2: `contracts/c10_daylog.v2.json` | done | commit `b5f39c8`; same run |
| WP-A2: README contract-edit order respected | done | §Contracts first (`04a6828`) → `contracts/` → three canvases (`b5f39c8`) |
| No runtime change | done | diff main…HEAD is `*.md`/`*.json` only; storage 310 · DP 788+21s · continuum 262+7s all green |
| Adversarial review, findings resolved | done | 14 raw → 7 confirmed → all fixed (final commit); 7 refuted with reasons |
| **Founder sign-off on the plan doc + schemas** | **pending — the blocker** | escalation **DP-A** on the founders' board, opened 2026-08-05 |

On sign-off: ratify/renumber D-R1…D-R6 into the register, then Stage B may open. Nothing in
Stages B–G is started.
