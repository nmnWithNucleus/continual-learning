# Gate thresholds — measured distributions and a proposal

**Status:** PROPOSAL. Nothing here is applied. `recipes/consolidation-v1.0.json` is unchanged
and the gate in `cycle.py` still blocks on the current thresholds. Cofounders ratify before any
of this becomes the gate; changing a recipe knob also forks `recipe_id`.

**Evidence base:** 7 complete 6-night chains — 4 reference (`replay_f30`, `_s1`, `_s2`,
`repro_replay_f30`) and 3 ours (`morpheus_f30_s0/1/2`). 42 night-level trap measurements,
7 final heldout measurements, each against its own base-model control.

---

## 1. Traps floor — the current value blocks the recipe it was written for

Measured, offline marker score, 28 probes per night:

| | mean | sd | min | max |
|---|---|---|---|---|
| reference nights (n=24) | 0.365 | 0.090 | 0.143 | 0.500 |
| all nights (n=42) | 0.333 | 0.119 | 0.036 | 0.536 |

**The current floor is 0.40. Only 7 of 24 reference nights clear it** — the validated recipe's
own nights would be blocked **71% of the time**. A gate that rejects the thing it was
calibrated on is not measuring quality.

It is worse than a bad constant. The reference's night-to-night sd is **0.090**, and the
binomial sd of a 28-probe sample at p=0.365 is **0.090** — identical to three decimals. **All
observed night-to-night variation is sampling noise.** At n=28 a single night's trap rate
carries ±0.18 at 95% confidence, so the metric cannot currently distinguish a good night from a
bad one at all.

Operating characteristics, against a healthy night (p=0.365) and a collapsed one (p=0.05, the
h12 horizon-erosion endpoint):

| floor | probes | blocks a healthy night | detects a collapse |
|---|---|---|---|
| **0.40 (current)** | 28 | **69.7%** | 100% |
| 0.30 | 28 | 25.3% | 100% |
| 0.25 | 28 | 6.8% | 100% |
| 0.20 | 28 | 2.8% | 99.8% |
| **0.15 (proposed interim)** | 28 | **0.9%** | **98.8%** |
| **0.25 (proposed target)** | **150** | **0.1%** | **100%** |

**Proposal.** Interim: **floor 0.15 at the current n=28** — ~1% false blocks, ~99% detection of
a genuine calibration collapse. Target: grow the trap suite to **≥150 probes** (WS4 — it is the
only suite that needs new generation) and then set the floor at **0.25**, which is both
essentially false-block-free and certain to catch collapse.

Worth noting for whoever reviews: our seed 0's first night scored **0.036** on traps — a real
calibration collapse that a 0.15 floor catches and that no reference night approaches. The
lowest-recall chain we produced is also the one the recalibrated gate would have blocked, which
is mild evidence the recalibrated floor keeps its teeth.

## 2. Heldout ceiling — n=60 cannot answer the question being asked

Final-adapter heldout recall, each against that run's own base-model control:

| run | adapter | base |
|---|---|---|
| replay_f30 | 1/60 (0.0167) | 0/60 |
| replay_f30_s1 | 2/60 (0.0333) | 0/60 |
| replay_f30_s2 | 2/60 (0.0333) | 0/60 |
| repro_replay_f30 | 2/60 (0.0333) | 0/60 |
| ours s0 | **0/60 (0.0000)** | 0/60 |
| ours s1 | 1/60 (0.0167) | 0/60 |
| ours s2 | **5/60 (0.0833)** | 0/60 |

**No systematic leak exists.** The base model scores 0/60 on all seven runs, and our seed 0
scored 0/60 — if the pipeline leaked heldout days into training, it could not produce a zero.

The ceiling is 0.05, which at n=60 is exactly 3/60; a run fails at 4/60. The reference's own
worst run sits at 2/60, i.e. **two probes from tripping a gate that blocks publication.**

Power at n=60 (one-sided, α=0.01, against a 0.5% base rate):

| true contamination | n=60 | n=222 |
|---|---|---|
| 2% | **12%** | 46% |
| 5% | 58% | **99%** |

At n=60 the check misses 5%-level contamination nearly half the time while sitting two probes
from a false block. It is simultaneously too twitchy and too weak.

Seed 2's 5/60 is significant against its own base by a one-sided Fisher test (**p = 0.029**).
But across 7 runs at α=0.05 we expect ~0.35 false positives by chance, so one is unremarkable —
and that ambiguity is exactly what a 60-probe suite cannot resolve.

**Proposal.**
1. **Use all 222 heldout probes** the suite already contains (the harness caps at 60 —
   `HELDOUT_LIMIT` in `morpheus/eval.py`). This is free: no generation, no new data.
2. **Replace the fixed ceiling with a one-sided exact test against the run's OWN base-model
   heldout at matched n**, α=0.01. At n=222 that blocks above 5/222 (0.023) with 99% power
   against 5% contamination — stricter where it matters and far less trigger-happy.
   Testing against the run's own base control (rather than a constant) also survives a change
   of base model, which a fixed ceiling does not.
3. **Keep an absolute backstop** at 0.15 for the case where the base model is itself
   contaminated and the differential test would see nothing.

## 3. What is NOT proposed

- The **new-day recall floor** (0.15) is untouched. Every run here clears it and there is no
  evidence to re-derive it from.
- The three **unwired checks** (decay spot-check, general-ability canary, read-skill canary)
  stay unwired and stay visibly listed as skipped in every report.
- Nothing about **when** the gate blocks. Report-only mode exists solely for the M0 dry run,
  which serves no one; the gate stays blocking in `cycle.py` for anything real.

## 4. If ratified

`recipes/consolidation-v1.0.json` → **v1.1** (any knob change forks `recipe_id`, so artifacts
trained under v1.0 stay comparable):

```
gate.traps_pass_min      0.40  -> 0.15      (interim, n=28; -> 0.25 when the suite reaches 150)
gate.heldout_recall_max  0.05  -> replaced by heldout_test: {vs: base_control, alpha: 0.01,
                                                             backstop: 0.15}
eval.heldout_probes       60   -> 222       (harness sizing, not a recipe knob)
eval.trap_probes          28   -> 150       (needs WS4 generation)
```
