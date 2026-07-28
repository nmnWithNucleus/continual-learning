# Contracts — machine-readable schemas

> The **source of truth** for inter-service payload shapes. Prose summaries + the seam table
> live in [../ARCHITECTURE.md](../ARCHITECTURE.md) §Contracts; the JSON Schemas here are what
> code validates against.

**Serve-loop v0.0 (2026-07-09):** `c3_userprompt.v0.json`, `c9_response_stream.v0.json`,
`c4_turn_record.v0.json`, `c6_resolve.v0.json`.

**Learn-loop v0.0 (2026-07-09):** `c1_raw_stream_envelope.v0.json`,
`c2_processed_record.v0.json` — the capture path (computer mic → ASR → `/context`). C1 is two
legs: the `/raw` blob write (recording → storage, storage mints an opaque `blob_ref`) is pinned as
prose in [../ARCHITECTURE.md](../ARCHITECTURE.md) §Contracts, the envelope is this schema. Delivery
is push/at-least-once, dedup on `chunk_id`, gaps via `(stream_id, sequence)`.

## Rules
- **These are `version:"0"` and will evolve.** Additive optional fields need no ceremony.
  A **breaking** change = new `*.vN.json` file + version bump + an [ARCHITECTURE.md](../ARCHITECTURE.md)
  §Contracts edit. Never mutate a schema file in place once services build against it.
- Every service validates the payloads it produces/consumes against these schemas in its tests.
- Ten contracts are materialized across **twelve files**: C3/C9/C4/C6 (serve loop) + C1/C2 (learn
  loop) + **C12** (user profile, D18) + **C10 v1** (*two* files — the day-log fetch and the
  training-window ledger row, see below) + **C13** (recipe registry — *two* files, see below) +
  **C14** (reservoir ledger); the last four landed 2026-07-27 with the storage build slice.
  C5/C7/C8/C11 get schema files when their slices start.

| File | Contract | Body |
|---|---|---|
| `c1_raw_stream_envelope.v0.json` | C1 | recording → data-processing envelope |
| `c2_processed_record.v0.json` | C2 | data-processing → storage `/context` record |
| `c3_userprompt.v0.json` | C3 | input → inference UserPrompt |
| `c4_turn_record.v0.json` | C4 | inference → storage `/sessions` turn |
| `c6_resolve.v0.json` | C6 | model-directory resolution |
| `c9_response_stream.v0.json` | C9 | inference → output stream envelope |
| `c10_daylog.v1.json` | C10 | `GET /training/daylog` — the rendered day-log |
| `c10_training_window.v1.json` | C10 | one training-window ledger row (open · close · each element of the enumeration) |
| `c12_user_profile.v0.json` | C12 | `GET /users/{user_id}/profile` |
| `c13_recipe.v0.json` | C13 | `GET /recipes/{recipe_id}` |
| `c13_gate_policy.v0.json` | C13 | `GET /policies/{policy_id}` |
| `c14_reservoir_ledger.v0.json` | C14 | `GET /reservoir/{user_id}` |

## C-numbers minted 2026-07-26 (D18 — storage/C10 board)

`C12` **user profile** (per-user policy; `home_tz`) · `C13` **recipe registry** · `C14`
**reservoir**. Shapes are pinned in [../ARCHITECTURE.md](../ARCHITECTURE.md) §Contracts; **C10
evolves in place** (raw range read → day-log fetch + watermark window) rather than taking a new
number, because its direction and peers are unchanged.

**C12** shipped first: it is one field, fully determined, and it makes D17's "no default timezone
anywhere" rule machine-checkable. **C10-evolved, C13 and C14 schemas land as the build slice's first
act** — per the rule above and per [../ORG.md](../ORG.md) §"Contracts before fan-out", the schemas
must exist before that slice's workstreams fan out, and not before there is a slice. **`c10_daylog.v1.json`
landed 2026-07-27 with day-log materialization**, transcribed from the body already pinned in
ARCHITECTURE's *C10 card*; it is `version:"1"` because C10 evolved in place, and the raw
range read it evolved FROM (`GET /context/records?user_id=&from=&to=`, event-time) is not retired
and carries no schema of its own. **`c13_recipe.v0.json`, `c13_gate_policy.v0.json` and
`c14_reservoir_ledger.v0.json` landed 2026-07-27** with the registry + reservoir slice, and storage
validates against all three on the read path. **`c10_training_window.v1.json` landed 2026-07-27**,
closing the last unschema'd body storage serves. Writing a schema no service validates against is how
prose and schema drift apart, which is the failure D17 hit (a claim hiding in a schema
`description`, missed by a count taken from prose).

