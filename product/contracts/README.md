# Contracts — machine-readable schemas

> The **source of truth** for inter-service payload shapes. Prose summaries + the seam table
> live in [../ARCHITECTURE.md](../ARCHITECTURE.md) §Contracts; the JSON Schemas here are what
> code validates against.

**Frozen for serve-loop v0.0 (2026-07-09):** `c3_userprompt.v0.json`, `c9_response_stream.v0.json`,
`c4_turn_record.v0.json`, `c6_resolve.v0.json`.

## Rules
- **These are `version:"0"` and will evolve.** Additive optional fields need no ceremony.
  A **breaking** change = new `*.vN.json` file + version bump + an [ARCHITECTURE.md](../ARCHITECTURE.md)
  §Contracts edit. Never mutate a frozen file in place once services build against it.
- Every service validates the payloads it produces/consumes against these schemas in its tests.
- Only the four v0.0 contracts are materialized. C1/C2/C5/C7/C8/C10/C11 get schema files when
  their slices start.

## The serve-loop v0.0 flow these describe
```
browser → input (QueryBuilder builds C3) → inference (/infer)
        → inference resolves C6, streams C9, writes C4 to storage
        → output relays/renders the C9 stream in the browser
```
