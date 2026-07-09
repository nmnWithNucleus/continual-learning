# Contracts — machine-readable schemas

> The **source of truth** for inter-service payload shapes. Prose summaries + the seam table
> live in [../ARCHITECTURE.md](../ARCHITECTURE.md) §Contracts; the JSON Schemas here are what
> code validates against.

**Frozen for serve-loop v0.0 (2026-07-09):** `c3_userprompt.v0.json`, `c9_response_stream.v0.json`,
`c4_turn_record.v0.json`, `c6_resolve.v0.json`.

**Frozen for learn-loop v0.0 (2026-07-09):** `c1_raw_stream_envelope.v0.json`,
`c2_processed_record.v0.json` — the capture path (computer mic → ASR → `/context`). C1 is two
legs: the `/raw` blob write (recording → storage, storage mints an opaque `blob_ref`) is pinned as
prose in [../ARCHITECTURE.md](../ARCHITECTURE.md) §Contracts, the envelope is this schema. Delivery
is push/at-least-once, dedup on `chunk_id`, gaps via `(stream_id, sequence)`.

## Rules
- **These are `version:"0"` and will evolve.** Additive optional fields need no ceremony.
  A **breaking** change = new `*.vN.json` file + version bump + an [ARCHITECTURE.md](../ARCHITECTURE.md)
  §Contracts edit. Never mutate a frozen file in place once services build against it.
- Every service validates the payloads it produces/consumes against these schemas in its tests.
- Six v0.0 contracts are materialized: C3/C9/C4/C6 (serve loop) + C1/C2 (learn loop). C5/C7/C8/
  C10/C11 get schema files when their slices start.

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
