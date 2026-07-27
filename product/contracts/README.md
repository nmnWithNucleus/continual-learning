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
- Seven contracts are materialized: C3/C9/C4/C6 (serve loop) + C1/C2 (learn loop) + **C12**
  (user profile, D18). C5/C7/C8/C10/C11/C13/C14 get schema files when their slices start.

## C-numbers minted 2026-07-26 (D18 — storage/C10 board)

`C12` **user profile** (per-user policy; `home_tz`) · `C13` **recipe registry** · `C14`
**reservoir**. Shapes are pinned in [../ARCHITECTURE.md](../ARCHITECTURE.md) §Contracts; **C10
evolves in place** (raw range read → day-log fetch + watermark window) rather than taking a new
number, because its direction and peers are unchanged.

Only **C12** ships a schema today: it is one field, fully determined, and it makes D17's
"no default timezone anywhere" rule machine-checkable. **C10-evolved, C13 and C14 schemas land as
the build slice's first act** — per the rule above and per [../ORG.md](../ORG.md) §"Contracts before
fan-out", the schemas must exist before that slice's workstreams fan out, and not before there is a
slice. Writing a schema no service validates against is how prose and schema drift apart, which is
the failure D17 hit (a claim hiding in a schema `description`, missed by a count taken from prose).

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
