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

## WP-C1 — `app/stagegraph/stage.py` rewrite (uniform Stage)

| File | Action | Why |
|---|---|---|
| `app/stagegraph/stage.py` | REWRITTEN (326 → ~250 lines) | the uniform Stage{name, modality, stage_version, backend, needs, slot, required, byte_budget, one-of run_sync\|run_async}; registration checks (unique slot per modality, one-of run methods, segment grammar); kinds/mutate/writes/mutable_slots/SlotView/best_effort/order/version_fragment/enabled/assemble/R1 machinery all deleted |
| `app/stagegraph/__init__.py` | edited | exports the new stage surface only; executor/processor exports return with WP-C2/C3 |
| `tests/test_stage_registry.py` | created (TDD, red→green) | 59 checks: the uniform declaration, slot default, segment composition (+ `.exp-` codes), backend-override-at-construction (mock named in the version string), every registration rejection, dead concepts absent from module and class |

In-session decisions (things the plan left to code):

- **`Backend(name, version)` frozen dataclass + constructor override.** "backend …
  resolved in code" is realized as an in-code default on the class plus a
  constructor override (`AsrStage(backend=Backend("mock", 1))`) — how tests and any
  future offline harness select fakes/experiments. Selection is always code; the
  dialect always names it (plan §3's mock rule).
- **Grammar enforced at registration**: stage/backend/slot/experiment names must
  match `[a-z0-9_]+` — the same grammar the v1 contract's `pipeline_version` /
  `slot_version` regexes pin, so an illegal segment cannot exist at runtime.
- **`server` attribute (operational only).** Thin clients need a routing key into
  the model-client pool (`"whisper"`, `"ocr"`…). It is deliberately not part of the
  identity segment: endpoints/replicas are operational (L9); the model behind them
  is pinned by server code + manifest identity.
- **`StageOutput{value, bytes}`.** `value` = the JSON slot value (None = stage
  emits no record slot — the clipprep case; the §2 video example shows slots only
  for caption/ocr). `bytes` = the in-run transient payload the executor frees after
  the last consumer (L5's blackboard `{ref, bytes}`).
- **No Settings anywhere in the stage surface.** `StageContext{c1, blob,
  span_seconds, inputs, clients, metrics}` — a stage structurally cannot read a
  knob; L4 by construction, T-1 backstops `os.getenv` cheating.
- **No `order` field.** Execution is readiness-driven, record slots are a map;
  nothing consumes an ordering. `stages_for` sorts by name for determinism only.
- **`registered_modalities()` moves here** — the first half of folding
  `processing/`'s modality routing into the stage registry (completed at WP-C5).

Evidence: `./.venv/bin/python -m pytest tests/test_stage_registry.py -q` →
`59 passed` (was collection-error red against v0 stage.py before the rewrite).
Transitional note: between WP-C1 and WP-C4/C5 the old stage files + executor are
against the dead API and the v0 DP suite does not fully collect — expected
mid-stage state; the suite disposition lands at WP-C6. storage/continuum suites
are untouched by construction.

## WP-C2 — `app/stagegraph/executor.py` rewrite (resolution + readiness executor)

| File | Action | Why |
|---|---|---|
| `app/stagegraph/executor.py` | REWRITTEN (402 → ~330 lines) | KEPT: readiness TaskGroup, commit-on-success, cancel-and-await-siblings, leaf re-raise (ProcessingError preferred), threadpooled `run_sync`. ADDED: sorted `'+'`-join version composition pre-run (L4); one-producer-per-slot + required-never-downstream-of-optional resolve checks; single-`GraphResult` assembly with executor-stamped slot `version`; byte-budget enforcement at slot emission (`SlotEmitError`, never truncation); blackboard `{value, bytes}` with bytes freed after the last consumer. DELETED: kinds/mutate chains/SlotView plumbing/discriminator guard/`assemble` fan-out |
| `app/stagegraph/__init__.py` | edited | executor exports restored (`resolve`, `run_graph`, `GraphResult`, `GraphResolutionError`, `SlotEmitError`) |
| `tests/test_executor.py` | created (TDD) | 27 checks: composition sortedness + `.exp-` riding; every resolve rejection; concurrency timing; leaf re-raise + ProcessingError preference; cancel-and-await; L7 hole/cancelled-cone statuses; budget exact-boundary (fit passes, +1 fails); binary/non-dict/forged-version/wrong-return emission failures; bytes delivered to consumers and freed (incl. under a cancelled cone) |

In-session decisions:

- **Budget enforced at slot emission (stage completion), not final assembly.** L5
  says "exceeding it at assembly is a stage failure"; enforcing when the stage's
  value becomes record bytes is the only point where L7's "downstream cone
  cancelled" stays coherent (a breach discovered after consumers ran could cancel
  nothing). The measure is `len(utf8(json(emitted slot)))` under the canonical
  serialization (compact separators, `ensure_ascii=False`), INCLUDING the stamped
  `version` key — i.e. exactly the bytes the record will carry for that slot.
- **The executor stamps `version`; a stage supplying its own is a loud failure.**
  The contract's deliberate slot-version redundancy can never drift from the
  dialect segment because there is exactly one writer for both (T-3/T-4 assert it
  end-to-end at WP-C6).
- **Slot values must be JSON objects.** Every v1 contract slot is an object; a
  bare string/list would silently break the `{"version": …, **value}` merge.
- **`value=None` = no record slot, status still `ok`** — the clipprep shape (§2
  names video slots as caption/ocr only). L11 note: a transient-output stage's
  name in the dialect with no slot reads as a hole to a consumer that doesn't
  know the stage; acceptable because nothing consumes such a slot — recorded so
  T-6 documents the distinction via slot-producing stages only.
- **Statuses vocabulary `ok|failed|cancelled`** — exactly L8's done-row set, so
  Stage D can persist `GraphResult.statuses` verbatim into the extended row.
- **Bytes freeing**: refcount = declared dependents; released when each consumer
  finishes (ran, failed, or cancelled), plus an unconditional end-of-run sweep in
  a `finally` (transient payloads never outlive the run on any path).
- **Required-failure leaves no `GraphResult`** — the leaf exception propagates;
  there is structurally no partial record to emit (L2/L6).

Evidence: `pytest tests/test_stage_registry.py tests/test_executor.py -q` →
`86 passed`.
