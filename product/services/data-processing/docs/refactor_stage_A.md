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

### WP-A2 — contract schemas

*(pending)*

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

## Deviations from the plan

*(flagged loudly, with rationale)*

## Test evidence

*(exact commands + verbatim output)*

## Noticed for later stages

## Exit criteria (§8 Stage A)

| Criterion | Status | Evidence |
|---|---|---|
| Founder sign-off on the plan doc + schemas | pending | — |
