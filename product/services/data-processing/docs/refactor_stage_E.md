# DP Rebuild — Stage E worklog (Storage v2)

**Stage:** E — Storage v2 · **Status:** in progress · *Dated:* 2026-08-06
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
