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
| exclude | `.git/info/exclude` gains `.venv` (the repo's `.venv/` pattern matches directories only, so the symlink would read untracked); machine-local, not a repo change |
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
