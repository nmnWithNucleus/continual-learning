# Phase 3 — decomposition: can the REAL pipeline carry PARITY content to PARITY numbers?

**Status:** RUN + ANSWERED · **Branch:** `svc/continuum-phase3-decomp` (off main; Phase-3 result merged)
· Follows [phase-3-report.md](phase-3-report.md). Cofounder review before merge.

> **The question:** Phase 3 showed the 1-min product path did NOT preserve separation
> (0.077 vs a 0.179 baseline; indistinguishable from a rehearsal-off control). The cause
> was understood — dose dilution + block-text mangling. This run holds everything constant
> except those two, feeding PARITY block content through the SAME services, to ask whether
> the pipeline itself can reach parity numbers.
>
> **VERDICT: PIPELINE SOUND.** Separation returned to the baseline band and is statistically
> indistinguishable from it (permutation p = 0.148), while being significantly above both
> the rehearsal-off control (p = 0.018) and the 1-min product path (p = 0.016). The
> learn-loop integration is proven end-to-end; the remaining gap to the baseline center is
> a recipe/dose question, not a pipeline defect.

## What changed from Phase 3 (three config changes, no code)

1. Replay index built from the **5-min** descriptions with **`--with-anchor`**, so a caption
   record carries the parity block text verbatim, tour anchor and all
   (`[Day 21 of 35 · Oklahoma City + Kansas City, OK + KS · 5min clip · ~10:19 AM CT]`).
   Phase 3 dropped that line; the probes are day-indexed and the recipe's rule is
   anchors-in-prose, so this was the largest residual.
2. New recipe `consolidation-test-5min-v1.0`: `segment_seconds=300, block_segments=1` — ONE
   description per block, no concatenation. Byte-identical to `consolidation-v1.0` on every
   training knob (48×, neg_frac 0.15, LoRA r128/α256, 3 epochs, replay amp 0.30).
3. Same spine, same bridge, same `user_id`, same DP profile, same probes, same seeds.

## The setup reproduced parity, not an approximation (all measured pre-chain)

| stage | 5-min decomp | reference | Phase-3 1-min |
|---|---|---|---|
| blocks (6 train days) | 1,400 | 1,427 | 1,401 |
| chars / block | **1,447** | ~1,410 | 5,740 |
| blocks over 6000-char cap | **0** | 0 | 565 (40%) |
| amplified paragraphs | 67,155 (0.981×) | 68,440 | 65,571 (0.958×) |
| ok-rate | **0.996–1.000** | ~1.0 | 0.939–1.000 |
| chars / paragraph | 921–956 | 904–942 | 1,024–1,052 |
| retelling per source fact | **~32×** | ~32× | ~8.6× |

Block counts match the reference exactly on 5 of 6 train days (day 17 is 246 vs 272, the
same 24 h-window truncation carried from Phase 3). 3a was exact: all 9 days `all_ok=True`,
every expected caption present, one per 300 s segment, zero collisions.

## THE ARM-1 TABLE (job 773, 5 seeds, Qwen3-VL-8B, 4 h 08 m)

| run | seen | **separation** | heldout | base |
|---|---|---|---|---|
| arm1_s0 | 0.1278 | 0.1111 | 0.0167 | 0.0111 |
| arm1_s1 | 0.1667 | 0.1500 | 0.0167 | 0.0111 |
| arm1_s2 | 0.1861 | 0.1194 | 0.0667 | 0.0111 |
| arm1_s3 | 0.1611 | 0.1444 | 0.0167 | 0.0111 |
| arm1_s4 | 0.1944 | 0.1611 | 0.0333 | 0.0111 |

| set | n | separation mean | sd | range |
|---|---|---|---|---|
| **5-min decomp (this run)** | 5 | **0.1372** | 0.0211 | 0.1111 – 0.1611 |
| 5-min parity baseline | 10 | 0.1786 | 0.0584 | 0.0805 – 0.2333 |
| Phase-3 1-min product path | 5 | 0.0772 | 0.0383 | 0.0333 – 0.1167 |
| rehearsal-off control | 3 | 0.0648 | 0.0397 | 0.0306 – 0.1083 |

Exact permutation tests on run-level separation:
* **vs the 5-min baseline: p = 0.148** — NOT significant → same distribution (the
  pre-registered PIPELINE-SOUND condition).
* **vs the rehearsal-off control: p = 0.018** — significant → clearly above "no
  consolidation".
* **vs the Phase-3 1-min run: p = 0.016** — significant → the change recovered the signal.

Every seed's separation (0.111–0.161) sits inside the baseline's spread, and the decomp
distribution is **tighter** than the baseline's (sd 0.021 vs 0.058). Heldout stayed clean
(0.030) and the base floor is unmoved (0.0111): real signal, no leak.

## One honest residual (named, not chased — per the pre-registered rule)

The decomp mean (0.137) sits a little below the baseline center (0.179), though well inside
its spread and statistically indistinguishable from it. The decay matrix says where that
gap lives, and it is NOT where Phase 3's was:

| | acquisition (night written) | seen (chain end) |
|---|---|---|
| 5-min decomp | 0.121 | 0.167 (recall **rises** after the write — rehearsal building) |
| 5-min baseline | 0.249 | 0.217 (recall **falls** — normal forgetting) |
| Phase-3 1-min | 0.079 | 0.101 |

Acquisition roughly doubled from the 1-min run (0.079 → 0.121) but is still below the
baseline's 0.249 — the difference is that the decomp chain *retains and compounds* what it
writes (end > acquisition) where the baseline forgets (end < acquisition), so the two
converge at the endpoint. The most likely remaining residual is the day-log's block
**wrapper** — each block is prefixed `On <date>, around HH:MM–HH:MM local time:` and a
`Scene:` label instead of the reference's `[Day N of 35 · City · 5min · ~time]` header, so
the amplifier sees a slightly different anchor than the probes were written against. That is
the next thing to test **if** anyone wants to close the last ~0.04 — a block-shape question.
It does not change this run's verdict: the pipeline carries parity content to
statistically-parity separation.

## Verdict (pre-registered, unmoved)

**PIPELINE SOUND — learn-loop integration proven end-to-end.** Real recording → DP → storage
→ continuum, fed parity block content, reproduces baseline separation (p = 0.148, not
significant) and is decisively separated from both the no-consolidation control and the
1-min path that failed. The Phase-3 collapse is therefore confirmed as a **recipe/dose**
property (48× amplification over a block whose content the rule-bend quadrupled), not a
defect in the product pipeline. Any further recall gain is a recipe change for the
consolidation line, out of this workstream's scope.

## Cost + provenance

| job | stage | elapsed | GPU-h (held) |
|---|---|---|---|
| **772** | bridge — 5-min captions, 9 days | 2:00:14 | 16.0 |
| **773** | Arm 1 — day-log, amplify, 5 chains, judge | 4:08:15 | 33.1 |
| | | **6:08** | **49.1** |

Chains 3.70–3.74 h each, `Qwen/Qwen3-VL-8B-Instruct`, `consolidation-test-5min-v1.0`, replay
amp 0.30, days 5,9,12,13,17,21, rehearsal seed 7, seeds 0–4. Judge
`vertex_ai/gemini-2.5-flash`, 0 GPU. Artifacts under `~/phase3b/{plan,daylog,corpus,reports}`,
`~/phase3b/context.db`; chains in `continuum/var/phase3/arm1_s{0..4}` (Phase-3's 1-min chains
preserved as `var/phase3/p3_1min_s{0..4}`).
