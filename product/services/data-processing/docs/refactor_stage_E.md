# DP Rebuild — Stage E worklog (Storage v2)

**Stage:** E — Storage v2 · **Status:** DONE 2026-08-06 · *Dated:* 2026-08-06
**Branch:** `dp-rebuild-v1` · **Plan:** [refactor_dp_service.md](refactor_dp_service.md) §8 Stage E
**Laws this stage:** plan §5 (the storage-side set, ratified as D27/D28) · §1 L8 as
corrected at the Stage D close-out (convergence, not monotonicity) · D18 (watermark
philosophy) · D20 (parity bar, re-baseline ruled by D28).
**Scope:** WP-E0 (live-service repoint + inherited nits) · WP-E1 (`db.py`
`created_at`/`updated_at` + the C2 v1 surface) · WP-E2 (`daylog.py` v2 renderer) ·
WP-E3 (E-2 whole-record retraction) · WP-E4 (D20 parity re-baseline).

Carried-over instructions honoured this stage (from Stage C/D "Noticed"): the two heal
shapes are the byte-compare inputs — a still-holey re-POST is byte-identical (no
re-window) and a filling heal is byte-different (next window); C10 v2 bucketing accepts
both timestamp spellings (verbatim C1 root spans, and the `+00:00`-microsecond split
spelling); ocr slot text keeps chunk-relative `+Ns` stamps; `c10_daylog.v2.json` joins
the `window_id` length-bounds trap test; the Heard-lines `asr` fallback is ruled in.

Founder rulings applied this stage (session brief, 2026-08-06):

- R1 — branch storage is C2-v1-only; the v0 paths keep running on the live worktree
  service until Stage F.
- R2 — `put_context` invalidation covers *both* windows an `updated_at` move touches;
  a re-materialized old window excludes the moved record (the D18 dissolve-not-handle
  philosophy).
- R3 — blind replace, never fuller-wins; slot regression under heal is designed, and
  the ledger carries hole truth.

---

## WP-E0a — live-service repoint (`:8083` → the main-pinned worktree)

**The hazard** (the same class WP-C0 closed for DP): the live storage service ran with
its working directory *inside* this repo's `product/services/storage/` — the tree
Stage E rewrites. It held pre-rebuild code in memory, but any restart would have
imported a half-rewritten `app/`.

**Mechanism read first** (`storage/run.sh` + `platform/deploy/run_learn.sh` +
`learn.env` + pidfiles; no surprises, no escalation needed):

- Storage was launched by `run_learn.sh` on 2026-08-03 16:01 (pidfile
  `run-learn/storage.pid` matched pid 3356393) from the *old* `SERVICES_ROOT`.
- The Stage C repoint of `learn.env` landed 2026-08-06, and `start_service` *adopts* an
  already-healthy service, so storage never picked it up (DP did, at its WP-C0 restart).
- `run.sh` prefers `$HERE/.venv/bin/uvicorn` and derives every data path from `$HERE`
  unless the env overrides it. Both facts shaped the repoint: the worktree needs a
  `.venv`, and the data paths must be pinned or they would silently move with `$HERE`.

**Data paths discovered from `/proc/3356393/environ` first** — preserved byte-exactly
(the repoint moves code, never data):

| Variable | Value (before == after) |
|---|---|
| `STORAGE_DB_PATH` | `/home/ubuntu/nmn/continual_learning/product/services/storage/app/dev.db` |
| `STORAGE_RAW_DIR` | `…/continual_learning/product/services/storage/app/raw_store` |
| `STORAGE_RESERVOIR_DIR` | `…/continual_learning/product/services/storage/app/reservoir` |
| `STORAGE_RECIPES_DIR` | `…/continual_learning/product/services/storage/recipes` |
| `STORAGE_POLICIES_DIR` | `…/continual_learning/product/services/storage/policies` |

