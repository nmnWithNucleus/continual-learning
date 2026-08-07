# The record-vs-mutation law — CHARTER extract (WS-E)

**Status:** superseded 2026-08-06 by [CHARTER.md](../CHARTER.md) §Slot Law
([D23](../../../DECISIONS.md)); this file is retained for the retired law's reasoning until
Stage G folds it into condensed history. Everything below this line is the original text,
unchanged — quoted material keeps its wording ([STYLE.md](../../../STYLE.md) rule 10). It was
folded into the charter as §Record-vs-mutation law on 2026-07-25 (WS-VC), the section §Slot
Law replaced.
**Source of truth for the reasoning:** `handoff/ws-video-clip.md` §4 (invariant, T1–T5, R1–R5, the
18-row worked table).
*Source of truth for the rule:* `tests/test_emission_law.py` + `app/stagegraph/stage.py`. The law
is executable; this text is its statement, not its enforcement.

---

## The invariant

A C2 record is not "a thing we computed". It is **one independently-placeable,
independently-labelable, independently-losable claim about a span of the user's life.**

- **Placement** is `t_start`, and only `t_start` (continuum's `daylog.py:109-114`; `t_end` is read
  zero times).
- **Labeling** is `content.kind`, and only `content.kind` (three labelled day-log lines).
- **Identity** is `record_id = sha256(chunk_id ␀ pipeline_version [␀ discriminator])`
  (`app/pipeline.py:33-46`), and `/context` is a blind upsert on it.
- **Loss** is per-stage: a failure removes exactly the units that stage alone emits.

Therefore: **information deserves a record iff it needs its own place, its own label, or its own
failure. Information that makes an existing record's declared _structure_ more precise is a
mutation. Information about _how_ a claim was obtained is an enrichment — or nothing.**

## The decision procedure — five ordered tests, first match wins

Apply to any signal **S** derived from one C1 chunk.

