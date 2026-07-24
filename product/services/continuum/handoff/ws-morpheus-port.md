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
- **ALL GPU work goes through SLURM — mandatory, no hand-placed jobs.** `sbatch` with explicit
  `--gres=gpu:N` (32B needs `:2`), `--job-name`, `--output`, `--time`; nights chained with
  `--dependency=afterok:`, never background pollers. Rationale, learned the hard way on
  2026-07-23: the co-tenant's job 698 *was* a SLURM job and therefore visible in `squeue`, while
  our work was hand-placed and invisible — unscheduled work racing scheduled work. Manual
  placement also produced four of that night's five tooling defects (a `flock` that only bound
  lanes started after it, a free-memory probe blind to a sibling lane's startup, two lanes landing
  on one GPU twice, and `pkill -f` matching its own shell). **SLURM's allocator replaces all of
  that tooling — delete it, don't fix it.** Use `scancel <jobid>`, never `pkill`.
- **SLURM interaction gotchas:** (a) SLURM sets `CUDA_VISIBLE_DEVICES` for the allocation, so jobs
  must use the *allocated* device (relative index 0 within the job), **not** an absolute node index
  — `MORPHEUS_DEVICE` must not override a SLURM allocation or the job writes to the wrong card;
  (b) SLURM hides GPUs without `--gpus-per-node=N`, even on the exclusive partition;
  (c) `sbatch --export=NAME=a,b` **splits on commas** and silently truncates a list to its first
  element (this cost the research chain a 12-day run that became 1-day) — pass dot-separated or
  `--export=ALL`; (d) never deploy over an sbatch file a running job is executing from NFS.
- Job names must be readable to co-tenants in `squeue` (`morpheus-chain-s3`, `morpheus-32b-m0`) —
  the node is shared and our usage should be legible to whoever looks.

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
  → **kernels + harness landed** on `svc/continuum-morpheus-2a`; every kernel parity test green;
  E2E seed ensemble measured. **✅ 2a COMPLETE (cofounders, 2026-07-24)** — the rehearsal sampler
  is now proven **byte-identical on 5 nights × 14 seeds**, closing the last unverified kernel
  surface; the ensemble is statistically indistinguishable from the reference (exact permutation
  test, p = 0.514); the eval path is independently validated (the *reference's* adapter scores
  0.45 through our eval code — its exact golden value). Write-ups:
  [phase-2a-report.md](phase-2a-report.md), [overnight-diagnosis-report.md](overnight-diagnosis-report.md).
  **Residual open item (tracked, NOT a blocker):** seed 0 under-performs — localised to
  *accumulation across nights*, specifically **retention, not acquisition** (it wrote day 5 at
  0.25 then decayed to 0.05 while the reference held 0.23 → 0.23). Sampler and single-night
  trainer both cleared, so this is a variance question — see §10.
  *Golden-path corrections found on the node:* the seed-0 reference run is
  `results/phased/replay_f30` (no `_s0` suffix), and the ref-eval set is
  `results/phased/_refeval/`, not `results/refeval/`. "Separation" in §2 is
  **seen-mean − final heldout** (0.2694 / 0.1778 / 0.2028 across the three seeds), which is what
  reproduces the quoted +0.178…+0.269 spread.
- **2b — full nightly cycle + M0.** Wire the real Morpheus backend into `cycle.py`
  (`TRAINER_BACKEND=morpheus` replacing `mock`/`engram`), producing a real 32B life adapter that
  **publishes via C5 and loads in vLLM**. Uses the scaffold's local storage stand-ins for now.
  **Exit:** charter M0 — one Speed day → adapter → loads in vLLM, through our gate + publish.
  **PREREQUISITE (measured 2026-07-23): 32B training requires ≥2 GPUs.** A 32B forward OOMs on a
  single H100 at *any* batch size (79.15/79.18 GiB at bsz 2, 79.16 at bsz 1 — it fails at the first
  forward, so no step/corpus change helps). `--shard 2` was never optional; `MORPHEUS_SHARD_MAX_MEMORY`
  now controls per-card budget. Already proven: the **full mechanic end-to-end on 8B** (train →
  report-only gate → C5 `entries.jsonl`+`active.json` → **vLLM load OK**, answering from Day 5), and
  **32B base + an r128/α256 LoRA of our recipe shape loads and serves** (the earlier failure was
  KV-cache budgeting at util 0.90/len 4096, not LoRA incompatibility). Still to establish: a 32B
  adapter *we* trained end to end. **2b's bar is M0 mechanics + in-band sanity, NOT strict parity** —
  there is no 32B golden at this probe set to diff against.
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

## 8b. Gate policy — RATIFIED (cofounders, 2026-07-24)

All three publish-gate checks were mis-calibrated: each blocked ~everything, **including the
validated recipe's own output**. They were written from the design doc's numbers and never tested
against a measured distribution. Adopt [gate-threshold-proposal.md](gate-threshold-proposal.md):

