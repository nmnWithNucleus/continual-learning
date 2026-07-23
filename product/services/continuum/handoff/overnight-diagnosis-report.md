# Overnight diagnosis — rehearsal proof, draw sweep, gate recalibration, 32B M0

**Run window:** 2026-07-23 ~09:00–19:00 UTC, unattended · **Branch:** `svc/continuum-morpheus-2a`
**Nothing here changes product behaviour.** No gate threshold was altered, no recipe knob was
forked, nothing real was published. The 32B run is a dry run on Speed data serving no one, with
the gate in report-only mode.

---

## 1. Rehearsal-text diff — the sampler is proven, byte for byte

**This closes the last unverified kernel surface.** The chunk-count check (18/18 integers) pins
the mixed corpus's total token volume but not *which* paragraphs are rehearsed — a one-paragraph
difference is ~250 tokens and rarely crosses a 1024-token boundary. That gap was the prime
suspect for seed 0.

There was no dump of the reference's rehearsal text, but there did not need to be: the reference
sampler is deterministic given (seed, corpora) and both are on disk. Extracted verbatim from
`b3c58e1` into a bare namespace — a **test oracle**, not an import into product code, with no
torch/peft/speed_lora side effects — and run over the same chain sequence:

| night | ref paragraphs | our paragraphs | ref chars | our chars | identical |
|---|---|---|---|---|---|
| s1_d9 | 3468 | 3468 | 3,271,764 | 3,271,764 | **yes** |
| s2_d12 | 3185 | 3185 | 2,996,849 | 2,996,849 | **yes** |
| s3_d13 | 3469 | 3469 | 3,243,398 | 3,243,398 | **yes** |
| s4_d17 | 3996 | 3996 | 3,684,831 | 3,684,831 | **yes** |
| s5_d21 | 2852 | 2852 | 2,659,581 | 2,659,581 | **yes** |

Byte-identical on every night, and on **14 independent rehearsal seeds** — so this is the same
*function*, not a coincidence at one stream. **Seed 0 is not a rehearsal bug.**

Frozen as `tests/parity/test_rehearsal_text.py` with hashed goldens (the text is ~15 MB/chain).

**Deviation from the work order, with the proof in hand:** the sweep was specified as ~8 draws
with our sampler and ~8 with the reference's. Since the two are provably the same function on 14
streams, that split would train identical text twice. All 16 slots were repurposed as draws from
one distribution, doubling the sample at no cost.

## 2. Fixed-start draw sweep — separating one night from six

Same pinned start (`repro_replay_f30/adapter_s4_d17`), same night 5, **only the rehearsal seed
varies**. Anchors: the reference reached **0.45** on day 21 from this exact state; our full seed-0
chain reached **0.15**.

| rehearsal seed | day 21 (just written) | day 5 (retention canary) | loss_last |
|---|---|---|---|
| 103 | 0.2167 | 0.0833 | 0.272 |
| 106 | 0.3333 | 0.1000 | 0.254 |
| 107 | 0.3333 | 0.1667 | 0.275 |
| 105 | 0.3667 | 0.1500 | 0.251 |
| 104 | 0.4000 | 0.1667 | 0.272 |
| 109 | 0.4000 | 0.0833 | 0.271 |
| 108 | 0.4500 | 0.2500 | 0.287 |
| 102 | 0.4667 | 0.1833 | 0.270 |

| | n | mean | sd | range |
|---|---|---|---|---|
| day 21 | 8 | **0.3708** | 0.0740 | 0.2167–0.4667 |
| day 21 (+2 earlier same-config runs) | 10 | 0.3733 | 0.0727 | 0.2167–0.4667 |
| day 5 | 8 | 0.1479 | 0.0537 | 0.0833–0.2500 |

**Verdict.** The reference's 0.45 sits at the **70th percentile of our own single-night draws** —
an ordinary result from our distribution, not a level we fail to reach. Seed 0's chain values are
**below everything a single night produces**: day 21 at 0.15 against our minimum of 0.2167,
day 5 at 0.05 against our minimum of 0.0833.

**Seed 0's deficit is therefore ACCUMULATED across the six nights, not made in any one of them.**
Together with §1 (the rehearsal text is byte-identical) this rules out the sampler and the
single-night trainer, and localises the remaining question to what compounds over a chain —
which is exactly what P1/P3 would have measured, and what capacity did not allow.

## 3. Gate recalibration — proposal only

Full analysis in [gate-threshold-proposal.md](gate-threshold-proposal.md). Headlines:

- **Traps:** the 0.40 floor blocks **71% of the reference recipe's own nights**. Worse, the
  reference's night-to-night sd (0.090) equals the binomial noise at n=28 (0.090) to three
  decimals — **the metric currently measures nothing but sampling**. Proposed 0.15 interim
  (0.9% false-block, 98.8% collapse detection), 0.25 once the suite reaches ~150 probes.
- **Heldout:** base scores 0/60 on all seven runs and our seed 0 scored 0/60, so **no systematic
  leak exists**. But n=60 has 12% power against 2% contamination while sitting two probes from a
  false block. The suite already contains **222 probes and the harness uses 60**. Proposed: use
  all 222, and replace the fixed ceiling with a one-sided exact test against each run's *own*
  base control (α=0.01 → blocks above 5/222, 99% power at 5% contamination), with a 0.15
  absolute backstop for a contaminated base.

## 4. 32B M0 dry run

M0 asks a mechanical question: does an adapter we trained publish through C5 and load in the
server that will serve it. It is now answered — in two halves, because a hard limit forced the
split.

