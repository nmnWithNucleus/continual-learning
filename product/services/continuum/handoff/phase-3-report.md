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

---

## 2. Stage 3a — the bridge (SLURM job 756, node-5, 8×H100, 1 h 44 m)

**629 chunks · 209.7 h of real Speed audio · 9 days · through recording → DP → storage.**
`phase3_verify.py` asks storage exactly what the nightly asks and checks the answer against
the plan by SET equality. `all_ok=True`:

| day | captions | expected | missing | foreign | transcripts | ASR chars | seg dup | ok |
|---|---|---|---|---|---|---|---|---|
| 5  | 1354 | 1354 | 0 | 0  | 69/69 | 429,631 | 0 | ✅ |
| 6  | 1421 | 1421 | 0 | 0  | 72/72 | 453,248 | 0 | ✅ |
| 9  | 1400 | 1400 | 0 | 0  | 71/71 | 482,177 | 0 | ✅ |
| 12 | 1294 | 1294 | 0 | 60 | 66/66 | 464,012 | 0 | ✅ |
| 13 | 1409 | 1409 | 0 | 0  | 71/71 | 482,604 | 0 | ✅ |
| 16 | 1383 | 1383 | 0 | 0  | 70/70 | 463,902 | 0 | ✅ |
| 17 | 1417 | 1417 | 0 | 0  | 72/72 | 430,403 | 0 | ✅ |
| 21 | 1126 | 1126 | 0 | 0  | 58/58 | 417,768 | 0 | ✅ |
| 28 | 1417 | 1417 | 0 | 0  | 72/72 | 416,456 | 0 | ✅ |

**12,221 caption + 621 transcript C2 records** (13,020 emitted; the difference is the
out-of-window tail), one dialect throughout — `asr-fw-v1+diar-pyannote-v1` — 4.04 M
characters of real transcript, 23 GB of audio blobs in `/raw`, **zero segment collisions**
(every caption alone in its 60 s bucket, which is the property the rule-bend depends on).

