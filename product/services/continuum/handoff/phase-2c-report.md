# Phase 2c — lean architecture + storage client seams

**Branch:** `svc/continuum-morpheus-2c` off main · **Status:** the three seams landed, cycle runs
the 5-verb loop against them, parity byte-identical · **Cofounder review before merge.**

A self-contained continuum refactor: no other service, no GPU, no new experiments. Continuum now
consumes storage across three CLIENT interfaces, each with a local backend today and an
HTTP-to-storage backend later — the eventual integration is a transport swap, not a redesign.

---

## The three seams (`app/clients/`)

| seam | interface | local backend | future |
|---|---|---|---|
| **day-log fetch** | `DayLogClient.fetch_daylog(win)` → segment/block day-log; `eligible_blocks`, `render`, `fingerprint` | `LocalDayLogClient` — builds from a record provider via the same `build_daylog` + renderer | HTTP GET of storage's materialized day-log |
| **recipe registry** | `RecipeRegistry.fetch_recipe(id)`, `fetch_policy(id)` | `LocalRecipeRegistry` — resolves an id to `recipes/<id>.json` / `policies/<id>.json` | GET the versioned artifact |
| **reservoir** | `ReservoirClient.admit(...)`, `entries(...)`, `sample_replay(..., source)` | `LocalReservoirClient` — filesystem reservoir; rawlog replay re-reads prior day-logs via the day-log client | HTTP write + read |

Factories in `clients/__init__.py` (`day_log_client`, `recipe_registry`, `reservoir_client`) pick
the backend from settings; when storage lands they gain an `http` branch and nothing above changes.
This is the posture the scaffold already used for the reservoir and model directory.

## The day-log migration — byte-identical

`cycle.py` no longer builds the day-log inline. It fetches it: `daylog_client.fetch_daylog(win)`.
`daylog.py` / `window.py` / `renderer.py` are now reached only *through* the client (build,
segmentation, and file rendering are its internals). The stage key hashes the day-log's **content
fingerprint** rather than the raw records — correct (two record sets that render to the same
day-log are the same night's input) and forward-looking (once storage materializes, raw records
never reach continuum).

Proven byte-identical, three ways:
- `test_local_daylog_client_is_byte_identical_to_inline_build` — segments and blocks match the
  pre-2c inline path exactly.
- `test_rendered_daylog_files_are_byte_identical` — `segments.jsonl` / `blocks.jsonl` / `day.txt`
  compare equal byte-for-byte.
- **The render_block parity suite still passes byte-identical** (tier B, pinned train env): the
  full `tests/parity/` run is **83 passed, 1 skipped** — `render_block` 1427/1427, the chain
  fingerprint, and amplify parity all green. The migration touched no kernel.

## The 5-verb loop

`run_cycle(win, *, daylog_client, registry, recipe, policy, force)` now reads as the lean loop:
**fetch recipe** (registry) · **fetch day-log** (client) · **amplify** · **finetune** (backend) ·
**gate** (policy) · **publish** (C5). Recipe and gate policy are fetched **by id** from the
registry, not by file path — the id is what a night records, and only `recipe_id` enters a stage
key (`policy_id` never does, preserving the 2b structural split).

Both backends stay green: the mock cycle tests and the morpheus backend both resolve and run;
`TRAINER_BACKEND=morpheus` selects correctly and the registry resolves the shipped
`consolidation-v1.0` + `gate-policy-v1.1`.

## Replay: the locked raw-source path, now wired

The reservoir seam expresses the locked decision (replay re-reads prior **day-logs**; the
amplified store is audit/provenance). Recipe v1.0 pins `source="amp"` because the Phase-1 goldens
were produced that way and parity diffs against them — that path is **unchanged and byte-identical**
(the pooled sampler was factored out, same inputs, same output). `source="rawlog"` is now wired
through the day-log client (it resolved the standing `NotImplementedError` in the replay stage), so
flipping a future recipe to raw is a **recipe change, not a code change**. Both are tested end to
end through the cycle.

## Tests

**185 tier-A passed, 7 skipped** (was 171) — +14: 13 seam/wiring tests (`test_clients.py`) and one
rawlog cycle test. **83 tier-B parity passed, 1 skipped.** The full synthetic night runs green
through the CLI over the seams.

## Contract questions for the founders' storage session (NOT pinned)

1. **Day-log fetch must be addressable by `(user, window_id)` for arbitrary PRIOR windows**, not
   just "the current window." Raw-source replay re-reads prior day-logs, so C10-evolved must serve
   any past window's day-log on demand. The local impl reconstructs prior windows from the
   reservoir ledger + the recipe boundary; storage's C10 needs the same addressability. *(This is
   the one design point that surfaced concretely — the local rawlog test needed a window-aware
   fetch to work.)*
2. **The set of a user's prior consolidated windows** is today derived from the reservoir ledger
   (which nights ran). If replay reads day-logs and the amplified reservoir becomes pure
   audit/provenance, storage should expose "which windows has this user consolidated?" as part of
   the reservoir or model-directory contract, rather than inferring it from amplified-corpus files.
3. **Recipe-registry + reservoir contract IDs** — new contracts (registry fetch, reservoir
   write/read) need IDs minted when the board ratifies the storage expansion (storage CHARTER
   lists these as *pending board*). The client interfaces here are the continuum-side shape to
   ratify against.
4. **Day-log format is recipe-versioned.** The local client is constructed with the recipe's
   segmentation (`segment_seconds`/`block_segments`); storage's materialized day-log must carry
   the recipe/format version so a segmentation change is legible on both sides.

## Out of scope (untouched, as specified)

No DP integration, no Phase 3, no parity experiments, no GPU, no serve-time harness, no real
storage server. No `Profile` changes, no gate-logic changes (v1.1 ratified). No storage server
side — that is a separate storage workstream and this branch does not block on it.
