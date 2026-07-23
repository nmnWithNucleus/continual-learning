# WS2 — Morpheus port (the real nightly-consolidation core)

**Status:** ready to build — reproduction baseline landed (Phase 1 ✅), architecture + decisions
locked (cofounders, 2026-07-23) · **Owner:** a dedicated implementation session, works on the
cluster node (data + H100 + envs are there) · **Supersedes:** the retired `ws-engram-port.md`.

> **Morpheus** = our nightly-consolidation core. It is *our* service; we do not use the
> upstream experiment's nomenclature. Methods/recipe derived from the nucleus-research
> consolidation line @ commit `b3c58e1` (provenance only — cite the commit, not its name).
> Everything ships under `continuum/app/morpheus/` and is versioned on every method change.

---

## 0. What this workstream is

Reproduce the validated nightly-consolidation recipe **inside our services**, as lean,
production-grade, tested code — with a **parity harness** proving we match the research
numbers. This is NOT a code copy: his code is experiment-grade; we reimplement cleanly and
let the parity tests be the contract that we reproduced the behavior.

The learn loop we are building:
```
DP → storage /context (C2, faithful)
        │  storage-owned (see storage charter expansion): DAY-LOG view, RECIPE registry, RESERVOIR, MODEL DIR
        ▼
  Morpheus nightly (continuum):  fetch recipe → fetch day-log → amplify → finetune → gate → publish(C5)
```

Continuum is deliberately thin: **fetch recipe · fetch day-log · amplify · finetune · gate ·
publish.** Everything data-shaped (day-log build, recipe hosting, reservoir) lives in storage;
everything recipe-coupled-training lives in Morpheus.

## 1. Decisions locked (do not relitigate — build to these)

| # | Decision |
|---|---|
| Recipe | **v1.0: 48× amplification + 15% deny-then-correct (`neg_frac=0.15`) in amplification + LoRA r128/α256 CPT 3ep + 30% replay.** This is the Phase-2 target. |
| Calibration | **Do NOT over-calibrate.** The 40% `replay_neg_boost` arm (h12_calib) *lobotomizes* (recall→0.021, denies to pass traps). Long-horizon trap erosion is an open problem handled at the **gate** (traps ≥0.40 blocks publish + triggers a refresher night). `replay_neg_boost` stays a ≤10% tunable, **default 0 / off**. |
| Replay source | **raw is a tie** with amplified → replay re-fetches **prior day-logs** (raw). The amplified reservoir is audit/provenance, not on the replay hot path. |
| Base model | **32B** (BWM=D6; adapter must match the served base). Recipe is base-agnostic; 32B chain is proven recipe-identical. 32B==8B is a *tie* (write-bound) — we pay 32B compute for serve-quality, not memory-quality. |
| Ownership | **Storage owns** day-log materialization + recipe registry + reservoir + model directory. **Continuum owns** amplify + finetune + gate + publish. |
| Code | Clean, lean, ours — **parity harness is the contract**, not code fidelity. |
| Naming | **Morpheus**, versioned. No "engram" in our surface. |
| Speed-specificity | Isolated behind a single **`Profile`** seam (§6). |
| Exec model | Production-close: pinned env / container, absolute interpreter (never `conda activate`), device + `gpu_memory_utilization` configurable. |

## 2. Reproduction baseline + golden references (all on the cluster node)

Phase 1 verdict: **REPRODUCED** — our `repro_replay_f30` seen-mean 0.286 == his seed-0; separation
+0.253 inside his 3-seed spread (+0.178…+0.269); micro 0.160 in his 0.152–0.183; day-5 retention
1.00; corpus rebuild ratio 1.004. Diff every kernel against these:

- **His golden runs:** `~/engram/results/phased/replay_f30_s0` / `_s1` / `_s2` (seed ensemble),
  `~/engram/results/phased/repro_replay_f30` (our Phase-1 run), `~/engram/results/refeval/*.json`.
- **Prebuilt inputs:** `~/engram/data/corpus/day{D}.blocks.jsonl` (30 days), `~/engram/data/narrative/day{D}_x48neg.corpus.txt` (29 days) — the amplifier output to diff against.
- **Root data:** `~/speed_lora/data/descriptions/{1,5,10,20}min/` (5min=9063), `~/speed_lora/data/holdout_manifest.csv`.
- **Reference code:** `~/nmn/cl-research/research/engram/code/` @ `b3c58e1` — READ to understand behavior, do not import.
- **Envs:** conda `speedlora` (train/serve), `vllm23` (judge/litellm/Vertex). **H100 80GB** on the node (8× shared — GPU 0 is busiest, make it configurable). Judge: Gemini-2.5-flash via litellm on Vertex `poetic-avenue-438401-a7` (team access; export `VERTEX_PROJECT`).
- **Day set:** train 5,9,12,13,17,21; heldout control 6,16,28.

## 3. Port manifest — behavior → home → parity test

Reimplement each **behavior** cleanly; prove it with the listed test. Discard all his infra.