*The 60 "foreign" captions on day 12 are the tz-overlap the spine predicts:* day 12 is
Chicago and day 13 is New York, so their 04:00-local windows overlap by an hour and day
12's range read legitimately returns day 13's first three chunks. Arm 1's provider drops
them by construction (it filters on the day's own caption timestamps); this check is what
would notice if that ever stopped.

**43 of 621 transcripts are empty** — chunks of road noise and music with no speech. That
is the VAD gate doing its job, not a failure: an honest empty transcript beats a
hallucinated one.

### Arm 2 — the DP-stage validation (real ASR + real diarization, spot-checked)

`ASR_BACKEND=faster_whisper` (large-v3, float16, VAD on, language pinned `en`) +
`DIARIZE_BACKEND=pyannote` ran on every chunk. Sample transcript, day 21, 10:03–10:23:

> *"Yes, sir, chat. Good morning. Stop playing with me. […] Anna underscore donated $4.99
> through Super Chat. Keep your tour bus clean. If you need a traveling housekeeper for
> your next tour, I'm available. […] Anthony donated $2 through Super Chat. Speed, are you
> faster than Sonic? Hell yeah."*

That is the real stream: superchat readouts, crew banter, the donation ritual. Diarization
attached speaker turns throughout (day 5: 24 distinct chunk-local labels).

Sample caption (day 21, 09:00–09:01), the injected 1-min description as a C2 caption record:

> Headline: A dark grey SUV passes the RV on an Oklahoma highway while IShowSpeed sleeps in
> his bunk. / Scene: Outdoor, highway, Oklahoma/Arkansas border area, sunny daylight. /
> People & outfits: IShowSpeed is visible in the PiP window, sleeping under a white blanket
> […] / On-screen overlays: GPS map showing Oklahoma/Arkansas border, stream timer at
> 479:14:30, subscriber count at 44,160,604, speed indicator at 75 mph […]

**Measured throughput:** 65 s per 20-minute chunk end to end (blob PUT + sha verify + ASR +
diarize + 21 C2 writes), 8 lanes, **8.0 chunks/min sustained**. Wall clock 1 h 44 m;
**≈13.9 GPU-h** (8 cards held for the duration). The long pole was the lane carrying two
days (124 chunks); a per-day work queue would cut ~35 min.

---

## 3. Stage 3b — the measurement (SLURM job 767)

### 3.1 The day-log continuum actually fetched

`phase3_daylog.py` calls the same `day_log_client(settings, recipe, record_provider=…)` the
nightly calls, over the same `fetch_window_records` C10 read, with the provider wrapped
caption-only + train-split:

| day | records fetched | captions | kept (train) | segments | **blocks** | 5-min baseline | chars/block | >6000 cap |
|---|---|---|---|---|---|---|---|---|
| 5  | 1423 | 1354 | 1194 | 1194 | **240** | 240 | 5740 | 87 |
| 9  | 1471 | 1400 | 1225 | 1225 | **245** | 245 | 5752 | 90 |
| 12 | 1423 | 1354 | 1129 | 1129 | **226** | 226 | 5808 | 91 |
| 13 | 1480 | 1409 | 1237 | 1237 | **248** | 248 | 5757 | 106 |
| 17 | 1489 | 1417 | 1222 | 1222 | **246** | 272 | 5679 | 92 |
| 21 | 1184 | 1126 |  980 |  980 | **196** | 196 | 5924 | 99 |
| | | | | | **1401** | 1427 | | 565 |

**The rule-bent day-log reproduces the 5-min baseline's block count EXACTLY on five of six
train days.** Day 17 is the one day that differs (246 vs 272) — its 26.0 h of stream does
not fit a 24 h window, so ~2 h falls outside it. Day 5 is 238 blocks of exactly five 60 s
segments plus two short ones; the rule-bend does what it says.

**But the blocks are not the same blocks.** Same five minutes, same count, ~4× the text:

| | reference (1×5-min description) | product (5×1-min descriptions) |
|---|---|---|
| chars/block | mean 1409, max 2546 | mean **5740**, max 8122 |
| first line | `[Day 5 of 35 · Washington, DC · 5min clip · ~9:22 AM ET]` | `On 2025-09-02, around 04:05–04:10 local time:` |
| body | the eight description fields once | `Scene: ` + the eight fields, five times over |

Two consequences to hold onto when reading the number:
1. **565 of 1401 blocks (40%) exceed `EXCERPT_CHARS = 6000`**, the amplifier's excerpt cap
   (`profiles/speed.py:79`). At 5-min cadence that cap never bound (max 2546); here it
   truncates the tail of two blocks in five. It is a profile constant, not a recipe knob —
   changing it would be tuning, so it was left alone and is reported instead.
2. The day-log renderer labels the caption channel once and concatenates
   (`daylog.py:152,160`), so a block reads `Scene: Headline: … Audio: … Headline: …`. That is
   the real product renderer over five caption records; nothing here was special-cased.

### 3.2 Amplification (48×, recipe knobs untouched)

| day | paragraphs | ok-rate | neg-frac | chars/para (ref) | minutes |
|---|---|---|---|---|---|
| 5  | 11,519 | 1.000 | 0.158 | 1033 (942) | 8.2 |
| 9  | 11,642 | 0.990 | 0.151 | 1025 (927) | 6.9 |
| 12 | 10,347 | 0.954 | 0.153 | 1052 (917) | 6.3 |
| 13 | 11,611 | 0.975 | 0.151 | 1026 (904) | 6.7 |
| 17 | 11,619 | 0.984 | 0.152 | 1024 (937) | 6.6 |
| 21 |  8,833 | 0.939 | 0.149 | 1043 (938) | 5.5 |
| | **65,571** | | | | **40.2 min** |

Against the reference's 68,440 paragraphs: **95.8%**. Calibration fraction lands on the
recipe's 0.15 every night. **The ok-rate gate held everywhere** (0.939 worst vs the 0.85
abort threshold) — the answer to the one thing the anchor-free caption text put at risk:
the day number reaches the prose from the amplify prompt alone. Paragraphs run ~11% longer
than the reference's, which is the 4×-denser source showing through a fixed token cap.

### 3.3 Before the number: are the probe answers even in there?

The probes were written from the **5-min** descriptions; the product path trains on the
**1-min** ones. If a recall drop were just the answers going missing, the whole comparison
would be uninterpretable — so it was checked first, per day, over all 900 day-probes
(fraction of a gold answer's content words present anywhere in that day's block text):

