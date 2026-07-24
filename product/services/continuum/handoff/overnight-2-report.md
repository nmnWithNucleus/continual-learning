# Overnight run 2 — composition, gate recalibration, 32B M0, ensemble depth

**Window:** 2026-07-24, unattended · **Branch:** `svc/continuum-morpheus-2a` · all GPU work via
SLURM (jobs 733 wave-a, 734 wave-d) · **Nothing applied to the live gate or merged without
sign-off.**

---

## 1. Rehearsal composition — the seed-0 hypothesis is FALSIFIED

§10 hypothesised that seed 0's draws under-sampled day-5 paragraphs. They cannot have: **the
rehearsal draw is seed-INDEPENDENT.** `set_seed(chain_seed)` fixes LoRA init; the rehearsal
sampler draws from a *separate* `random.Random(7)` (ours) / hardcoded `random.Random(7)`
(reference, `phase_d_driver.py:313`) that the global seed cannot reach. Verified: our composition
== reference composition, and all three original chains used `rehearsal_seed=7`, so seeds 0/1/2
trained on **byte-identical rehearsal text**. Draw variance is excluded as the cause.

Per-source-day dose (identical across every chain, ours and reference):

| night | day5 | day9 | day12 | day13 | day17 | day5 share |
|---|---|---|---|---|---|---|
| s1_d9 | 3467 | — | — | — | — | 100% |
| s2_d12 | 1631 | 1554 | — | — | — | 51% |
| s3_d13 | 1209 | 1153 | 1107 | — | — | 35% |
| s4_d17 | 989 | 1015 | 958 | 1034 | — | 25% |
| s5_d21 | 565 | 567 | 513 | 561 | 646 | 20% |

Uniform pooling gives no per-day floor — day-5's absolute dose falls 6× across the chain — which
is the concern behind the abandoned `--replay-floor`, now quantified. But the reference dilutes
identically and retains day 5 fine, so this is not seed 0's cause either.

## 2. Gate recalibration — RATIFIED policy applied and re-scored

Structural split shipped (ratified §8b): gate thresholds moved from the training recipe to
`policies/gate-policy-v1.1.json`. `recipe_id` is hashed into the amplify/train stage keys, so a
threshold living in the recipe would make re-deciding what is shippable re-run a night of GPU
work; the policy never enters a stage key. Heldout is now a **one-sided exact test against each
run's own base control** (α=0.01, all 222 probes, 0.15 backstop), not a fixed ceiling — so
`EvalScores` carries counts, not just rates. 171 tests green.

Re-scoring every run (`scripts/gate_rescore.py`, offline):

| | recall | traps | heldout | verdict |
|---|---|---|---|---|
| reference (4 runs) | 0.211–0.286 | 0.286–0.393 | ≤2/60 | **4/4 PASS** |
| ours s0 | 0.117 | 0.179 | 0/60 | BLOCK (recall) |
| ours s1 | 0.250 | 0.107 | 1/60 | BLOCK (traps) |
| ours s2 | 0.283 | 0.429 | 5/60 | PASS |
| **CONTROL h12_calib** | **0.021** | **0.393** | 1/60 | **BLOCK** |

The control is load-bearing and instructive: the 40%-neg-boost lobotomy **passes the traps check
at 0.393** — it denies its way to a clean calibration score — and is caught only by the recall
floor. A calibration-only gate ships it. The previous 0.40 traps floor blocked 71% of the
reference recipe's own nights; the ratified 0.15 blocks exactly one of 24 reference nights (its
own minimum, 4/28), which is the least a floor with any teeth can do at n=28.

## 3. 32B end-to-end M0 — 2b's last unestablished deliverable, PROVEN

An adapter WE trained, at the production size, through the whole mechanic:

| step | result |
|---|---|
| train (sharded 2-GPU, 32B does not fit one card) | 4203 steps, loss 1.718 → 0.263, **1.97h** |
| gate (report-only) → C5 publish | `m0-32b-1a460233b0c7`, active alias written |
| **vLLM load** | **OK** — *"On Day 5 of his 35-day US tour in Washington, DC… IShowSpeed walks along the Lincoln Memorial Reflecting Pool…"* |
| judged new-day recall | **0.2667** (8B M0: 0.2167; reference night-0: 0.183–0.25) |

Bar was M0 mechanics + in-band sanity, not strict parity (no 32B golden at this probe set). Met.

## 4. Decay-matrix reproduction — eval path proven everywhere, not one cell

Our eval over the reference's own 21 adapter snapshots reproduces its published decay matrix:
**20/21 cells exact, RMS |diff| 0.004**, matrix means agree to 0.0008. The single 0.017 cell is
one probe of 60 (temp-0 Gemini is near- but not fully deterministic). The eval-path validation is
now complete rather than a single spot-check.

## 5. Seq-arm control — the harness reproduces a FAILURE

Every parity result so far shows agreement with GOOD runs. The seq arm (rehearsal OFF, its own
`recipe_id` `consolidation-seq-v1.0`) reproduces the reference's catastrophic-forgetting FAILURE:

```
seq_s0:  day5 0.00  day9 0.017  day12 0.00  day13 0.00  day17 0.05   day21 0.50   (mean 0.094)
seq_s1:  day5 0.00  day9 0.033  day12 0.00  day13 0.033 day17 0.133  day21 0.517  (mean 0.119)
```

Both seeds collapse except the just-written day; seen-means 0.094 / 0.119, matching the reference
seq's ~0.13. Day-5 → 0.00 in both matches the
reference seq signature exactly. This is what shows the harness detects regressions, not merely
agrees with good runs — and the gate blocks it (recall 0.094 < 0.15).

## 6. Ensemble at the expanded n — the seed-0 question, RESOLVED

Both sides sampled deeper (ours 3→10, reference 4→8):

| | n | seen-means | mean | sd |
|---|---|---|---|---|
| reference | 8 | 0.042, 0.197, 0.197, 0.211, 0.217, 0.236, 0.286, 0.286 | 0.2090 | **0.0716** |
| ours | 10 | 0.097, 0.117, 0.161, 0.200, 0.242, 0.250, 0.258, 0.278, 0.283, 0.283 | 0.2170 | **0.0661** |

Exact permutation test on seen-mean: **p = 0.82** — emphatically the same distribution.

**Seed 0 is ordinary chain variance, now proven rather than argued.** The measurement that
settles it: the reference's own low tail, unmeasured at n=4, contains a **0.042 chain — lower
than any of our ten.** The "2× variance" reported after run 1 was an artifact of the reference
being sampled at n=4 where its four seeds happened to be middling. At matched depth the
reference's spread is **wider** than ours (0.072 vs 0.066), its mean is **below** ours (0.209 vs
0.217), and the two ensembles are statistically indistinguishable. This is exactly what lane B
(the 4 extra reference chains) was for, and it is the cleanest outcome available: our low chains
are not a defect, they are the same tail the reference has.

## 7. SLURM job ids (auditable)

| job | contents | node |
|---|---|---|
| 733 morpheus-wave-a | 32B M0 · ref s4–s7 · our s3,s4 | node 5 |
| 734 morpheus-wave-d | seq s0,s1 · decay matrix · our s5–s9 | node 7 |

Both are single exclusive-node allocations using all 8 GPUs — the partition is
`OverSubscribe=EXCLUSIVE`, so 7 one-GPU jobs would have serialised 5 behind 2 with GPUs idle.

## 8. Decisions for the cofounders

1. **Apply gate-policy-v1.1 to the live gate?** Re-scored and controlled (§2). Not applied.
2. ~~Our ensemble variance is ~2× the reference's~~ — **RESOLVED (§6).** At matched sampling
   depth the reference's spread is slightly wider than ours (its low tail was unmeasured at n=4);
   the ensembles are indistinguishable (p=0.82). Seed 0 was ordinary chain variance. No action.
