# WS-P3 — Phase 3: the DP dogfood (Speed data through the real pipeline)

**Status:** ✅ **COMPLETE — LEARN-LOOP INTEGRATION PROVEN END-TO-END.** 3a bridged real Speed
audio through recording→DP→storage; 3b (1-min rule-bend) collapsed on **dose**, not the pipeline;
the **decomposition run (parity content, `segment_seconds=300 block_segments=1`) reproduced the
baseline separation** — 0.137 vs baseline 0.179, permutation **p=0.148 (same distribution)**,
p=0.018 above the no-consolidation control, p=0.016 above the failed 1-min run. Verdict: **PIPELINE
SOUND**. Reports: [phase-3-report.md](phase-3-report.md), [phase-3-decomp-report.md](phase-3-decomp-report.md).
· design locked (cofounders, 2026-07-24) · **Spans:** recording + DP + storage + continuum ·
**Driven by:** continuum (learn-loop validation) · Prereq: Phase 2 (2a/2b/2c) DONE on main.

> **Bottom line:** our real product services carry the learn loop without losing the model's
> ability to learn. The only residual is a **recipe/dose** property (amplification is fixed at
> 48 retellings *per block*, but recall depends on retellings *per unit of text* — so dose must
> scale with block-text volume at our native cadence) — Gnandeep's knob, NOT an integration defect.
> A minor block-**wrapper** rendering residual (`On <date>… / Scene:` vs the reference's
> `[Day N of 35 · City · …]` header) is a later block-shape question; it does not move the verdict.

> **The one question Phase 3 answers:** when Speed's data flows through our REAL product pipeline
> (recording → DP → storage → continuum), does **seen-vs-heldout separation SURVIVE**? Not "does it
> byte-reproduce 0.28" — the product path differs from the parity path in several coupled ways, so
> the number will differ and that's fine. Separation surviving = the product path works. If it
> collapses, THEN decompose — not before (the Phase-2 seed-0 lesson).

## Why this is cheap (feasibility verified 2026-07-24)

Almost all of it is config; only two small files are net-new. Grounded findings:

- **Rule-bend = pure config.** `segment_seconds` + `block_segments` are recipe knobs that flow into
  `build_daylog` (buckets by the first, groups by the second — `daylog.py:68,134`). A test recipe
  with `segment_seconds=60, block_segments=5` gives 1-min segments / 5-min blocks, **no code change**.
- **Continuum needs ZERO changes to consume real data.** `record_provider` already defaults to the
  real `/context` read (`fetch_window_records` over C10) — 2c wired this. Phase 3's "swap" is the
  existing default (`nightly.py:51`, `clients/__init__.py:43`).
- **Real ASR+diarize is turnkey** — `ASR_BACKEND=faster_whisper` + `DIARIZE_BACKEND=pyannote`, already
  node-7-validated (`asr-fw-v1+diar-pyannote-v1`).
- **An injected description → a C2 caption record is zero-schema-change** — exact precedent is the
  acoustic sidecar (`app/stages/audio/acoustic.py`) emitting `kind="caption"` with a per-unit sub-span.

## Design decisions (locked — build to these)

| # | Decision |
|---|---|
| Test-type | **Config profile + naming convention, NOT a contract field.** C1/C2 are v0-frozen (`additionalProperties:false`) — a `request_type` field is a v0→v1 bump across recording+DP, not worth it. Instead: a dedicated DP env profile + `user_id="replay-speed"` (out-of-band tag; replayed chunks are indistinguishable-by-design, the intended posture). True per-request mode, if ever needed, is the designed-but-unbuilt **C8 interactive profile** (`resolve()` mechanism ready) — do NOT invent a parallel enum. |
| Rule-bend | Test recipe `recipes/consolidation-test-1min-v1.0.json`: `segment_seconds=60, block_segments=5`; `recipe_id` == filename stem == `settings.recipe_id` (registry rule). Forks `recipe_id` — fine for a test profile. Use the **1-min descriptions** as segments → grouped into 5-min blocks. (Expect different block content than parity: 5×1-min vs 1×5-min descriptions — hence "does separation survive," not "match the number.") |
| Captions | Injected via a **new DP sidecar** (§ wiring), NOT the video caption backend (keyframe-coupled/awkward). |
| Arms | **Arm 1 (the measurement): descriptions-only recall.** ASR runs for real (Arm 2) but its transcripts are **excluded from the recall day-log** via a caption-only record-provider filter (zero core change). Arm 3 (descriptions+ASR) is deferred — flip the filter later. |

## End-to-end wiring (service by service)

**Recording — a replay `ChunkSource` (one new file + a registry line).** Per `app/sources/base.py`
"drop in one file". It downloads a Speed **audio** track's bytes (extracted from the 20-min video
blob in GCS) and yields it as chunk(s); driven through the existing `POST /capture/run` (accepts
`modality, source, chunk_seconds, user_id, device_id, base_wallclock` — `models.py:49-64`).
- **Blob leg needs the BYTES** — storage has no by-reference/GCS-URL registration (**recording's**
  OQ8, unbuilt — `../../recording/CHARTER.md:175`; this line said "storage OQ8", which does not
  exist and the mislabel propagated into the storage/C10 launch prompt — corrected by D18), so
  re-PUT through `/raw/blobs`. Fine at test volume; `/capture/run` avoids the 64 MB segment cap.
- Feed **audio** (not video): the blob then serves double duty — real ASR runs on it AND it resolves
  the mandatory `blob_ref` sha-check that DP does before any stage.