| day | probes | 5-min baseline | product (1-min) | Δ | day chars 5-min → 1-min |
|---|---|---|---|---|---|
| 5  | 150 | 0.940 | **0.970** | +0.030 | 338,568 → 1,377,916 |
| 9  | 150 | 0.952 | **0.980** | +0.028 | 338,525 → 1,409,549 |
| 12 | 150 | 0.932 | **0.955** | +0.023 | 314,812 → 1,312,728 |
| 13 | 150 | 0.934 | **0.976** | +0.043 | 339,904 → 1,427,881 |
| 17 | 150 | 0.957 | **0.961** | +0.004 | 376,288 → 1,397,284 |
| 21 | 150 | 0.949 | **0.968** | +0.018 | 287,627 → 1,161,245 |
| | | **0.944** | **0.968** | +0.024 | **4.1× the text** |

**The product path's day-log contains MORE of what the probes ask about, not less.** So a
weaker number cannot be read as "the facts fell out of the pipeline" — they are more
present than in the corpus the baseline was measured on. What changed is how they are
PRESENTED: the same 48 retellings per block now have to cover 4.1× as much material, and
40% of blocks lose their tail to the 6000-char excerpt cap. That is the mechanism to reach
for first if the number moves, and it is a *cadence* mechanism, which is exactly the
variable Phase 3 set out to change.

---

## 5. Cross-service friction the dogfood surfaced (NOTES for the storage / C10 session — not pinned here)

Every one of these is a real thing the product path wanted and did not have. None was
worked around in a way that hides it.

1. **C10 has no `kind` filter.** Arm 1 is descriptions-only, so continuum fetches every
   record in the window and discards the transcripts client-side — day 13 pulls 1,480
   records to keep 1,237. At fleet scale the transcript half is the larger half by bytes.
   A `kind=caption` (or `kind in [...]`) parameter on the range read would make the
   caption-only day-log a server-side projection instead of a client-side filter.
2. **A range read cannot express "this consolidation window's records".** Consecutive tour
   days in different timezones give overlapping 04:00-local windows (day 12 Chicago / day
   13 New York overlap by an hour), so `(user, from, to)` legitimately returns a
   neighbour's records. Today the caller resolves it. If storage ever owns day-log
   materialization (the lean-architecture plan), the *window* — not a time range — is the
   unit it should be addressed by.
3. **No by-reference blob registration (OQ8).** 23 GB of audio was re-PUT into `/raw` that
   already existed in GCS, purely to satisfy the mandatory `blob_ref` sha-check. The
   bridge's whole I/O cost was that copy. A registration that carries a URI + sha would
   have made this leg free.
4. **The range read has no cursor and no cap.** One day is ~1,480 records / ~57 MB of JSON
   in a single response. Fine at pilot volume, not a shape that survives a real fleet.
5. **There is no way to retract a window.** A replay mints fresh ULID `chunk_id`s, so a
   re-run writes a disjoint record set into the same window and the day-log double-counts
   it (observed on a 2-chunk smoke: 40 captions in 20 segments). The bridge now wipes the
   store per run; a real service needs delete-by-(user, window) or an idempotency key that
   survives re-delivery.
6. **Dev sqlite on NFS does not take concurrent writers.** Measured before the run: 8-way
   concurrent C2 writes on the NFS home take the lock ~2% of the time (HTTP 500) and run
   27× slower than on local disk. The bridge puts `STORAGE_DB_PATH`/`STORAGE_RAW_DIR` on
   the node SSD and copies the DB back. Neither knob exists in `platform/deploy/learn.env`.

## 6. What was deliberately NOT done (spec boundaries, held)

No contract change (C1/C2 untouched — the caption record needed none). No recipe or kernel
tuning: `EXCERPT_CHARS` binds on 40% of blocks and was left alone; the day-log renderer's
caption concatenation was left alone. No real VLM keyframe captioning. No Arm 3
(descriptions+ASR) — the transcripts are on disk and the filter is one line, so it stays a
later flip. No C8 per-request profiles. No GCS by-reference blobs. No serve-time harness.
One core-file edit in total: the single `SOURCE_BUILDERS` registry line.
