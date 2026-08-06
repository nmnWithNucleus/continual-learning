# DP Rebuild — Stage C worklog (New stagegraph)

**Stage:** C — New stagegraph · **Status:** IN PROGRESS · *Dated:* 2026-08-06
**Branch:** `dp-rebuild-v1` · **Plan:** [refactor_dp_service.md](refactor_dp_service.md) §8 Stage C
**Scope:** WP-C0 (live-service pre-flight) · WP-C1 (`stagegraph/stage.py` rewrite) ·
WP-C2 (`executor.py` rewrite) · WP-C3 (`pipeline.py` / `schemas.py` / `config.py`) ·
WP-C4∥ (audio stages as thin clients + `speaker_align`) · WP-C5∥ (video stages) ·
WP-C6 (test spine T-1…T-6 + real-backend e2e). Laws L1–L12 (D23–D28) govern every line.

Carried-over instructions honoured this stage (from Stage A/B "Noticed for later
stages"): T-3 owns `pipeline_version` sortedness; T-3/T-4 assert each emitted
`slot.version` equals its stage's dialect segment; mock-dialect fixtures distinct from
real ones; `schemas.py` mirror is one change with four parts; the OCR client rewire
mapping (old `/ocr` wire + `model_sha_det/rec` → `identity.weights.det_sha256/rec_sha256`
+ the `/infer` envelope); `app.state.vlm_pool` as the ModelClient hand-off hook; AST
caption folding client-side with `ACOUSTIC_TOP_K/THRESHOLD` becoming stage-code pins;
whisper's deliberate dialect change (large-v3/cuda/fp16 vs v0's base/cpu/int8) reflected
in the asr stage's `vB`; carried hardening (starve-proof `/health`, `client_timeout_s`
wire-or-delete, replica re-verify after respawn).

---

## WP-C0 — Live-service pre-flight (v0 moved to a main-pinned worktree)

**The hazard:** the live v0 DP service (uvicorn `:8085`, launched from
`platform/deploy/.venv-learn` by `run_learn.sh`) ran with its working directory inside
this repo's `product/services/data-processing/` — the tree Stage C rewrites. It held v0
code in memory, but any restart would have imported a half-rewritten `app/`.

**Deploy mechanism read first** (`platform/deploy/README-learn.md` + `run_learn.sh`):
plain bash launcher — `( cd "$SERVICES_ROOT/$name" && bash run.sh )` per service,
pidfiles under `deploy/run-learn/`, `learn.env` sourced with `set -a`, no
systemd/supervisor. Matches the plan; no escalation needed. Two mechanism facts made
the repoint clean and surgical:

- `SERVICES_ROOT` is env-overridable (`: "${SERVICES_ROOT:=…}"` runs *after* sourcing
  `learn.env`), so a `learn.env` entry durably repoints every future launch. The
  deploy self-test is immune (it forces `ENV_FILE=…/nonexistent.env`).
- `start_service` *adopts* an already-healthy service instead of restarting it, so a
  bring-up with only DP down restarts only DP — storage `:8083` and recording `:8084`
  were never touched.

**Journal finding:** `dp_var_dir` defaults to `<tree>/var` resolved from `__file__`
(`app/config.py:42`), i.e. it would silently move with the code tree. Empirically
`product/services/data-processing/var/` was **empty** — v0 runs `ingest_mode: inline`
and never created `dp.db` — so there was no journal state to migrate and nothing
pending to recover; recording's at-least-once redrive covers anything in flight.
`DP_VAR_DIR` is now pinned explicitly (worktree `var/`, the same value the worktree's
default resolves to) so the journal location can never drift again.

| Action | Detail |
|---|---|
| Worktree | `git worktree add /home/ubuntu/nmn/dp-v0-live main` → main @ `9307b7e`, own `var/` created |
| Repoint | `learn.env` (gitignored, operational-only) += `SERVICES_ROOT=/home/ubuntu/nmn/dp-v0-live/product/services` + `DP_VAR_DIR=…/dp-v0-live/…/data-processing/var`, dated comment |
| Restart | one tight window: `kill -TERM 3356422` → port free in <1 s → `bash run_learn.sh --skip-install` → healthy |

**Evidence (2026-08-06):**

- Before (06:15:39Z): `:8085` → 200 `{"ok":true,"asr_backend":"faster_whisper",…}`,
  pid 3356422, cwd → `…/continual_learning/product/services/data-processing`.
- Window: TERM 06:16:25Z → `data-processing healthy (pid 3835816)` 06:16:30Z (~5 s).
- After: pid 3835816, cwd → `/home/ubuntu/nmn/dp-v0-live/product/services/data-processing`,
  env carries the same wiring (`ASR_BACKEND=faster_whisper ASR_LANGUAGE=en` + pinned
  `DP_VAR_DIR`), `:8085` → 200, same health body.
- Siblings untouched: storage still pid 3356393, recording still pid 3356458 (compared
  via `ss -tlnp` before/after).
- Chunk processed after restart: fresh 7 s WAV (unique bytes, so dedup could not
  shortcut) → `POST :8084/capture/run` (`chunk_seconds=4`) → 2 chunks emitted, 2
  record_ids returned; `GET :8083/context/records/10228cf4…` → HTTP 200, a C2 **v0**
  record (`content.kind: transcript`, `pipeline_version: asr-fw-v1`) — the v0 wire,
  byte-for-byte the old world, now served from the worktree. Empty transcript is the
  honest result for a synthetic tone under VAD.

v0 is now immune to anything Stage C does to this tree. The single authorized restart
is spent; no further v0 restarts this stage.