| check | was | now | why |
|---|---|---|---|
| traps | ≥0.40 | **≥0.15 interim; ≥0.25 once the suite reaches ~150 probes** | 0.40 blocks **71% of the reference recipe's own nights**. Decisive: reference night-to-night sd (0.090) **equals binomial noise at n=28 (0.090) to three decimals — the metric currently measures nothing but sampling.** 0.15 gives 0.9% false-block, 98.8% collapse detection |
| heldout | ≤0.05 on 60 probes | **all 222 probes + one-sided exact test vs each run's OWN base control (α=0.01 → blocks above 5/222), 0.15 absolute backstop** | No systematic leak exists (base 0/60 on all seven runs; seed 0 also 0/60). But n=60 has 12% power against 2% contamination while sitting two probes from a false block. The suite already holds 222 probes and the harness was using 60 |
| min_probes | ≥150 | **148** (or grow the suites) | The harness supplies exactly 148 (60+60+28) — the floor was **unpassable by construction** |

**Shipped 2026-07-24** as `app/policy.py` + `policies/gate-policy-v1.1.json`, split from the
training recipe (`recipe_id` unchanged, so no training cache was invalidated). Re-scored: the
reference now passes **4/4** (was 71% of its nights blocked); the **lobotomy control is blocked**.

Two follow-ups the re-scoring surfaced:
- **The lobotomy passes the traps check at 0.393** — it *denies its way* to a mid-reference
  calibration score — and is caught **only by the recall floor**. Design principle to keep:
  **calibration checks cannot be the primary safety net; a lobotomized model scores well on them.**
  Never let the gate degrade to traps-only.
