# Contracts — machine-readable schemas

> The **source of truth** for inter-service payload shapes. Prose summaries + the seam table
> live in [../ARCHITECTURE.md](../ARCHITECTURE.md) §Contracts; the JSON Schemas here are what
> code validates against.

## Rules
- **A schema is never mutated in place once services build against it.** Additive optional fields
  need no ceremony; a *breaking* change is a new `*.vN.json` file plus an
  [ARCHITECTURE.md](../ARCHITECTURE.md) §Contracts edit, in that order.
- Every service validates the payloads it produces and consumes against these schemas in its tests.
  Writing a schema no service validates against is how prose and schema drift apart.
- Ten contracts are materialized across **thirteen files**, listed below.
- C5/C7/C8/C11 get schema files when their slices start.

| File | Contract | Body |
|---|---|---|
| `c1_raw_stream_envelope.v0.json` | C1 | recording → data-processing envelope |
| `c2_processed_record.v1.json` | C2 | data-processing → storage `/context` record — one per chunk, built from slots (D24) |
| `c2_processed_record.v0.json` | C2 | the retired record shape, kept as the day-log parity proof's fixture format ([↓](#why-a-retired-schema-is-still-here)) |
| `c3_userprompt.v0.json` | C3 | input → inference UserPrompt |
| `c4_turn_record.v0.json` | C4 | inference → storage `/sessions` turn |
| `c6_resolve.v0.json` | C6 | model-directory resolution |
| `c9_response_stream.v0.json` | C9 | inference → output stream envelope |
| `c10_daylog.v2.json` | C10 | `GET /training/daylog` — the rendered day-log, slot-walk renderer, `updated_at` axis (D28) |
| `c10_training_window.v1.json` | C10 | one training-window ledger row (open · close · each element of the enumeration) |
| `c12_user_profile.v0.json` | C12 | `GET /users/{user_id}/profile` |
| `c13_recipe.v0.json` | C13 | `GET /recipes/{recipe_id}` |
| `c13_gate_policy.v0.json` | C13 | `GET /policies/{policy_id}` |
| `c14_reservoir_ledger.v0.json` | C14 | `GET /reservoir/{user_id}` |

## Why a retired schema is still here

`c2_processed_record.v0.json` describes a record shape nothing emits. It stays because the day-log
parity proof ([D20](../DECISIONS.md)) loads it by `$id` as the format of its reference fixtures,
and a green storage test runs that proof in-process. Retiring the whole parity apparatus is a
single deliberate act, recorded on the [storage board](../services/storage/HANDOFF.md) §Next.
Until then this file is a fixture format, not a wire.

## Why C10 is two files

C10 is a family of *operations*, not one payload: `GET /training/daylog` returns the rendered
day-log, while `POST /training/windows`, `POST /training/windows/{id}/close` and each element of
`GET /training/windows` return a **ledger row** (`{window_id, user_id, t_start, t_end, state,
outcome, opened_at, closed_at}`). Folding both into one file would need a `oneOf`, the same thing
rejected for C13 below. The day-log schema's `$id` also names the day-log body specifically, so a
consumer validating "a C10 day-log" against a root that had quietly become a union would be
checking nothing.

The ledger row deliberately carries *no `contract`/`version` envelope*, unlike every other body
here: it is returned bare and returned inside arrays, and stamping a tag on every element of an
enumeration puts the envelope in the wrong place. *The row schema is strictly stronger than
storage's pydantic mirror*, which is why it is not redundant with it: only the schema can say
`state` and `outcome` *agree*. A `consolidated` row carrying a null `outcome` (a night whose
training status is unanswerable, because `last_trained_t` is derived by selecting
`outcome = 'published'` rows) passes the model and is caught only here. Note also what the row
does *not* contain: `last_trained_t`. The watermark is derived, never stored, and
`additionalProperties: false` is what keeps a second source of truth from appearing beside the
ledger.

## Why C13 is two files

The training recipe and the gate policy are separate artifacts with separate ids and separate
lifecycles — `recipe_id` is hashed into continuum's amplify/train stage keys, `policy_id` never
enters one, so a publish-threshold change must never fork `recipe_id`. One schema could not
express both shapes without a `oneOf` that hides exactly the distinction the contract is *about*.

**Both also deviate from the house `additionalProperties: false` rule, on purpose:** recipe and
policy artifacts carry human provenance prose (`source`, `note`, `traps_note`) that records why a
number is what it is, and a registry that rejected an artifact for documenting itself would push
that documentation out of the artifact and into a wiki. A mistyped knob is still caught, because
every knob that matters is `required`.

## A trap in `pattern`, closed in every `window_id` spec

JSON Schema's `pattern` is *unanchored-search* semantics, and in the Python validator these
contracts are enforced by, `$` also matches **just before a trailing newline** — so
`^w\d{8}T\d{6}Z$` on its own admits `"w20260722T035900Z\n"`. That string is a *different
filesystem path* from the id it looks like, and `window_id` is a real path component (continuum's
`journal/`, `cycles/`, `adapters/`; storage's reservoir root).

Storage's own `validate_window_id` closes it with `fullmatch`. The three schemas that carry a
`window_id` (`c10_training_window.v1.json`, `c10_daylog.v2.json`, `c14_reservoir_ledger.v0.json`)
close it with `"minLength": 17, "maxLength": 17`, which also states the fixed-width property the
whole lexicographic-ordering guarantee rests on. *Those bounds are not redundant with the pattern
and must not be tidied away.*

`c2_processed_record.v1.json` closes the same hole on `record_id` with 64-char bounds.
`pipeline_version` is variable-width by nature, so it cannot be closed that way and requires
`fullmatch` in any enforcing implementation.

## The serve-loop flow these describe
```
browser → input (QueryBuilder builds C3) → inference (/infer)
        → inference resolves C6, streams C9, writes C4 to storage
        → output relays/renders the C9 stream in the browser
```

## The learn-loop flow these describe
```
computer mic → recording (chunk) → PUT bytes → storage /raw ── mints blob_ref ─┐
                                                                               │
recording ── C1 envelope {blob_ref, chunk_id, (stream_id,sequence)} ──push──▶ data-processing
                                                    (pull bytes by blob_ref) ──▶ stage graph
data-processing ── C2 processed record ──▶ storage /context  (idempotent on record_id)
```