**32B cannot train on a single H100, at any batch size.** Measured, not assumed: bsz 2 OOM'd at
79.15/79.18 GiB, bsz 1 at 79.16/79.18. The failure is at the FIRST forward pass, so no step count
or corpus size changes it. The reference chain's `--shard 2` was not optional. `LifeAdapter`
already supports sharding but hardcoded 76 GiB per card; that is now
`MORPHEUS_SHARD_MAX_MEMORY`, because on a shared node we do not own the whole card.

**Half A — the full mechanic, with an adapter we trained (8B):**

| step | result |
|---|---|
| one night, recipe settings | 4203 steps, loss 1.814 → 0.285, 0.62 h |
| judged new-day recall | **0.2167** (reference night-0: 0.183–0.25) |
| judged heldout | **0.0000** |
| gate | ran **report-only**, verdict recorded, publish NOT blocked |
| C5 publish | `entries.jsonl` + `active.json` written |
| **vLLM load** | **OK** — answered *"On Day 5 of his 35-day US tour in Washington, DC at approximately 11:17 PM ET, IShowSpeed sleeps soundly shirtless on a grey tufted couch inside his RV…"* |

**Half B — 32B serving:** 32B base + an r128/α256 LoRA of exactly our recipe shape **loads and
serves** (util 0.97, max_model_len 2048), answering from the consolidated day. An earlier attempt
at util 0.90 / len 4096 failed for want of 0.7 GiB of KV cache — a serving-config question, not a
LoRA-compatibility one, and worth distinguishing.

**What this does NOT establish:** a 32B adapter *we* trained end to end. That needs a second GPU.

**Gate findings from the dry run**, feeding §3: the `min_probes` floor of 150 fails against the
148 probes the harness actually supplies (60 + 60 + 28) — an off-by-two mis-sizing, not a quality
signal. And a fresh single-night adapter scored **0.036 on traps** (1/28), matching our seed-0
chain's night 0 exactly; calibration appears to accrue over nights rather than arriving with the
first. The reference's night-0 traps were 0.250–0.393, so this is worth a look, but at n=28 the
noise is ±0.18.

## 5. Capacity — what did not run, and why

**P1 (4 more reference chains) and P3 (5 more of ours) did not run.** Not an oversight; there was
no room for them without crowding the co-tenant.

Five sweep jobs OOM'd early on. Cause was a footgun in our own code: `draw_sweep.py` built a
`LifeAdapter` directly and never passed `grad_checkpointing`, so `MORPHEUS_GRAD_CKPT=1` in the
launcher did nothing and jobs trained uncheckpointed at **~52 GB instead of ~38 GB**. Meanwhile
SLURM job 698 (`lane_council32b`, the research serve-time stack) had grown to ~40 GB on every
shared card. 52 GB does not fit in the ~42 GB that leaves.

Fixed, and guarded by a test asserting every script that opens a `LifeAdapter` decides
checkpointing explicitly rather than inheriting a default it cannot see. All work was then
consolidated onto **GPU 7, which has no co-tenant**, running strictly sequentially — one job per
card, waiting on real free memory so it queues *behind* the co-tenant instead of racing it.

That is one GPU for the night: ~8 draws (~4 h) plus the 32B M0 run (~2 h). The chains are 5 h
each and would have required the shared cards. **Open question for the cofounders:** take GPUs 2
and 4 (42 GB free against a 52 GB job — about 4 GB of headroom) to fit P1, or continue leaving
the co-tenant alone. Judged against "leave the co-tenant headroom", this session chose the latter.

## 6. Standing conclusions, unchanged by tonight

- Kernel parity green; `render_block` 1427/1427 byte-identical; LoRA target set 252/252.
- The eval path is independently validated: the **reference's** adapter scores **0.45 through our
  eval code — its exact golden value**.
- E2E ensemble (n=3 vs n=4) is statistically indistinguishable from the reference: exact
  permutation test on run-level seen-mean, **p = 0.514**.
- The min–max "in band" criterion was miscalibrated and has been replaced; by leave-one-out the
  reference satisfies its own band only 2 times in 4.

## 7. Harness defects found in this session's own tooling

Recorded because the results below were carried by tooling less reliable than they are. None
corrupted a measurement — every draw and the M0 run are verified by their own output files — but
the monitoring was blind for stretches, and that is worth knowing before trusting the next
unattended run.

| defect | consequence | fix |
|---|---|---|
| `echo "EXIT $? $job"` — `$(date)` expanded first, resetting `$?` | **every job logged EXIT 0, including one that failed with 127** | capture `rc=$?` before any substitution |
| `rm -f jobs/draw11*.sh` matched a script written seconds earlier | `draw110` silently never ran | explicit missing-script check, logged |
| free-memory probe cannot see a sibling lane's job during startup | two lanes put jobs on one GPU, twice | per-GPU `flock` held for the whole job, plus an in-job idle-wait |
| `flock` only binds lanes started after it | a pre-`flock` lane collided with a locked one; that 32B result was discarded | re-ran on a verified-idle card |
| `pkill -f <pattern>` matched the shell running it | killed my own session twice | match by PID |

## 8. What to decide

1. **Ratify or amend the gate thresholds** (§3). Nothing is applied. Note `min_probes=150` also
   needs to become 148 or the suites need to grow — the current value cannot pass.
2. **Capacity policy.** P1/P3 need either the shared cards (~4 GB headroom above the co-tenant —
   which is what caused tonight's OOMs) or a scheduled window when job 698 is not resident. This
   session chose to leave the co-tenant alone.
3. **2b prerequisite:** 32B training needs ≥2 GPUs. Worth confirming the node can reserve two
   before 2b plans a 32B nightly.