| Behavior | Home | Parity test (vs §2 goldens) |
|---|---|---|
| `render_block` (block → anchored text) | **storage day-log** (see 2c: client interface now, storage-side later) | byte-identical block text (Phase-1C showed identical) |
| amplify: STYLES + NEG_STYLE + `valid()` + ok-rate≥0.85 gate | `morpheus/amplify.py` | neg-frac == 0.150, ok-rate ~1.0, corpus-size ratio ~1.0 |
| CPT loop + LoRA cfg (r128/α256, LLM linears) + `chunk_corpus` (1024-tok) | `morpheus/train.py` | exact chunk boundaries, `adapter_config`, target-module set |
| replay sampler (raw source, matched-compute, `neg_boost` knob) | `morpheus/replay.py` | identical paragraph selection given a fixed seed |
| judge (prompt + litellm/Vertex call) | `morpheus/judge.py` | same judged-recall distribution on a fixed pred set |
| scorers (`TRAP_MARKERS`, f1/contains/trap) | `morpheus/scorers.py` | exact scores on a fixed transcript set |
| probes (self-study, ext) + probe≠corpus rule | `morpheus/probes.py` | probe/gold shape matches; no corpus-generator overlap |
| eval driver (per-day/decay matrix) | `morpheus/eval.py` | reproduces the decay matrix within run-to-run variance |
| **DISCARD** | — | all `sbatch/*`, `phased_run.sh`, `submit_chain.sh`, arm-dispatch, hardcoded paths |
| **NOT NOW** | inference | `engram_server/worker/planner/mneme/train_mneme_proto/ttt_probe` (serve-time 4-lane harness) |

## 4. Parity harness (first-class deliverable — it licenses the clean rewrite)

`continuum/tests/parity/` — differential tests, run against the §2 goldens:

- **Deterministic** (assert exact): `render_block` text, `chunk_corpus` boundaries, LoRA target set + `adapter_config`, `sample_replay` selection at a fixed seed, `trap_score`/`TRAP_MARKERS`.
- **Stochastic** (assert distributional): amplify → neg-frac 0.150 ± tol, ok-rate ~1.0, corpus ratio 1.0 ± 0.05; judge → recall distribution on a fixed pred set.
- **End-to-end** (assert in-band, **seed ensemble, not a single run** — his spread is ~0.075 wide): full cycle on days 5/9/12/13/17/21 → seen-mean ~0.28, separation inside +0.178…+0.269, micro 0.152–0.183, heldout ≤0.05, day-5 retention high.

A kernel is "ported" only when its parity test is green. No green, no merge.

## 5. Exec model (production-close)

- Morpheus training/judging runs in a **pinned env invoked by absolute interpreter path or a
  container** — never `conda activate` (his `phased_run.sh` crashed on exactly this: activate
  didn't fix PATH, python lacked peft). Capture `conda env export -n speedlora`/`-n vllm23`
  as the env lockfiles under `continuum/` for reproducibility.
- **Config knobs** (`config.py`): `MORPHEUS_DEVICE` (GPU index — GPU 0 hardcoding is gone),
  `gpu_memory_utilization`, interpreter/container path, model paths from config not
  `/home/ubuntu/engram`.
- Job submission = our scheduler/SLURM wrapper, chained by dependency, **not** background pollers.

## 6. The `Profile` seam (the single de-Speed lever)

All domain-specific bits live in ONE module: `morpheus/profiles/speed.py` (`SpeedProfile`).
It holds: the amplification prompt template, the `valid()` anchor check, the day/date/place
anchor scheme, and the "35-day" bound. Morpheus kernels take a `profile` and hardcode nothing.
Generalizing to real users = add `morpheus/profiles/lifestream.py` and point the recipe at it —
**one new file, nothing else in Morpheus changes.** (Speed profile is the only one needed for
Phases 2a–2c; do not build the lifestream profile yet, just keep the seam clean.)

## 7. Phases + exit criteria

- **2a — Morpheus core + parity.** Reimplement the §3 kernels in `app/morpheus/`, fed by the
  existing day-log blocks (`~/engram/data/corpus/day{D}.blocks.jsonl`). Green the §4 parity
  harness. **Exit:** every kernel's parity test green; E2E seed-ensemble in-band vs the goldens.
- **2b — full nightly cycle + M0.** Wire the real Morpheus backend into `cycle.py`
  (`TRAINER_BACKEND=morpheus` replacing `mock`/`engram`), producing a real 32B life adapter that
  **publishes via C5 and loads in vLLM**. Uses the scaffold's local storage stand-ins for now.
  **Exit:** charter M0 — one Speed day → adapter → loads in vLLM, through our gate + publish.
- **2c — lean architecture + storage seams (client side).** Introduce the storage **client
  interfaces** the lean shape needs — day-log fetch (C10-evolved), recipe-registry fetch,
  reservoir write + replay-read — each with a **local implementation now, HTTP-to-storage later**
  (same posture the scaffold already uses for reservoir/model-dir). Migrate `daylog/window/renderer`
  behind the day-log-fetch client. Finalize the `Profile` seam + exec-model hardening.
  **Exit:** continuum runs the 5-verb loop against the seam interfaces; storage-side
  implementation is a separate storage workstream (this session does NOT block on it).

DP dogfood / product-shape day-log (records → day-log) is **Phase 3**, a later workstream — out
of scope here. Keep DP and real storage OUT of the parity-critical path (2a) so a data-shape
change never confounds a port bug.

## 8. Boundaries + reporting

- This IS product work — write code under `product/services/continuum/`, on a branch, tested,
  cofounder-reviewed. Match our lean house style (crisp, no redundancy, high coverage).
- Do NOT touch the research repo (`~/nmn/cl-research`) — read-only reference.
- Do NOT build the DP/storage server sides or the serve-time harness.
- Report per phase: parity-harness results (kernel diffs + E2E seed-ensemble table), M0
  evidence (adapter loads in vLLM), env lockfiles captured, wall-clock/GPU-h, and any deviation
  from the goldens with a root-cause. Cofounders review before the next phase.

## 9. Divergence log (record every deliberate departure from `b3c58e1` behavior)

| Date | Behavior/file | Departure | Why |
|---|---|---|---|
| — | — | *(none yet)* | |