**Why C10 is two files.** C10 is a family of *operations*, not one payload: `GET /training/daylog`
returns the rendered day-log, while `POST /training/windows`, `POST /training/windows/{id}/close`
and each element of `GET /training/windows` return a **ledger row**
(`{window_id, user_id, t_start, t_end, state, outcome, opened_at, closed_at}`). Folding both into
one file would need a `oneOf` — the same thing rejected for C13 below — and `c10_daylog.v1.json`'s
`$id` names the day-log body specifically, so a consumer validating "a C10 day-log" against a root
that had quietly become a union would be checking nothing. Both are `v1` because the *contract*
evolved in place. The ledger row deliberately carries **no `contract`/`version` envelope**, unlike
every other body here: it is returned bare and returned inside arrays, and stamping a tag on every
element of an enumeration puts the envelope in the wrong place. **The row schema is strictly
stronger than storage's pydantic mirror**, which is why it is not redundant with it: only the schema
can say `state` and `outcome` **agree**, so a `consolidated` row carrying a null `outcome` — a night
whose training status is unanswerable, because `last_trained_t` is derived by selecting
`outcome = 'published'` rows — passes the model and is caught only here. Note also what the row does
*not* contain: `last_trained_t`. The watermark is derived, never stored, and
`additionalProperties: false` is what keeps a second source of truth from appearing beside the
ledger.

**A trap in `pattern`, closed in all three `window_id` specs (2026-07-27).** JSON Schema's `pattern`
is *unanchored-search* semantics, and in the Python validator these contracts are enforced by, `$`
also matches **just before a trailing newline** — so `^w\d{8}T\d{6}Z$` on its own admits
`"w20260722T035900Z\n"`. That string is a **different filesystem path** from the id it looks like,
and `window_id` is a real path component (continuum's `journal/`, `cycles/`, `adapters/`; storage's
reservoir root). Storage's own `validate_window_id` closes it with `fullmatch`; the three schemas
that carry a `window_id` — `c10_training_window.v1.json`, `c10_daylog.v1.json`,
`c14_reservoir_ledger.v0.json` — close it with `"minLength": 17, "maxLength": 17`, which also states
the fixed-width property the whole lexicographic-ordering guarantee rests on. **Those bounds are not
redundant with the pattern and must not be tidied away**; storage pins all three together in one
test so the fix cannot silently reopen.

**Why C13 is two files.** The training recipe and the gate policy are separate artifacts with
separate ids and separate lifecycles — `recipe_id` is hashed into continuum's amplify/train stage
keys, `policy_id` never enters one — so a publish-threshold change must never fork `recipe_id`.
One schema could not express both shapes without a `oneOf` that hides exactly the distinction the
contract is *about*. **Both also deviate from the house `additionalProperties: false` rule, on
purpose:** recipe and policy artifacts carry human provenance prose (`source`, `note`, `traps_note`)
that records why a number is what it is, and a registry that rejected an artifact for documenting
itself would push that documentation out of the artifact and into a wiki. A mistyped knob is still
caught, because every knob that matters is `required`.

## The serve-loop v0.0 flow these describe
```
browser → input (QueryBuilder builds C3) → inference (/infer)
        → inference resolves C6, streams C9, writes C4 to storage
        → output relays/renders the C9 stream in the browser
```

## The learn-loop v0.0 flow these describe
```
computer mic → recording (chunk) → PUT bytes → storage /raw ── mints blob_ref ─┐
                                                                               │
recording ── C1 envelope {blob_ref, chunk_id, (stream_id,sequence)} ──push──▶ data-processing
                                                    (pull bytes by blob_ref) ──▶ ASR
data-processing ── C2 processed record ──▶ storage /context  (idempotent on record_id)
```