- **The heldout policy is not yet fully exercised:** it specifies all **222** probes, but the
  re-scoring ran on **60** (seed 2's p=0.029 pass is a 60-probe result). Generating predictions for
  the full 222-probe heldout suite is outstanding before the policy is truly in force.
- Honest limit: at n=28 traps, **0.15 is the smallest floor with any teeth**, and it still blocks
  one of 24 reference nights (its own minimum, 4/28 = 0.143). The floor gets meaningful only when
  the trap suite grows to ~150.

**Structural fix, required with the above.** Gate thresholds currently live inside
`recipes/consolidation-v1.0.json`, and `cycle.py` hashes `recipe_id` into the **amplify and train**
stage keys. So editing a *publish-policy* threshold forks `recipe_id` → invalidates the amplify and
train caches → re-runs hours of GPU work, and falsely implies the trained artifact changed.
**Split them:** the training recipe (parity-critical, frozen) and the **gate policy** (tunable,
separately versioned) become two artifacts, both storage-hosted per the lean architecture. Only the
training recipe may enter a stage key. Ratifying these thresholds must not fork `recipe_id`.

## 10. Open item — seed 0 (tracked; does not block 2b)

Cleared: the rehearsal sampler (byte-identical, 5 nights × 14 seeds) and the single-night trainer
(the reference's 0.45 is the **70th percentile of our own 8 draws**, mean 0.3708 sd 0.074 — our
single-night training is the same distribution). Seed 0's values sit *below everything a single
night produces*, so the deficit **accumulates across the chain**.

Framing that survives: seed 0 **acquired day 5 fine (0.25) and then failed to retain it (0.05)**,
while the reference held 0.23 → 0.23. It is a *forgetting* failure.

**Two cofounder hypotheses were tested and BOTH FALSIFIED (2026-07-24) — recorded so nobody
re-derives them:**

1. *"Seed 0's draws under-sampled day 5."* **Impossible — the rehearsal draw is not seed-dependent
   at all.** The rehearsal RNG is a separate `Random` instance on a constant seed (the reference
   hardcodes `random.Random(7)`, `phase_d_driver.py:313`); `transformers.set_seed()` touches the
   *global* `random` and cannot reach an independent instance. `--seed` fixes **LoRA init only**.
   Verified empirically: all three of our chains trained on **byte-identical rehearsal text**, and
   seeds 1/2 saw exactly what seed 0 saw and landed in-band. Draw variance is excluded.
2. *"Per-night draw noise (sd 0.074) ÷ √6 ≈ 0.030 ≈ the reference's chain sd 0.033, so their
   spread is explained by draw noise."* **A coincidence, comparing different variance sources.**
   The draw sweep varied the *rehearsal seed*; real chains hold it fixed at 7. So 0.074 measures
   rehearsal-draw sensitivity, which real chains do not exercise. We have **no calibrated model**
   for how much chain variance LoRA-init + GPU non-determinism should produce.

**What that leaves:** the only differences between chains — ours or the reference's — are **LoRA-A
initialization and non-deterministic GPU reductions**. So either the recipe has an init-sensitive
failure mode, or seed 0 is a tail draw. Only more chains settle it (ref n→8, ours n→10, in flight).

**The metric to watch is the GATE PASS RATE, not just seen-mean.** Under the ratified policy the
reference passes 4/4 and we pass 1/3 (s0 blocked on recall, s1 on traps). A nightly loop that ships
one night in three is a different product from one that ships every night — this is the
production-relevant readout of the chain wave.

## 10b. Recipe finding — uniform pooling has no per-day floor (quantified 2026-07-24)

Not the cause of seed 0 (the reference dilutes identically and retains fine), but a real property
of recipe v1.0, measured: day 5's share of each night's rehearsal falls **100% → 51% → 35% → 25%
→ 20%**, a **6× absolute drop** (3467 → 565 paragraphs) over five nights.

**Why this matters beyond the 6-day testbed:** our product consolidates nightly *forever*. Under
uniform pooling a given day's share decays as ~1/N, so after a year any single day receives
essentially zero rehearsal — and this service's inherited law is that forgetting is *access decay*.
That plausibly links to the trap-erosion-at-horizon seen at 12 nights. Validated horizons are 6
nights (fine) and 12 (erosion); nobody has measured 30+.
**Candidates if long-horizon retention degrades:** the reference's abandoned `--replay-floor`
(per-day dose floor), spaced-repetition scheduling (decaying but non-zero per-day dose), or
periodic re-consolidation. **Take to Gnandeep as a finding** — it is upstream research, not a port
gap, and v1.0 stays the parity target regardless.

## 9. Divergence log (record every deliberate departure from `b3c58e1` behavior)

Every entry below is deliberate and none of them moves a number the parity harness checks.
"Not ported" means the behavior is absent by decision, not by oversight.

| Date | Behavior/file | Departure | Why |
|---|---|---|---|
| 2026-07-23 | `phase_d_driver` arms `smart` / `dream` / `smartdream` / `olora` / `agem` / `joint` | **not ported** | None is recipe v1.0. `smart` (forgetting-weighted replay) ties uniform at 3 seeds and DESIGN_PROD keeps it behind a flag; dream / olora / merge are measured losers; agem and joint are mechanism probes. Parity is against the `replay` arm only. Reviving one is a research question, not a port gap. |
| 2026-07-23 | `corpus_forget_score` | **not ported** | Only feeds `smart` / `smartdream`. |
| 2026-07-23 | `--replay-floor` (per-day dose floor) | **not ported** | An h12 horizon experiment. Recipe v1.0 uses a flat `replay_frac`; the goldens ran `replay_floor=0`. |
| 2026-07-23 | `NEG_MARKER` (was in `sample_replay`) | moved onto the **Profile** as `is_calibration()`; the sampler takes the predicate | Same regex, same 300-char scan window, identical behavior — but the matcher is the inverse of the profile's `NEG_STYLE`, so a non-Speed profile must be able to bring its own. Found by the seam test that reads kernel source for domain leaks. |
| 2026-07-23 | Adapter continuity across nights | research holds ONE process across the 6 days; production reloads the adapter from disk each night (`PeftModel.from_pretrained(..., is_trainable=True)`) | A nightly service is process-per-night. Numerically equivalent: the optimizer is rebuilt per day in the reference too, and the bf16 safetensors round-trip is lossless. The parity chain runs all 6 nights in one process, exactly as the reference did, so the E2E comparison is unaffected. |
| 2026-07-23 | ok-rate gate | raises `AmplifyBelowOkRate` instead of `sys.exit(2)` | Same threshold (0.85) and same semantics (abort the night, keep serving the prior adapter, log the window as debt). A service cannot exit the process. |
| 2026-07-23 | Step loop bounds | uses `range(0, len(chunks) - bsz + 1, bsz)` (the `phase_d_driver` form), not `train_cpt.py`'s `range(0, len(chunks) - bsz, bsz)` | The driver is the production path and the two differ by one batch at the tail. Confirmed by parity: the golden step counts (4203, 4272, 3879, 4206, 4782, 3423) only reproduce with the driver's form. |
| 2026-07-23 | Eval-harness sizing (`probes_per_day` 60, `traps_n` 50, heldout 60) | CLI flags → constants in `morpheus/eval.py` | Identical values. They size the eval, not the artifact, so they are not recipe knobs and must not be reachable from a recipe. |
| 2026-07-23 | `sbatch/*`, `phased_run.sh`, `submit_chain.sh`, arm dispatch, hardcoded `/home/ubuntu/engram` paths | **discarded** | §3 DISCARD. Replaced by `scripts/morpheus_chain.py` + `PinnedEnv` (absolute interpreter, import preflight). |
| 2026-07-23 | Parity E2E base model | **Qwen3-VL-8B**, not the production 32B | The goldens are 8B runs, so that is where the numbers to match exist. 32B ≈ 8B on identical probes is a measured tie (write-bound, not capacity-bound). The 32B adapter is 2b's deliverable, for serve-quality, not memory-quality. |
| 2026-07-23 | Parity E2E seeds 1 and 2 | ran with gradient checkpointing; seed 0 without | Numerically identical (recomputes the same forward ops) and required to fit three chains on a shared node. Verified: identical `loss_first` (2.007) on both paths for the same corpus. |
| 2026-07-23 | `recipes/consolidation-v1.0.json` `source` field | re-pinned `9711f4a` → `b3c58e1`, provenance-only wording | Commit re-pin per §0. **No knob changed**, so `recipe_id` stands and artifacts trained under it stay comparable. |
| 2026-07-23 | `TrainerBackend.train()` | gained `new_day_corpus_path` | Matched compute needs the new day's chunk count, which cannot be recovered from the mixed corpus. Closes the WS1 known gap ("the budget cap ports with the trainer"). |