| Action | Detail |
|---|---|
| venv | worktree `storage/.venv` → symlink to this tree's `storage/.venv` (same interpreter; the venv only supplies packages and is gitignored) |
| exclude | `.git/info/exclude` gains `.venv` (the repo's `.venv/` pattern matches directories only, so a symlink reads untracked); machine-local, not a repo change |
| pins | `learn.env` += the five `STORAGE_*` values above (dated comment); else `run.sh` re-derives them from the worktree `$HERE`, moving data |
| restart | one tight window: `kill -TERM 3356393` → port free → `bash run_learn.sh --skip-install` |

**Evidence (2026-08-06):**

- Before (20:30:06Z): `:8083` → 200 `{"ok":true}`, pid 3356393, cwd →
  `…/continual_learning/product/services/storage`, interpreter
  `…/continual_learning/…/storage/.venv/bin/python3`; `dev.db` 86016 bytes,
  mtime 06:17, `context_records` 2 · `raw_blobs` 2, latest ingest
  `2026-08-06T06:17:03Z`.
- Window: `kill -TERM` 20:31:12Z → port free 20:31:13Z → `storage healthy (pid 330817)`
  → bring-up complete 20:31:16Z (≈4 s). DP `:8085` and recording `:8084` adopted, not
  restarted (pids 3835816 / 3356458 unchanged before vs after).
- After (20:31:39Z): pid 330817, cwd → `/home/ubuntu/nmn/dp-v0-live/product/services/storage`,
  cmdline runs the worktree's `.venv/bin/uvicorn` whose symlinked shebang resolves to
  the *same* interpreter; all five `STORAGE_*` values byte-identical to the table
  above; `:8083` → 200.
- Code provably resolves from the worktree: a fresh `app/__pycache__/` appeared there
  while this tree's storage `__pycache__` kept its 2026-08-03 mtime. Worktree
  `git status` stays clean.
- End-to-end (fresh chunk, unique bytes so dedup could not shortcut): a 7 s
  generated WAV → `POST :8084/capture/run` (`chunk_seconds=4`) → 2 chunks, record_ids
  `6cbdef97…62c4d` and `739eff2b…7b53`.
- `GET :8083/context/records/6cbdef97…` → HTTP 200, a C2 **v0** record
  (`content.kind: transcript`, `pipeline_version: asr-fw-v1` — the old wire,
  untouched). Both rows landed in the *same* `dev.db` (count 2 → 4, ingest_time
  `20:32:20Z`/`20:32:21Z`, mtime bumped). Fleet health after: `:8083`/`:8084`/`:8085`
  all 200.

The single authorized restart is spent; storage is now immune to anything Stage E does
to this tree. Notes: recording (`:8084`, pid 3356458) still runs *from this tree* —
its files are never touched on this branch; carried to Stage F/G. A stray Jul-24
storage smoke instance on `:8099` (scratchpad DB from an old session) is not part of
the deploy fleet and was left alone.

**`STORAGE_RECIPES_DIR`/`STORAGE_POLICIES_DIR` tension, recorded:** both still point
into this working tree (preserved exactly, per the brief — they are env-pinned values
of the live process). `registry.fetch_recipe` reads per request, so the live service
reads recipe files from the branch tree at render time. Safe because recipes are
immutable under their id (the only legal edit is a new file with a new stem) and the
live renderer pins `consolidation-v1.1` in worktree code: Stage E's recipe bump lands
as a *new additive file* the live service never asks for. Inventory item for Stage F.

## WP-E0b — inherited nits (paper)

| File | Action | Why |
|---|---|---|
| `CHARTER.md` (DP) | edited | the 2026-08-06 L8 changelog bullet's second sentence carried two em-dashes (rule 6); restructured with semicolons, substance identical |
| `CHARTER.md` §Slot Law L8 | edited | the 76-word heal sub-bullet split into three sub-bullets, each under the 42-word cap; wording otherwise preserved |
| `docs/refactor_dp_service.md` §1 L8 | edited | the 101-word heal clause (verdict 4) split into a verdict line and three sub-bullets |
| `product/DECISIONS.md` D27 | edited | Watch-out #1 trimmed 66 → 59 words (the 60-word reasoning cap); substance identical |
| `product/ARCHITECTURE.md` C10 | edited | the 50-word `created_at`/`updated_at` v2-delta bullet split in two at the heal clause |
| `docs/refactor_stage_D.md` | appended | dated correction: the close-out's "four test files" was *three* (`de5de9d` touches `test_dedup_claim`/`test_heal_seam`/`test_journal`) |

`product/scripts/style_check.py` pre-fails repo-wide against its ratchet (known since
the Stage D review round; re-baselining is the founder's call). Touched-lines rule
honoured: these six edits only shrink findings (−7 net) and boil no ocean.

## WP-E1 — `db.py` `created_at`/`updated_at` + the C2-v1-only surface (D27, R1, R3)

| File | Action | Why |
|---|---|---|
| `app/db.py` | edited | `context_records` splits `ingest_time` → `created_at` + `updated_at` (schema, module header); NEW `_migrate_context_v2` — rename + backfilled add + index swap, run *before* `_SCHEMA` (an index on a migration-added column cannot be created late); `put_context` rewritten: the byte-compare *is* the upsert (`ON CONFLICT DO UPDATE … WHERE record_json <> excluded`, one atomic statement, rowid-stable), R2 both-window cache invalidation on change, nothing on a no-op; docstring rewritten — the "stays inside a rendered window" rationale is the premise D27 deletes; `list_context_by_ingest` → `list_context_by_updated`; `earliest_ingest_time` → `earliest_updated_at`; the window floor and ledger prose move to the new axis |
| `app/schemas.py` | edited | `C2_ID` → `c2_processed_record.v1.json` (R1: the branch validates v1 *exclusively*); `validate_c2` gains the contract-demanded fullmatch on `pipeline_version` + every slot `version`, patterns read *from the loaded schema* (Python's `$` admits a trailing newline; `record_id` is closed by its 64-char bounds instead) |
| `app/models.py` | edited | the v0 C2 mirror (kind/text, enrichments, discriminator, `processed_at`, `source.modality`) replaced by the v1 mirror: six typed slot models, strict everywhere, root modality, D17 trio incl. `device_clock`; restated from the contract, never imported from DP (the `ids.py` precedent) |
| `app/daylog.py` | edited (mechanical) | the *axis* moves at E1, the *renderer* at E2: `select_dialects` keys latest `updated_at` (rowid tiebreak now noted as load-bearing); the store read renamed; v0 kind/discriminator selection logic untouched until WP-E2 |
| `app/main.py` | edited (docstring) | the day-log line names the `updated_at` axis; the C2 route blurb stays shape-identical |
| `scripts/daylog_parity_diff.py` | edited (mechanical) | stamp forcing moves to the split columns; P1 pinned to the **v0** schema explicitly (the fixtures are the v0-world inputs both proof renderers consume) with a dated note — WP-E4 re-cuts the whole proof over v1 |
| `tests/conftest.py` | rewritten (C2 half) | `make_c2` builds C2 **v1** (slots map, root modality, 64-hex L3 `record_id` — NUL-joined exactly as DP derives it); slot builders (`slot_asr`/`slot_transcript`/`slot_caption`/`slot_ocr`/`slot_acoustic`) shared with the later WPs |
| `tests/test_created_updated.py` | NEW (TDD) | 12 tests: first landing mints `created_at == updated_at`; byte-identical re-POST leaves the row *completely* untouched (stamps, rowid, json — the heal no-op and §4 crash-replay inputs); byte-different bumps `updated_at`, preserves `created_at` + rowid; R3 slot-*regressing* re-POST replaces unconditionally; hole-migrated re-POST (byte-different-but-not-fuller) replaces; stamp keeps the `_TS_FMT` spelling; R2 both-window invalidation + no-op invalidates nothing + per-user; migration rename/backfill/index-swap + idempotence |
| `tests/test_context.py` | rewritten (8 → 18) | v1 round-trip/upsert/ordering/isolation kept in v1 terms; NEW pins: the v0 shape rejected wholesale *and* per-concept, empty slots map legal, empty-string slot values land verbatim (L11), unknown slot fails closed, `record_id` 64-hex bounds, `pipeline_version` + slot-version grammar incl. the trailing-newline 422, mirror↔schema agreement, D17 trio verbatim |
| `tests/test_windows.py` | edited | `_land` forces both stamps; rule 4 and the floor read the `updated_at` axis; migration test covers the D27 rename; index assertions on `idx_context_user_updated` |
| `tests/test_daylog.py` | edited (mechanical) | axis + fixture plumbing only (the v2 rewrite is WP-E2): store-level writes for the v0-content fixtures (the HTTP gate is v1-only), `_land_v0` helper, and ONE replaced test — the "rewritten record re-renders in place" cache test is the premise D27 deletes; its successor proves the record *dissolves out* of the rendered window and re-enters the next (R2, deterministic ledger clock) |
| `tests/test_discriminator.py` | **deleted** (11 tests) | its whole subject — the v0 within-chunk `discriminator` — is on the charter's dead-concepts list (D24); v1 carries no such field and the v1-only gate rejects it, so every test either loses its subject or duplicates the new v0-rejection pins in `test_context.py`. Nothing ported |

In-session decisions:

- **Packaging: E1 moves the axis, E2 rewrites the renderer.** Every ledger/read/dedup
  site speaks `updated_at` from this commit; the v0 renderer keeps rendering v0-shaped
  content at the *store* level until WP-E2 lands the slot-walk. Each WP commit stays
  green and no dual-version ingest/render support is built (R1) — the HTTP surface is
  v1-only from this commit on.
- **Migration is rename + backfilled add, not rebuild.** `ingest_time`'s data is
  already `created_at`'s truth (first landing, preserved across reprocess), so RENAME
  keeps history verbatim; `updated_at` backfills equal — the landing is the last change
  the old world could know about. Runs before `_SCHEMA` (index ordering); the added
  column is nullable on a migrated DB (`ALTER … ADD` cannot carry NOT NULL), documented
  where it happens. OD-2 wipes `/context` at cutover regardless.
- **The byte-compare is the upsert itself** — `DO UPDATE … WHERE record_json <>
  excluded.record_json`: a filtered-out update touches nothing (stamps, rowid, bytes),
  atomically, with change-detection off `total_changes`. The compare runs over
  storage's canonical serialization, which DP's byte-identical wire bytes reproduce
  exactly (T-1's sorted assembly).
- **`updated_at` stays second-granularity** (the brief's either/or): `_TS_FMT` is the
  one spelling of every storage-minted stamp and the lexicographic-order discipline
  hangs off it. The rowid tiebreak is therefore documented as LOAD-BEARING at the mint
  site and the dedup site, and the within-one-second tie is pinned by test.
- **R2 lives in `put_context`**: on change, invalidate the windows containing the
  *previous* `updated_at` and the new one; on a byte-identical no-op, nothing — an
  unchanged record cannot go stale, which is the honest successor of the old
  single-window invalidation comment.

Evidence: TDD red first — `pytest tests/test_context.py tests/test_created_updated.py
tests/test_windows.py -q` → `86 failed, 16 passed` against the shipped app (v0 `C2_ID`
pin, no `created_at` columns, no `earliest_updated_at`); after implementation the same
three files → `108 passed`. Full storage suite → `321 passed` (was 310: −11
discriminator, +10 context, +12 created_updated; daylog/windows/civil counts
unchanged). The parity proof re-runs green over the split columns (31 checks; P1
explicitly v0 until the WP-E4 re-cut).

## WP-E2 — `daylog.py` v2: the slot-walk renderer (D28)

| File | Action | Why |
|---|---|---|
| `app/daylog.py` | REWRITTEN (renderer core) | the record loop walks `content.slots`: `caption` → Scene · `ocr` → World text (OCR) · `transcript.splits[]` → speaker-bucketed speech, each split in its OWN bucket by its OWN `t_start` (both spellings — verbatim `Z` root spans and the `+00:00`-microsecond form — via `_parse_ts`); the RULED fallback: transcript *absent* → `slots.asr`, spk null (splits by own `t_start`; the split-less whole-chunk shape at the record's `t_start`); present-but-empty transcript = an aligned-and-empty claim, no fallback; holes render as absence, `""` values are honest claims, empty slots map renders nothing (L11); NO acoustic/diarization route (the contract names none; acoustic's consumer marker stays honestly speculative); dedup collapses to `(chunk_id)` — latest `updated_at`, rowid tiebreak (D28 verbatim); module docstring rewritten to the v2 story |
| `app/daylog.py` stamps | edited | `DAYLOG_FORMAT_VERSION = "2"`; default recipe → `consolidation-v2.0`; `daylog_body` emits contract version "2" |
| `recipes/consolidation-v2.0.json` | NEW (additive) | forks v1.1 with every knob byte-identical; the note records why the fork exists (D28: `recipe_id` enters stage keys and C5 lineage, so v2-rendered corpora must not share an id with v1-rendered ones) and the Stage F stamp-teaching gate. Additive on purpose: the LIVE service reads this tree's `recipes/` per request and never asks for the new id |
| `app/schemas.py` | edited | `C10_ID` → `c10_daylog.v2.json` — the v2 schema now gates every served body |
| `app/models.py` | edited | `DayLogBody` mirrors v2 (`version: "2"`) |
| `tests/test_daylog.py` | REWRITTEN (46 → 58) | PARTs 1–3 rebuilt over v1 fixtures (the v0-content shims from WP-E1 die); NEW pins: dedup key is `(chunk_id)` alone + version-forward renders once end-to-end + orphan-chunk defensive key; both split-timestamp spellings bucket by instant and keep their verbatim `t`; the asr fallback (with and without splits) + no-fallback-when-present-but-empty + honest-silence/ran-and-empty/hole-absence + no acoustic route; the v2 stamps and the v2-recipe-knob agreement; PART 4 — the heal×window matrix, all three Stage D shapes |
| `tests/test_windows.py` | edited (1 test) | the trailing-newline trap test's day-log case becomes valid-v2-except-the-forged-id, so the rejection stays about `window_id` (the c10 v2 file joins the width-bounds closure — the Stage A carry) |

In-session decisions:

- **Acoustic stays unrouted.** The brief's routing list and the c10 v2 contract name
  caption/ocr/transcript(+asr fallback) and nothing else; Stage C left acoustic's
  consumer marker `speculative:c10_ambient_route_unruled` for Stage E, and the ruling
  the brief implies is *no route* — routing it would be a contract edit this stage was
  not given. Recorded under Noticed for Stage F.
- **Fallback fires only on an ABSENT transcript slot.** Present-with-empty-splits is
  an aligned-and-empty claim the renderer must not second-guess (L11); absence — a
  hole, a permanent hole, or a dialect that never attempted alignment — falls back to
  `slots.asr` with speakers unlabeled, exactly the 2026-08-06 ruling.
- **The parent `asr` witness never renders on top of its aligned view** — one witness
  on two channels is one voice in the day-log (the L11 provenance corollary).
- **PACKAGING, loud (the Stage C mid-stage precedent):** the renderer swap necessarily
  breaks the old v0-fixture differential proof — a slot-walk renders nothing from
  per-kind records, so `tests/test_daylog_parity.py` is RED at this commit (4
  failures, all in that file; every other suite green: 334 passed). The proof and the
  renderer are one seam; the re-cut IS WP-E4's substance and lands in the next commit,
  which restores full green. WP-E3 follows it, keeping the red window to one commit.

Evidence: the v2 suite watched red by stash-revert of the three app files against the
WP-E1 renderer — `40 failed, 18 passed` (v1 fixtures render nothing through the v0
kind loop; the 18 that pass are grid/zone/HTTP-failure tests with no slot content at
stake) — then `58 passed` restored. Full suite at this commit: `334 passed, 4 failed`
(the four = the old parity proof, disclosed above).

## WP-E4 — the D20 parity re-baseline (run before WP-E3, closing WP-E2's red window)

| File | Action | Why |
|---|---|---|
| `scripts/daylog_parity_diff.py` | re-cut | the differential now feeds each side ITS OWN SHAPE of the SAME content: continuum's untouched v0 reference renderer over the 27 v0 originals, storage's v2 slot-walk over `fixture_records_v1()` — 24 hand-built v1 equivalents (each caption/ocr pair on one chunk folds into ONE record per L2; transcripts → `asr` slots; the diarized one → a `transcript` slot with splits + its unrendered asr witness; chunk-a3 carries the full video dialect with ocr *absent* — an honest hole that must render as absence). N4 joins the neutralised divergences; P1 validates both shapes against their own schemas (v1 through the service's fullmatch gate); P2 proves the 1:1 (chunk_id, t_start) pairing *and* the landed order; P4 uses the D28 dedup key; P5 gates against `c10_daylog.v2.json` |
| `scripts/daylog_parity_diff.out.txt` | regenerated | the committed baseline record D20's card expects: `PASS — all 31 binding checks hold (8 preconditions, 15 tier A byte-identical, 8 tier B proven-equivalent), over 2 window origins: grid-aligned, misaligned` |
| `tests/test_daylog_parity.py` | edited (header) | the re-baseline note; the check-id set and counts are UNCHANGED, so the tripwire's pins (`P1–P6`/`A1–A8`/`B1–B4`, 31 checks, both origins, one genuinely off-grid) carry over intact |
| `product/DECISIONS.md` D20 · storage `CHARTER.md` M9 | stamped | status-line-only re-baseline records (the Stage A back-edit precedent; decision text untouched), D28 noted on both — the joint-row follow-through |

What tier A now proves, said precisely: re-cutting the record shape (v0 per-kind pairs
→ v1 slot maps) *and* the renderer around it changed NOTHING the trainer can see —
block text, ordering, ids, anchors, quality and segment payloads byte-identical to the
untouched reference, over a grid-aligned *and* a misaligned window origin. That is
D20's "the block text is contract" sentence made executable across the rebuild, and it
is why the c10 v2 contract could say "the v2 re-baseline is the differential proof
re-run, not a label change".

Hand-posted exit-criterion demo (a REAL socketed instance on a scratch port/DB, three
hand-written v1 records over `POST /context/records`, `delta=0`, then the fetch):

```
POST /context/records ×3 → {"ok":true,"record_id":"0c3b1bab…"} / "d640d270…" / "b4a466a6…"
POST /training/windows → w20260806T212514Z
GET  /training/daylog  → contract C10 version 2 · daylog_format_version 2 ·
                         recipe_id consolidation-v2.0 · home_tz America/New_York
block b0000:
  On 2026-08-06, around 14:00–14:00 local time:
  Scene: a whiteboard covered in storage diagrams
  Heard: speaker-0: the byte compare is the upsert | and convergence is the guarantee | ship the worklog with the commit
  World text (OCR): STAGE E — STORAGE V2
```

— the aligned speaker named, the null-speaker split and the asr-fallback record both
unlabeled, caption and ocr routed, the anchor in the profile zone (18:00Z → 14:00
New York). Scratch instance torn down after; live fleet 200/200/200.

Evidence: proof re-run → exit 0, all 31 checks green (report committed);
`tests/test_daylog_parity.py` → `8 passed`; full storage suite → `338 passed` (the
WP-E2 red window closed).

## WP-E3 — E-2 whole-record retraction, finally built (D28; charter M5)

| File | Action | Why |
|---|---|---|
| `app/db.py` | extended | NEW `Store.retract_context(user_id, *, record_id, chunk_id, pipeline_version, dry_run)` — selectors AND over the mandatory `user_id`, at least one required (a selectorless call raises: the full-user wipe is M5's *other* primitive); whole records only; auditable manifest with counts by `pipeline_version` (D28); the day-log cascade invalidates every cached window containing an affected `updated_at` (the corrected-`home_tz` mechanism); dry-run returns the identical manifest and touches nothing; retracting nothing is an honest zero, not an error. The docstring states the LEDGER BOUNDARY plainly |
| `app/main.py` | edited | `DELETE /context/records` → the manifest; 422 `{"error": "no selector"}` on a selectorless call; the route comment restates the boundary (retention, never correctness; redelivery skips upstream; rebuild = OD-2 replay or a version bump) |
| `app/models.py` | edited | `RetractionManifest` + `RetractionSelector` (strict; storage-minted body → response model; no contract file — E-2 is a service-owned primitive whose shape is pinned on the D28 card, for Platform M2 to call) |
| `tests/test_retraction.py` | NEW (TDD) | 12 tests: the three selectors (by-record, by-chunk takes the version-forward lineage *beside*, by-version), AND-composition, selectorless 422 that wipes nothing, `user_id` required + cross-user attempt fails closed with an honest zero, idempotent zero-manifest retry, dry-run parity with the wet manifest, the day-log cascade (renders *without* the retracted record; spans windows; bystander user survives), `/raw` blobs untouched (bytes are sacred), and the ledger-boundary drill |
| `CHARTER.md` (storage) M5 | edited | E-2's rules block replaced with the built whole-record truth (the kind-granular board shape retired unbuilt, D28); the time-slice delete explicitly stays M5's own unbuilt primitive; "deletion is never the mechanism for correctness" stands verbatim in §Retention; dated How-it-got-here entry + Last-updated stamp |
| `HANDOFF.md` (storage) | rewritten in place | today-state: the WP-E0a repoint recorded on the status line; §Incoming flips D27/D28 to built-on-branch with the Stage F stamp-teaching gate; §Next item 1 flips to built-with-remainder (Platform M2, reservoir leg, time-slice delete) |

In-session decisions:

- **The ledger-boundary drill simulates the skip reply** (the brief's "or" branch): DP's
  claim tree decides from its OWN ledger, never a storage read (a Stage D decision), so
  the wire fact to pin is that a redelivery after retraction produces *no write here* —
  the drill retracts, states the D16 skip shape (`200 {ok, record_ids:[rid]}` with a
  record_id storage no longer holds), and asserts the record stays 404 with zero rows.
  The full cross-service replay drill belongs to Stage F's drill list.
- **The reservoir is deliberately NOT in E-2's cascade**: reservoir corpora are
  window-granular artifacts, not record-granular ones — no record selector maps to an
  admitted corpus. The full-user delete (M5, unbuilt) owns that leg; the charter card
  says so now instead of implying otherwise.
- **`/raw` untouched, pinned by test**: bytes are sacred (OD-2); E-2 retracts processed
  records only.

Evidence: TDD red first — `tests/test_retraction.py` → `11 failed, 1 passed` against
the shipped app (405 on the DELETE route, no `retract_context`); green after: `12
passed`. One fixture corrected mid-round, disclosed: the isolation test originally gave
two users the SAME chunk_id, and L3 (record identity = chunk_id ␀ pv, no user
component) made the second POST upsert the first user's row — real contract behavior,
not a defect; chunk ids are globally unique ULIDs in production, so the fixture now
models that and adds the fails-closed cross-user assertion. Full storage suite →
`350 passed`.

## Noticed for later stages

- **Stage F — the continuum stamp-teaching gate (the brief's own carry, now concrete):**
  the v2 stamps are `daylog_format_version "2"` and `recipe_id "consolidation-v2.0"`.
  Continuum's stamp-refusal (built, F3) will — correctly — block every window until it
  is taught both; teach them (and copy `consolidation-v2.0.json` into continuum's local
  registry: recipes are deliberate copies, immutable under their id) *before* cutover.
  The C10 body's contract `version` is now "2" as well, so any continuum-side body
  validation needs the v2 schema at the same moment. A cutover gate, not a bug.
- **Stage F — the VLM endpoint decision (Stage C's carry, restated):** clipcap needs
  `VLM_URL` (+key/timeout) pointing at an endpoint actually serving
  Qwen/Qwen3-VL-32B-Instruct; it sits outside the manifest identity scheme — the
  startup `/v1/models` probe suggestion stands.
- **Stage F — recording's 501-retry note (named by the brief):** the rebuilt DP answers
  a clean 501 for a modality with no registered pipeline. Before cutover, verify
  recording's push-retry taxonomy treats 501 as non-retryable — a retryable 501 on an
  image/text chunk would loop forever against the v1 fleet.
- **Stage F — the repoint inventory (what runs where, verified 2026-08-06):**
  - `:8083` storage — code from `/home/ubuntu/nmn/dp-v0-live` (main); interpreter =
    this tree's `storage/.venv` via a worktree symlink (`.git/info/exclude` carries the
    `.venv` entry); data pinned to THIS tree (`app/dev.db`, `app/raw_store`,
    `app/reservoir`); `recipes/`+`policies/` read from THIS tree per request
    (immutable-under-id keeps that safe; branch edits must stay additive).
  - `:8085` DP v0 — worktree code, `.venv-learn` interpreter, `DP_VAR_DIR` pinned to
    the worktree (Stage C).
  - `:8084` recording — still THIS tree's code + `.venv-learn`; never touched on the
    branch; Stage F must repoint or absorb it at cutover.
  - `:8097` (OCR sidecar from a deleted worktree) and `:8099` (a Jul-24 storage smoke
    on a scratchpad DB) both still answer 200 — not deploy-managed, not ours to stop
    this stage; the Stage B/G ownership notes stand.
  - `learn.env` now pins the five `STORAGE_*` data paths (with `SERVICES_ROOT` +
    `DP_VAR_DIR` from Stage C) — machine-local operational config to carry through
    cutover.
- **Stage F — acoustic stays unrouted in C10 v2** (this stage's ruling, from the
  brief's closed routing list): if ambient-sound tags should ever train, that is a
  founder contract edit, not a renderer patch; the stage's `speculative` consumer
  marker stays honest until then.
- **Stage G — the parity proof's reference side retires with cutover:** once
  continuum's local renderer is deleted (M9's own condition — the narrowed diff is
  green, re-baselined), the differential loses its left side. Decide then whether the
  proof retires with condensed history or becomes a storage-only golden pinned to the
  committed baseline report.
- **Stage F/G — the OD-2 wipe meets the D27 ladder cleanly either way:** the wipe
  clears `/context` rows, not the DB file; `_migrate_context_v2` no-ops on an
  already-migrated table, migrates a pre-E one, and (review round) is re-entrant under
  kill-9 at any ladder step — so the cutover order cannot strand a DB shape.
- **Stage F (added 2026-08-07) — bound the dry-run retraction's SQL variables:** the
  blast-radius predictor binds two variables per distinct `updated_at` stamp in one
  OR-clause, so a selector matching more than ~16k distinct stamps 500s on dry-run
  while the wet path (one DELETE per stamp) succeeds. Unreachable at pilot scale;
  bound it (chunk the ranges, or stage the stamps in a temp table) before Platform M2
  starts calling the endpoint.
- **Stage F (added 2026-08-07) — the `/context` ack echoes the body's `record_id`:**
  `put_context` returns `record["record_id"]` — the schema-gated 64-hex id from the
  posted body, never a value re-read from the row. Fine under the v1 gate; a consumer
  reconciling acks against rows should know the echo is the caller's own id.
- **Stage F checklist (added 2026-08-07; promoted from the repoint inventory) — the
  recipes/policies live↔branch coupling:** the live worktree service reads
  `recipes/` + `policies/` from *this* tree per request while its code runs from the
  worktree, so until cutover every branch edit there must stay additive (the v2 recipe
  was). The coupling dissolves at cutover when everything is one tree again — the F
  checklist should then retire the five `STORAGE_*` `learn.env` pins and the worktree
  `.venv` symlink alongside the repoint itself.

## 2026-08-06 — Adversarial review round (six lenses, skeptic-verified; fixes applied)

> review · a 22-agent workflow over the full Stage E diff (`139b1ce^..HEAD`): six
> reviewer lenses — D27-law / D28-renderer / sqlite-mechanics / contract-surface /
> parity-proof honesty / test+worklog honesty — each raw finding then attacked by an
> independent skeptic against the actual code, the ratified rows and the recorded
> rulings, with live reproduction (incl. real kill-9 fault injection).
> Arithmetic: 16 raw findings → **9 confirmed** (2 major · 4 minor · 3 nit; the
> migration-atomicity defect was found by two lenses, the dry-run divergence by two)
> → **6 distinct defects**, all resolved in this round's commit. 7 refuted, recorded
> below. The d28-renderer lens returned zero findings.

**Code fixes (TDD — all four new/changed tests watched red first, `5 failed` against
the shipped code):**

- **`_migrate_context_v2` made re-entrant under kill-9 (major, found twice).**
  python-sqlite3 autocommits DDL, so the three-step ladder could not ride one
  transaction — and its single guard keyed on the FIRST step's effect meant a crash
  between RENAME and ADD left a permanently unbootable store (`no such column:
  updated_at` from `_SCHEMA`'s index, every boot), while a crash before the backfill
  committed stranded NULL-`updated_at` rows invisible to the entire window axis. Both
  shapes were reproduced by the reviewers with real kill-9 fault injection. Fix: every
  step independently conditional/idempotent, plus an unconditional NULL backfill (a
  NULL stamp can only be a mid-ladder shape — `put_context` always writes it). Tests:
  `test_migration_reenters_after_a_crash_between_rename_and_add` +
  `test_migration_heals_null_updated_at_rows` — both red first.
- **Dry-run manifest made genuinely identical (major, found twice).** The docstring
  and the WP-E3 worklog row said "identical manifest"; the code returned
  `day_logs_invalidated: 0` on every dry run — an audit preview that under-states the
  blast radius. The dry path now PREDICTS the cascade (a distinct-row count over the
  same window ranges the wet path deletes); the rewritten test pins
  `wet == {**dry, "dry_run": False}`.
- **`retract_context` reads and deletes under one `BEGIN IMMEDIATE` (minor).** The
  selector read ran in autocommit before the deletes, so a racing write could make the
  manifest describe rows the delete did not take.
  `test_the_manifest_read_and_the_delete_share_one_transaction` (a connection probe
  asserting `in_transaction` at read time) — red first.
- **Non-finite `acoustic.confidence` closed at the schema gate (minor).** The json
  parser admits the NaN/Infinity literals and jsonschema's bounds are vacuously true
  for NaN, so the pydantic mirror 500'd on what the schema gate passed — the exact
  two-gates-in-series trap the D17/discriminator history warns about. `validate_c2`
  now rejects non-finite confidence beside its other Python-validator trap closures;
  `test_a_non_finite_confidence_is_a_422_not_a_500` sends the raw wire bytes
  (stdlib `json.dumps` emits `NaN` by default) — red first.
- **The parity module's N1 bullet corrected (minor, paper):** it still claimed both
  paths see "the same 27 records", P2 proving it, on the `ingest_time` axis — all
  three stale against the re-cut (24 v1 equivalents, P2 proves the pairing, the axis
  is `updated_at`). Docstring only; the checks and the committed report were already
  truthful.

**Worklog corrections (quote-and-correct; the committed sections above stand):**

- WP-E1's evidence line "after implementation the same three files → `108 passed`"
  was wrong twice: the three red-run files collect and pass **102**; the 108 figure
  came from a four-file green run that silently included `test_civil_time.py`'s 6.
  The red figure (`86 failed, 16 passed`) and the suite total (321) were verified
  accurate.
- WP-E0b's "−7 net" style-findings claim does not match the checker: measured, the
  six edited files went **−6** (repo-wide 816 → 811 = **−5**, including this new
  worklog's own +1 finding). The named nits were all verified fixed; only the
  arithmetic was off.

**Refuted (recorded so the next reader does not re-litigate):** the `put_context`
prior-read race (two lenses; the SQL observation is accurate but the failure scenario
is unreachable in the system as built, and the claimed trigger was factually wrong);
the cross-user same-`record_id` cache-stranding (reproduces, but the user-keyed
invalidation predates the stage and the scenario requires a record_id changing owners,
which L3 + globally-unique chunk ULIDs exclude); the N4 pairing "weaker than
disclosed" (P2's check text states exactly what it proves, and content drift is tier
A's to catch — the disclosure is accurate); the WP-E2 stash-revert (`40 failed, 18
passed`) and WP-E3 (`11 failed, 1 passed`) red claims called irreproducible — both
reproduced byte-for-byte by the skeptics; the ledger-boundary drill "asserts on a
reply it constructs" (a recorded in-session decision executing the brief's authorized
simulate-the-skip branch, with the cross-service replay named as Stage F's).

**Verification after all fixes:** review-fix tests red first (`5 failed` against the
shipped code), green after; full storage suite → `354 passed`; the parity proof
re-runs PASS (all 31 checks; the docstring edit touches no check); DP → `573 passed,
4 skipped` and continuum → `262 passed, 7 skipped` (re-run after the fixes, below).

## Exit criteria (§8 Stage E + the session brief)

| Criterion | Status | Evidence |
|---|---|---|
| Live-service repoint before any storage change; data paths preserved exactly; e2e verified | done | WP-E0a: pid 330817 from the worktree, ≈4 s window, all five `STORAGE_*` byte-identical, fresh chunk → same `dev.db` (2 → 4 records) |
| Hand-posted v1 records render a correct day-log (pasted) | done | WP-E4: three hand-written v1 records over a real socket → the C10 v2 block pasted (speaker line, null-speaker line, asr fallback, Scene, World text, home-zone anchor) |
| Retraction drill passes; ledger boundary stated plainly | done | WP-E3 (+review round): `test_retraction.py` 13 tests incl. retract-then-skip no-resurrection; docstrings + route + charter M5 state the boundary |
| heal×window matrix green — all three Stage D shapes | done | WP-E2 PART 4: filling heal re-windows; byte-identical still-holey does not; hole-migrated re-windows and renders the new truth |
| D20 parity re-baseline recorded the way the card expects | done | WP-E4: 31 checks green over both origins, tier A byte-identical across the renderer swap; report committed; D20 + M9 status lines stamped |
| Full storage suite green; every deleted v0 test dispositioned | done | `354 passed` (was 310); `test_discriminator.py` deleted with disposition, every rewritten file's delta itemized per WP |
| DP suite untouched-green | done | `573 passed, 4 skipped` — the exact Stage D close-out count; diff touches no DP code (paper + worklogs only) |
| continuum suite untouched-green | done | `262 passed, 7 skipped`; no continuum file touched |
| No new env knobs | done | storage's operational set is unchanged (`STORAGE_DAYLOG_RECIPE_ID` etc. predate the stage; the recipe bump is a code default + an additive artifact) |
| Live v0 services healthy all stage | done | `:8083`/`:8084`/`:8085` → 200 at WP-E0, after WP-E4's scratch drill, and at close; the stray `:8097`/`:8099` untouched |
| One commit per WP, worklog in the same commit; onboarding strays uncommitted | done | `139b1ce` E0 · `3443697` E1 · `f1c719c` E2 · `5f874c2` E4 · `f943094` E3 · this closing commit (review round + close-out); the two onboarding files remain uncommitted |
| Stage F not started | honored | zero cutover work; the Stage F carries are in "Noticed" above |

Deviations, restated in one place: WP-E4 ran before WP-E3 (to close the one-commit red
window the renderer swap forced — the Stage C mid-stage precedent, disclosed in
WP-E2); the parity re-cut rode WP-E4 as the renderer's one seam. No design questions
for the founder arose: every ambiguity resolved inside the ratified rows and the
session's rulings, and each resolution is recorded in its WP's decision list.

**Status: DONE.**

## 2026-08-07 — Paper close-out round (independent verification; paper-only)

> cleanup · applied on `dp-rebuild-v1`, one commit · triggered by an independent
> verification that confirmed Stage E with zero code defects and directed exactly the
> items below. Everything above stands as written; corrections amend, never rewrite.
> Stage F not started.

**Style fixes — the findings this stage's own M5/board edits introduced:**

- storage `CHARTER.md`: the M5 card's two ALL-CAPS tokens (whole, skips) → italics.
- storage `HANDOFF.md`: the five ALL-CAPS tokens (running ×2, live ×2, taught) →
  italics; the §Incoming D28 bullet (43w) and C2-v1-only bullet (45w) trimmed under
  the cap; §Next item 1's 41-word cell cut to 20 (the dropped reservoir-leg reasoning
  lives in WP-E3's decision list and the M5 card). The D27 bullet was also tightened,
  though it had not flagged — the 43w target was the D28 bullet, found by measuring.
- Old-vs-new on the two files, measured with `style_check.py` at `139b1ce^` vs this
  round: CHARTER 0 → 0, HANDOFF 7 → **6** (all eight stage-introduced findings
  removed; the rewritten §Next row also retired one pre-existing cell finding).
  Net −1 ≤ 0.

**Worklog corrections (quote-and-correct):**

- The review round's "repo-wide 816 → 811 = −5" was false at the stage tip: measured
  at `f21e09c` with the same script, the repo-wide count is 816 → **909** (+93),
  dominated by this worklog's own 90 findings — worklog files count toward the ratchet
  like every other `product/*.md`. The −5 was a measurement that predated this
  worklog's growth, quoted as if current; the per-file half of that correction (the
  six WP-E0b-edited files went −6) was verified and stands. The scoping lesson: a
  net-findings claim must name its file set *and* its commit, or it silently narrows
  to the flattering scope.
- The WP-E4 heading was split across two `##` lines (rendering as two headings);
  re-joined as one, wording condensed — formatting only.
- The DP `CHARTER.md` 2026-08-06 L8 changelog bullet, left at 55 checker-words in
  WP-E0b after its em-dash fix, is now under the 42-word cap; the trimmed half of its
  "was" quote ("same stage fails again") survives verbatim in the Stage D close-out
  section and on the D27 card, so no provenance is lost.

**Noticed for Stage F — three additions** (appended, dated, in the list above): the
dry-run SQL-variable bound, the ack-echoes-the-body's-`record_id` note, and the
recipes/policies live↔branch coupling promoted to the F checklist explicitly.

**Verification re-run (2026-08-07, after all edits):**

```
$ python3 product/scripts/style_check.py --all | grep -E 'storage/(CHARTER|HANDOFF)'
product/services/storage/HANDOFF.md:112: rule1-cell: 42 words      # pre-existing ×4
product/services/storage/HANDOFF.md:112: rule1-cell: 27 words
product/services/storage/HANDOFF.md:114: rule1-cell: 31 words
product/services/storage/HANDOFF.md:116: rule1-cell: 25 words
product/services/storage/HANDOFF.md:45:  rule6-bullet: 46w > 42    # pre-existing ×2
product/services/storage/HANDOFF.md:53:  rule6-bullet: 56w > 42
# CHARTER 0 (was 0) · HANDOFF 6 (was 7); data-processing/CHARTER.md:27 no longer flags
$ storage ./.venv/bin/python -m pytest -q
354 passed, 1 warning in 15.88s
```

Status stays **DONE**; Stage F not started.
