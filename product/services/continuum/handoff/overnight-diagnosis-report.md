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

*(table filled at completion — see `var/diag/sweep_report.json`)*

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

*(filled at completion — `var/diag/m0/m0_report.json` + `vllm_load.json`)*

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
