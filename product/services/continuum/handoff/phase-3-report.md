# Phase 3 — the DP dogfood: Speed's data through the REAL pipeline

**Status:** 3a landed · 3b measured · **Branch:** `svc/continuum-phase3-dogfood` · Spec:
[ws-phase3-dogfood.md](ws-phase3-dogfood.md) · Cofounder review before merge.

> **The one question:** with Speed's data flowing through recording → data-processing →
> storage → continuum, does **seen-vs-heldout separation SURVIVE**?
>
> **VERDICT: _(3b)_**

---

## 0. What was built (two net-new service files; everything else is config)

| file | what it is |
|---|---|
| `recording/app/sources/replay_source.py` (+1 registry line) | replays a recorded day through the UNCHANGED blob-first + C1-push emit path |
| `data-processing/app/stages/audio/injected_caption.py` | sidecar: one `kind="caption"` C2 per 1-min description window in the chunk's span |
| `continuum/recipes/consolidation-test-1min-v1.0.json` | the rule-bend: `segment_seconds=60`, `block_segments=5`. Every training knob byte-identical to v1.0 |
| `continuum/scripts/phase3_build_replay.py` | derives the wall-clock spine ONCE into a replay plan + caption index |
| `continuum/scripts/phase3_verify.py` | the 3a exit check against `/context` |
| `continuum/scripts/phase3_daylog.py` | day-log through the real 2c client, caption-only + train-split provider |
| `continuum/scripts/phase3_amplify.py` | 48× amplification with the day/city anchors the product day-log cannot carry |
| `continuum/scripts/phase3_report.py` | the Arm-1 table vs the 5-min baseline and the negative control |
| `continuum/slurm/phase3-{bridge,arm1}.sbatch` | the two stages, one exclusive node each |

**No contract change. No kernel or recipe-knob tuning. One core edit: the single registry
line.** Tests: recording 132 green (12 new), data-processing 173 green (10 new).

---

## 1. Decisions the build had to make (and the evidence for each)

### 1.1 The registry key — a replay source without touching the frozen C1 enum

`SOURCE_BUILDERS` is keyed by a SOURCE key, not by the C1 modality: `build_source()` uses
it for lookup only, and the C1 envelope's `modality` comes from `source_obj.modality`
(`capturer.py:158`). So `"replay" → ReplayPlanSource(modality="audio")` emits an envelope
byte-shape-identical to a live audio capture, with `wav_source` untouched.

**Deviation from the spec, deliberate:** the spec drives this through `POST /capture/run`,
but `CaptureRunRequest.modality` is a `Literal` mirroring the frozen C1 enum
(`models.py:14,58`), so a variant key 422s over HTTP. The replay is driven through
`python -m app.cli --modality replay`, which the module docstring already names as an
entry point and which runs *the same* `capturer.run_session`. Widening the HTTP request
model to reach a test-only source would have weakened the production surface for nothing.

### 1.2 The time spine — real clock is impossible, so the day starts at its boundary

A tour day is 18.8–26.0 h of stream; the consolidation window is 24 h.

* With the REAL clock there is **no boundary that fits even the eight days under 24 h**:
  day 13 needs a boundary ≥ 08:59 local, day 28 needs ≤ 08:49 — disjoint. Day 17 is
  26.03 h and fits no 24 h window at all. (`rwt` is also non-monotonic and partly blank.)
* So each day is laid **contiguously from its own window start** on a **whole-minute
  grid**: chunk *i* starts at `window_start + Σ ceil(duration/60)·60` over the chunks
  before it, while the chunk keeps its REAL duration as its C1 span.

Quantising the STRIDE (never the span) is what makes the rule-bend exact — verified on the
built fixture: **all 12,391 captions land on a whole-minute offset, each inside exactly one
chunk span, with zero intra-day and zero cross-day span overlaps.** The cost is 170
captions (1.37%) whose minute falls past the 24 h window: day 17 loses 146 (≈2.4 h, the
structural overflow), days 6 and 13 lose 6 and 18 to stride rounding, and days 6/16/28 are
heldout so their losses never reach training.

Dates are the real tour dates (day 1 = 2025-08-29, matching the manifest's own YYYYMMDD on
eight of nine days; day 17 spans two and takes the later). Timezone is the day's city.

### 1.3 Content scope — the baseline trained on TRAIN-split chunks only

Checked, not assumed: `~/engram/data/corpus/day{D}.blocks.jsonl` contains blocks from
exactly the day's `split=train` chunks and no heldout ones (day 5: 240 blocks / 61 distinct
parents / 61 train chunks / 0 heldout). Including the within-day heldout chunks would have
changed the CONTENT SCOPE as well as the cadence, and the comparison would no longer
isolate one variable.

So: **everything lands in `/context`** (that is what a faithful capture does), and the
scope is reproduced at the READ, in the same provider wrapper that keeps the day-log
caption-only. With that filter the rule-bent day-log reproduces the parity block count on
five of six train days.

### 1.4 The caption text — the description's fields, without the tour anchor

The injected caption carries `SpeedProfile.render_block`'s output **minus its first line**
— the tour anchor (`[Day 5 of 35 · Washington, DC · 5min clip · ~9:17 AM ET]`) is dataset
metadata no captioner could know, and the day-log writes its own time anchor from the
record's `t_start`. Keeping the eight FIELDS identical to the 5-min parity path is what
leaves cadence as the single changed variable.

The risk this created — `SpeedProfile.is_valid` requires the day number back in the
generated prose, and with the anchor gone it can only come from the amplify prompt — was
**measured before committing GPU hours: ok-rate 1.000 (384/384)** on product-path blocks,
against the recipe's 0.85 abort threshold. No anchor needed.

### 1.5 The sidecar joins on WALL CLOCK, because it must

A replayed chunk's `chunk_id`/`stream_id` are fresh ULIDs — by design, since the whole
point is that a replayed chunk is indistinguishable from a live one — so there is no id to
join a description to, and inventing a contract field to carry one is exactly what the
design forbade. Both services instead read the same spine: recording stamps each chunk from
the replay plan, the sidecar bisects the caption index built from that same plan, and
membership is half-open on `t_start`, the same attribution rule the day-log uses.

Like the acoustic sidecar it declares **no `version_fragment`**: it only ADDS records, and
forking the whole chunk's dialect (which would re-key the ASR primary too) on a sidecar
toggle is what that precedent declines to do.