| Test | Question | If it fires |
|---|---|---|
| **T1 `DERIVABLE`** | Is S a pure function of *this* chunk's bytes + config? | **No → not a DP record.** DP's ingest is per-chunk end to end; a cross-chunk summary is continuum's job. |
| **T2 `REACHABLE`** | Does S reach a consumer that exists **today**? (`content.text` of a pinned `kind` does; `content.segments` only for `kind='transcript'`; `enrichments` is read nowhere) | **Neither reachable nor read → do not emit.** Store nothing you cannot spend. Re-apply when a consumer lands — a gate on *when*, not a veto. |
| **T3 `SPINE`** | Is S the modality's answer to "what happened in these bytes"? | **`PRIMARY` unit(s)** from `assemble()`. Exactly one enabled primary per modality; its fragment is the base dialect and is non-empty. |
| **T4 `EDITS`** | Does producing S change bytes a record already claims? | **Structure-fill** (fills a field the parent declared and left empty) → `MUTATE`: `kind='mutate'`, `writes ⊆ primary.mutable_slots`, non-empty `version_fragment` mandatory. *String-change* (`content.text` would differ from a previous run's) → `FORBIDDEN`: refine *inside* the producing stage before assembly, or fork `pipeline_version` and mint a new record. *Mechanical test: could two workers on different config both honestly claim to be right? If yes → fork, not edit.* |
| **T5 `CHANNEL`/`SPAN`** | Does S own a pinned `kind` routing to its own day-log line, or an independently addressable span? | `NEW RECORD` with its own discriminator. |

Fallthrough → `ENRICHMENT` (subject to T2) or **stage input**.

## The five riders

- **R1 — `FORK` `RIDER` (mechanised).** Any enabled stage whose configuration can change the bytes
  of a record it does not itself emit must contribute a non-empty `version_fragment`. Mechanised
  as: *a sidecar declaring a non-empty `provides` must return a non-empty fragment when enabled* —
  a provided slot exists only to be consumed, i.e. to change someone else's bytes.
- Conversely a sidecar that only *adds* records and feeds nothing declares *no* fragment
  (`translate`, `acoustic`, `injected_caption`): forking the whole chunk's dialect on an additive
  toggle would re-key the primary for a change that never touched it.
- **R2 — `INDEPENDENCE` `RIDER`.** Two records may describe the same second only if a consumer can
  use either *without* the other; every sidecar record is *self-anchored* (carries its own app,
  region, offsets inside its own `content.text`).
- *Corollary 2:* where record B's specific strings are *grounded in* record A's (OCR text injected
  into the caption, D-09), the pair is *one witness rendered on two channels* — no consumer may
  treat their agreement as corroboration.
- **R3 — `DIALECT`-`HONESTY` `RIDER`.** `pipeline_version` states the *attempted* dialect, never
  what succeeded (it is resolved before any stage runs).
- Hence: (a) `best_effort` ⇒ additive-only, never a mutate, never upstream of a required stage;
  (b) *never `best_effort` + a non-empty fragment*; (c) never use `discriminator` as a back-door
  dialect carrier; (d) absence is diagnosed by record presence + a metric, never by the dialect
  string and never by a fabricated placeholder claim; (e) cross-service: continuum must never
  infer "no on-screen text" from an absent OCR record.
- **R4 — SET-`STABILITY` `RIDER`.** The record *set*, count, discriminators, spans, is a pure
  function of `(chunk bytes, settings)` and must not depend on model output, decoder build, *stage
  outcome*, or which siblings survived a filter.
- Discriminators are quantised from a grid that is itself a pure function of the declared C1 span
  — never a survivor ordinal, never a raw decoder frame index, never a hash of model output.
- **R5 — `BUDGET` `RIDER`.** Every new record class names (i) its consumer today and (ii) its
  characters-per-second-of-life budget against the day-log block. A class that cannot answer both
  does not ship.

---

## Where the law is enforced (three layers, deliberately not one)

| Layer | What it can catch | Where |
|---|---|---|
| **Registration** (import time) | The **structural** half of R1: a sidecar with a non-empty `provides` that never declares `version_fragment` at all. Plus the pre-existing rules: mutate may not override `enabled()`; `best_effort` is sidecar-only; `writes` is mutate-only and mandatory; one run method; unique `(name, order)` per modality. | `app/stagegraph/stage.py` → `StageRegistrationError` |
| **The law test** (CI) | The **configuration** half: over a matrix of environments (legacy · clip · every OCR backend · every audio sidecar · mistyped values), an enabled provides-bearing sidecar returns a NON-Empty fragment; no `best_effort` carries a fragment; enabled mutates' `writes ⊆` the enabled primary's `mutable_slots`; exactly one enabled primary; no required stage downstream of a `best_effort` one; resolvers are pure; a disabled stage's fragment never reaches the dialect; the mechanically-checkable worked-table verdicts. | `tests/test_emission_law.py` |
| **Graph resolution** (per chunk) | Config-shaped errors that need the live settings: enabled-but-needs-disabled, hollow `required` promises, duplicate `provides`, dialect composition. | `app/stagegraph/executor.py` → `GraphResolutionError` |

Settings do not exist at import, which is exactly why the raise cannot be the whole law: a stage that
*declares* a resolver and returns `''` for the configuration in which it is enabled is caught by the
CI layer and nowhere else.

## The one fixed exemption

`video/keyframes` — a sidecar with a non-empty `provides` and an empty `version_fragment`. It exists
solely to reproduce the retired `vidproc-*-v0` record_ids byte-for-byte (D-14) and is named, once, in
both `app/stagegraph/stage.py` and `tests/test_emission_law.py`. **No new stage may join it:** the
law test runs with the exemption set emptied and asserts that this pair is the *only* violation in
any configuration, so a second stage sliding under the exemption is a red test, not a silent debt.

## Known latent hole (named, not fixed)

Worked-table row 15 — the `acoustic` sidecar declares no fragment today. That is correct while it
only *adds* records and feeds nothing (`provides == ()`), and the law test pins that condition: the
day a real acoustic backend starts providing a slot, R1 applies and CI goes red. Retro-fitting
`+ac-<backend>-v1` is the audio owner's call at that point.