- Set `t_start/t_end` from `holdout_manifest.csv` (day/city/time) so chunks are window-addressable.

**DP — env profile + one new sidecar (one file).**
- Env: `ASR_BACKEND=faster_whisper`, `DIARIZE_BACKEND=pyannote`, translate/acoustic off.
- New `app/stages/audio/injected_caption.py`, shaped like `acoustic.py` (`needs=()`, gated by e.g.
  `INJECT_CAPTION_BACKEND`): given the C1 (chunk_id, stream_id, t_start/t_end), look up the matching
  **1-min Gemini description** and emit `ProcessedUnit(content.kind="caption", text=<description>,
  t_start/t_end=<the 1-min window>)`. `build_c2` serializes it verbatim (`pipeline.py:82`) — zero
  schema change, exact acoustic precedent.
- Result per chunk: real `transcript` records (ASR/diarize) **and** injected `caption` records, all → `/context`.

**Storage — no change.** Receives C1/C2 as today; `/context` is queryable by `(user_id, from, to)`.

**Continuum — no code change; two config choices.**
- Point the run at the test recipe (rule-bend). `record_provider` → real `/context` (already default).
- **Arm-1 caption-only filter:** wrap the provider at the injection site —
  `provider = lambda w: [r for r in fetch_window_records(...) if r["content"]["kind"] == "caption"]`.
  Keeps the recall day-log descriptions-only. Flip to no-filter for Arm 3.

## Arms

- **Arm 1 — recall (the answer).** Caption-only day-log, rule-bent, real pipe → Morpheus over the 6
  days → seen/heldout/separation table vs the 5-min parity baseline. Verdict: **does separation
  survive** (clear seen>heldout)? One line.
- **Arm 2 — DP integration validation (bonus).** ASR+diarize ran for real on Speed audio — report the
  stages produced sane transcripts (spot-check a few). NOT part of the recall number.
- **Arm 3 — DEFERRED.** Descriptions+ASR day-log (drop the filter), measured separately — a later
  "does adding transcripts help/hurt" finding, not now.

## Staging

- **3a — the bridge. ✅ DONE (2026-07-24, job 756).** 629 chunks / 209.7 h of real Speed
  audio through recording → DP → storage in 1 h 44 m on 8 H100s; 12,221 caption + 621
  transcript C2 records queryable by (user, window), zero missing, zero segment collisions,
  one dialect (`asr-fw-v1+diar-pyannote-v1`). Arm 2's ASR + diarization ran on every chunk
  and spot-checks sane.
  ORIGINAL SCOPE: replay `ChunkSource` + injected-caption sidecar + test recipe + DP env profile
  → Speed's 6 train days (5,9,12,13,17,21) + heldout (6,16,28) land in `/context` as rule-bent C2 for
  `user_id="replay-speed"`, via **real recording→DP→storage**. Arm 2's real ASR runs here.
  **Exit:** real C2 (caption + transcript) queryable by `(user, window)`; ASR spot-checked sane.
- **3b — the measurement. ✅ DONE (2026-07-24, job 767). VERDICT: separation did NOT
  survive.** 5 seeds: separation 0.077 vs the 5-min baseline's 0.179 (p = 0.0067) and
  statistically indistinguishable from the rehearsal-off control (p = 0.80). The rule-bent
  day-log reproduces the baseline's block count exactly on 5 of 6 train days and the bridge
  is exact, so this is NOT a pipeline defect: acquisition is 3.2x weaker (0.079 vs 0.249 on
  the night a day is written) while retention is fine, tracking a 3.7x cut in amplification
  dose per fact (48 retellings now cover 4.1x the block content). FIRST DECOMPOSITION STEP,
  config only: inject the 5-min descriptions with segment_seconds=300, block_segments=1 —
  same services, same spine, parity block content. Full write-up:
  [phase-3-report.md](phase-3-report.md).
  ORIGINAL SCOPE: continuum fetches (caption-only filter) → Morpheus over the 6 days →
  Arm-1 verdict. **Exit:** the separation-survives table + one-line verdict.

**Deferred (do NOT start):** real VLM keyframe captioning (the true caption-shape test), Arm 3,
C8 per-request profiles, GCS by-reference blobs (OQ8).

## Data facts

- Speed videos: GCS bucket `nucleus-continual-learning` (20-min chunks / 67 videos). Audio extractable.
- Descriptions: `~/speed_lora/data/descriptions/{1,5,10,20}min/` (use **1min**), + GCS mirror.
- `~/speed_lora/data/holdout_manifest.csv` — chunk → day/city/split. Days 6/16/28 heldout.
- WhisperX+pyannote ASR already exists (`.en.vtt`) — we deliberately **re-run our own** (Arm 2 tests
  our stages); don't reuse the precomputed files for the transcript records.

## Boundaries + reporting

- Measure, do NOT tune. No recipe knob-tuning to chase parity, no Morpheus kernel changes.
- The replay source + injected-caption sidecar are the only net-new code; keep them lean and clearly
  test-oriented. No contract changes (that was the whole point of the config-profile decision).
- SLURM for the Morpheus training (6 days). Branch off main. Cofounder review before merge.
- Any cross-service contract friction (e.g. C10 needs kind-filtering, blob-by-reference) → NOTE for
  the storage/C10 session, do not pin.
- **Report:** 3a — records landed (counts, a sample caption + transcript), ASR spot-check. 3b — the
  Arm-1 table vs the 5-min baseline + the one-line verdict (did separation survive). If it didn't, the
  FIRST decomposition step you'd take (cadence / block-shape) — not a full investigation. Job ids, GPU-h.
