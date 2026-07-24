# ws-video-clip.md — the mac-app screen-recording VIDEO path

**Status:** design ratified in-session; build not started.
**Owner:** data-processing.
**Supersedes:** `handoff/ws-video-pipeline.md` §3 (the D8 caption/OCR weave) and the keyframe-per-record shape.
**Scope:** the `modality=video` stage graph, `app/vision/**`, `app/stages/video/**`, one new co-located OCR sidecar service. Audio is untouched. Browser and camera scenarios are named but not built.

---

## Decision addendum — OCR↔caption coupling RATIFIED (2026-07-24)

A second design pass (17-agent fan-out: 4 grounds · 4 advocates A/B/C/D · 3 priority-split judges ·
5 adversarial lenses · decision) settled the founder's question — *"why not a separate OCR record,
woven only at consolidation?"* The separate `kind='ocr'` record already exists (D-08); the live
question was whether the captioner **sees** the OCR text as input (Architecture **A**, injection,
D-09) or is produced blind with fusion deferred to consolidation (**B** juxtapose / **C** fuse-at-
consolidation / **D** minimal-hint).

**VERDICT: keep A (injection), exactly as the stage graph is written — `clipcap.needs =
("clipprep","screentext")`, injection per D-09.** The stage graph does **not** change. Reasons,
each verified against source:
- **"Fuse at consolidation" asks the one model there to do what it is forbidden to do.** The day-log
  renderer is dumb string concatenation (`daylog.py:152-164`); the only model in the nightly loop is
  the amplifier, whose prompt says *"keep every … name and on-screen/world text verbatim. Do not
  invent"* (`speed.py:63-64`) — it must **not** manufacture the string→action binding, and it
  restates whatever it is given ~48×/block. So *"at 13:04 the user wrote to Sarah about the Q3 deck"*
  is **never written as text** under defer-fusion; injection binds it once, as prose, where the
  pixels + PTS + region roles + OCR confidences live. *(This corrects my own earlier finding: the
  amplifier is a model, but it is a model instructed NOT to fuse — so it cannot stand in for
  injection.)*
- **Failure isolation — the intuitive win — is illegal, three ways.** It needs `screentext` to be
  `best_effort`, which the executor's best-effort cone forbids (`executor.py:207-219`), R3(b)
  forbids (fragment + best_effort), and which would silently rewrite the OCR record under a stable
  `record_id`. B-required / C / D **all** dead-letter 100 % of video chunks on an OCR outage,
  identical to A. Decoupling buys **zero** on failure.
- **Version isolation is illusory.** `record_id` hashes one flat `pipeline_version` per chunk
  (`pipeline.py:33-46`); a decoupled OCR stage either keeps its fragment (caption re-keys anyway) or
  drops it (silent overwrite). Same fork under A and B alike.
- **B pays all of A's costs and delivers none of its benefit**, so the fallback if A fails its gate
  is **D**, not B. C is unbuilt, unscheduled, blocked on M4, and breaks `build_daylog` purity.

**Accepted costs of A (now in §8 caveats, not "fixed"):** +0.36 s @10 s / +1.8 s @60 s serialised
OCR latency; a jointly-sourced pair the amplifier can reinforce 48× (bounded by new R2 Corollary 2);
an OCR knob honestly re-keys the day's captions (OCR is a real caption input).

**The one thing the design got wrong: it argued the grounding premise instead of measuring it.**
So A's cutover is **gated on O-8** (below): a $15 / ~40 s blind-vs-injected A/B, pre-registered rule
— ship A iff `named_entity_recall(A) − named_entity_recall(B) > 0.25` **and**
`propagation_rate < 0.10` on a 30 %-corrupted-OCR arm; else ship **D**.

**Six ratified edits (this addendum IS the record; fold into the numbered sections at integration):**
1. **§9 O-8** — the blind-vs-injected gate above. WS-H adds one pack file `screen-clip-blind-v1`
   (+ `screen-clip-hint-v1` for the D fallback) to `prompt_ab.py`; arms fork `pipeline_version` by
   pack digest so they cannot collide.
2. **D-09 / §8 counter** — widen `dp_caption_ungrounded_quote_total` from double-quoted spans to all
   named ≥4-char strings (measured 32.6 % of OCR-derived strings enter the caption unquoted and
   escape the current check). One regex in the WS-H scorer. This is what makes A's headline safety
   property actually hold.
3. **§4.3 R2 Corollary 2** — added inline above.
4. **§4.3 R4 `stage outcome`** — added inline above.
5. **§8 finding #3** — reclassify "OCR fork is correct" from *Fixed* to *Accepted caveat* (it is a
   re-classification, not a fix; the re-key cost is real — A-15).
6. **§11 WS-E2** — **promoted from tail item to a prerequisite of the clip cutover.** Verified hole:
   `register_stage` binds `enabled()`↔`version_fragment()` only for `mutate` (`stage.py:221-230`); a
   **sidecar** with non-empty `provides` — which `screentext` is, under A — has two independent
   resolvers, re-opening the diarize silent-overwrite class for the *caption*. A is the only
   architecture whose R1 correctness depends on WS-E2's registration-time raise, so WS-E2 ships
   **before** `VIDEO_PIPELINE=clip` is flipped on.

**Consequence for the fan-out:** WS-C's `screentext` and WS-D's `clipcap` wiring is **confirmed = the
current design (A)** — no pending decision remains; build them as written. **No new cross-service
escalation** is created (choosing A actively *declines* the fusion ask C/B would have filed on
continuum). WS-H gains the O-8 arm + the widened scorer. Everything else in §11 is unchanged.

---

## 1. Today's flow — the honest current state

### 1.1 The graph

```
POST /ingest → C1 schema gate (main.py:324-331) → pipeline_version (main.py:358)
  → process_chunk (ingest_core.py:84-193)
      keyframes (sidecar, order 0, provides keyframes+vision_settings, NO version_fragment)
        → captions (primary, order 10, fragment = select_captioner().PIPELINE_VERSION)
```

`keyframes` (`app/stages/video/keyframes.py:24-49`) runs `extract_keyframes` (`app/vision/frames.py:222`): **6 ffmpeg-family subprocesses per chunk** — 1 `ffprobe -show_entries format=duration`, 1 scene-detection pass (`fps=2.0,select='gt(scene,0.30)',metadata=print`), and N single-frame extracts whose `-ss` sits **after** `-i` (`frames.py:149`), i.e. output seek, i.e. a full decode from t=0 for every frame.

`captions` (`app/stages/video/captions.py:94-97`) fans N **independent** single-image VLM calls through `asyncio.gather(run_in_threadpool(...))`, then `assemble()` emits one `kind='caption'` record per keyframe with a sub-span (`captions.py:46-66,117-135`), OCR woven into the caption string by `_weave_ocr` (`captions.py:37-43`), plus optional `kind='ocr'` records behind `VIDEO_OCR_RECORDS=1` (`captions.py:136-145`).

### 1.2 The knobs (`app/vision/config.py:80-95`, read fresh per chunk)

| knob | default | effect |
|---|---|---|
| `VIDEO_BACKEND` | `mock` | selects captioner module → `PIPELINE_VERSION` (`vidproc-mock-v0` \| `vidproc-vlm-v0`) |
| `VIDEO_SCENE_THRESHOLD` | `0.30` | `select='gt(scene,0.30)'` |
| `VIDEO_KEYFRAME_INTERVAL_S` | `3.0` | sets `n = ceil(dur/3.0)` — **not** the spacing |
| `VIDEO_MAX_KEYFRAMES` / `_MIN_` | `8` / `1` | cap / floor |
| `VIDEO_SAMPLE_FPS` | `2.0` | scene-pass sampling |
| `VIDEO_FRAME_MAX_WIDTH` | `768` | `scale='min(iw,768)':-2` |
| `VIDEO_VLM_URL` / `_MODEL` / `_API_KEY` / `_TIMEOUT` | `127.0.0.1:8000` / `Qwen/Qwen3-VL-32B-Instruct` / `""` / `120` | wire |
| `VIDEO_VLM_MAX_TOKENS` | `256` | **one budget for caption AND verbatim OCR** |
| `VIDEO_OCR_RECORDS` | `0` | extra `kind='ocr'` unit per keyframe |

**None of these appear in `pipeline_version`.** `captions.version_fragment` (`captions.py:78-80`) returns `select_captioner(vs).PIPELINE_VERSION`, a bare constant (`vlm.py:36`).

### 1.3 The prompt, verbatim (`app/vision/vlm.py:38-47`)

```
_SYSTEM = (
    "You are a precise visual describer for a personal life-logging pipeline. "
    "Describe what is happening in the frame factually and concisely. Then "
    "transcribe any legible on-screen text exactly as written."
)
_USER = (
    "Describe this video keyframe. Respond in exactly two lines and nothing else:\n"
    "Caption: <one or two factual sentences describing the scene>\n"
    "On-screen text: <every legible on-screen/UI text, verbatim; or 'none'>"
)
```

Message shape (`vlm.py:93-108`): `[{system}, {user: [one image_url, one text]}]`. No neighbour frame. No prior caption. No timestamp. `temperature: 0`, `max_tokens: 256`.

### 1.4 Per-10s-chunk arithmetic (measured)

| quantity | value |
|---|---|
| keyframes selected | **4** (`_uniform_times`: `n=ceil(10/3)=4` → `[0.0, 2.5, 5.0, 7.5]`; scene pass contributes 0 on real screen content) |
| VLM calls | **4** |
| prefill tokens | 4 × (360 vision + 132 text) = **1,968** |
| output tokens | 4 × ~110 = **440** |
| C2 records | **4** (8 with `VIDEO_OCR_RECORDS=1`) |
| ffmpeg subprocesses | **6** |
| video decoded | **24.8 s per 10 s of wall clock (2.5× realtime)** |
| ffmpeg wall time | **1.22–1.30 s** |

Per screen-hour: 1,440 calls, 708K prefill, 158K output, 1,440 records, 2,160 subprocesses.
Per 8 h day: 11,520 calls, 5.67 M prefill, 11,520 records, 17,280 subprocesses, 9.55 MB stored.

### 1.5 What is specifically wrong for SCREEN content

1. **Per-frame calls cannot describe change.** The prompt says *"Describe what is happening in the frame."* A full application switch at t=5 s produces two unrelated static descriptions and **no record anywhere states that a switch occurred.** This is a property of the call shape, not a tuning parameter.
2. **Massive redundancy.** Measured SSIM between consecutive selected keyframes: near-static email + caret **0.99983 / 0.99986 / 0.99999**; app-switch clip 0.99997 / 0.27299 / 0.99986. 60–75 % of the 1,440 calls/hour describe pixels a prior call already described. There is no similarity check anywhere in the codebase.
3. **Scene detection is inert on screens.** Measured **0 cuts** on full-screen scrolling code whose SSIM fell to 0.47; **0 cuts** on typing (scores `0.000011`, `0.000029`). It fires once, at score `1.000000`, on a whole-screen light→dark swap — landing 67 ms from a grid point and minting a **67 ms record** whose source frames SSIM at 0.999951.
4. **768 px destroys the text OCR exists to read.** Chain: display 3024 → capture `min(1728,iw)` → DP `min(iw,768)` = **×0.254 net**. A 13 pt UI em arrives at **6.6 px**; a monospace cell at **3.98 px**.
5. **One token budget for two jobs.** A truncated reply silently degrades to "no on-screen text" via `vlm.py:75-76` — no error, no metric.
6. **The prompt is invisible to record identity.** Editing `_SYSTEM` changes every caption while `pipeline_version` stays `vidproc-vlm-v0`, so the reprocess **upserts over `/context`** at `storage/app/db.py:302`. This is exactly the failure class `app/audio/diarize/__init__.py:4-10` was built to make impossible.
7. **Identity is decoder-dependent.** `_probe_duration` measured **9.933 vs 9.867 s** across nominally identical segments, rescaling every grid point; `record_id` folds the keyframe *index*, so a heterogeneous fleet upserts the same id with a different span and a different caption.
8. **The training target is a wall.** 48 caption strings space-joined into one `Scene:` line (`daylog.py:160`) = 9,600–19,200 chars per 2-min block, of which **38–69 % is truncated by `EXCERPT_CHARS=6000`** before the amplifier reads it. Dose ≈ 5.2×, against 32× for the validated baseline and 8.6× for the Phase-3 arm that failed.

---

## 2. Target flow

**One clip call. One CPU OCR pass at native resolution, injected into the clip prompt as input and emitted as one record. Exactly two C2 records per chunk, always.**

```
POST /ingest → … → run_graph (video)

  clipprep    (sidecar, required, order  0)  2 ffmpeg passes, 1 decode each
     │                                        provides: clip_frames, delta, vision_settings
     ▼
  screentext  (sidecar, required, order 10)  CPU OCR over changed frames @ native res
     │                                        provides: ocr_text  ·  emits 1 × kind='ocr'
     ▼
  clipcap     (primary,  required, order 20)  ONE multi-image VLM call, OCR injected
                                              emits 1 × kind='caption'
```

Legacy graph (`keyframes` order 0 → `captions` order 10) is retained, gated off, byte-identical.

### 2.1 Stage declarations

| | `clipprep` | `screentext` | `clipcap` |
|---|---|---|---|
| `name` | `clipprep` | `screentext` | `clipcap` |
| `modality` | `video` | `video` | `video` |
| `kind` | `sidecar` | `sidecar` | **`primary`** |
| `policy` | `required` | `required` | `required` |
| `needs` | `()` | `("clipprep",)` | `("clipprep","screentext")` |
| `provides` | `("clip_frames","delta","vision_settings")` | `("ocr_text",)` | `("clip",)` |
| `mutable_slots` | — | — | `("enrichments",)` |
| `order` | `0` | `10` | `20` |
| `version_fragment` | `"+cp-v1"` | `ocr.version_tag(vs)` → `"+ocr-ppv6-cpu-v1"` | `backend.PIPELINE_VERSION + backend.prompt_tag(vs) + cfg_tag(vs)` |
| emits units | no | **1** (`kind='ocr'`, `discriminator="ocr"`) | **1** (`kind='caption'`, `discriminator=""`) |
| run mode | `run_sync` (subprocess + bytes) | `run_sync` (blocking HTTP to loopback) | `run_async` (one loop-native call) |

**Legacy, unchanged except for a 4-line gate:**

| | `keyframes` | `captions` |
|---|---|---|
| `enabled(settings)` | `resolve_pipeline(vs) == "keyframe"` | same |
| `version_fragment` | `""` — **frozen legacy exemption**, see D-13 | `select_captioner(vs).PIPELINE_VERSION` (unchanged) |

### 2.2 Resolution check (traced against `executor.py:88-238`)

Enabled = `{clipprep, screentext, clipcap}`. Exactly one primary ✓ (`:114-119`); base fragment non-empty ✓ (`:121-126`); needs form a DAG, no cycle ✓ (`:183-200`); no mutates; `provides` disjoint and disjoint from `mutable_slots` seed ✓ (`:165-181`); no `best_effort` so the cone rule is vacuous ✓ (`:202-219`); orders 0/10/20 unique per modality ✓ (`stage.py:253-266`). Assembly = primary first, then sidecars by `(order, name)` ✓ (`:388-391`); discriminators `{"", "ocr"}` distinct ✓ (`:393-401`).

Composed dialect, production defaults:

```
vidclip-vlm-v1@p1.7f3a9c21#c4b2e01d+cp-v1+ocr-ppv6-cpu-v1
```

(58 chars. `pipeline_version` has no `minLength`/`maxLength`/`pattern` at `c2:66`, is `TEXT` in `storage/app/db.py:84`, and is hashed to fixed 64-hex at `pipeline.py:41-42`.)

---

## 3. Decisions

### D-01 — Semantic window = the C1 chunk. DP is span-parametric; the window length is recording's knob.

**Decision.** Every number in this design derives from `ctx.span_seconds` (`ingest_core.py:127` ← `pipeline.py:49-53`). DP ships and pays for itself at today's 10 s chunks with **zero cross-service ask**, and absorbs 60 s as a pure `.env` change on recording's side (escalation E-1).

**Rejected — buffer N chunks in a DP stage.** Five independently fatal blockers: `ingest_core.py:149-154` makes a chunk emitting zero units a **terminal, non-retried** dead-letter, so chunks 1–5 of every window are reported to recording as permanent loss; `INGEST_WORKERS=4 < 6` (and `INGEST_MODALITY_LIMITS="video=2"` worse) is a permanent permit deadlock; it breaks the ratified D16 invariant `dp_acked=1 ⇔ C2 durably written`, a cross-service contract break; `INGEST_ISOLATION=subprocess` spawns a fresh child per chunk with no shared memory; and `source{}` (`c2:17`) can cite exactly one `blob_ref`, so a redelivery of one chunk cannot reproduce the record.

**Rejected — a deferred second pass.** DP has five HTTP routes, two `create_task` sites, and no scheduler. Worse, `journal.processed` (`journal.py:64-74`) stores no `blob_ref` and the `pending.c1_json` row holding it is deleted on success (`:269-272`); `StorageClient` exposes exactly `get_blob` and `post_record`. **A second pass literally cannot find its bytes.** Minimum honest scope is a durable windows table + a `blob_ref` column + a trigger surface + a second `pipeline_version` namespace.

**Rejected — hierarchical (per-chunk AND per-window records).** Both levels land in the same `Scene:` line (`daylog.py:152,160`), roughly doubling block characters. Phase 3 measured acquisition falling **3.2× for a 3.7× rise in chars/block** (0.0772 vs 0.1786, p=0.0067). It costs 2× GPU to move the one measured variable the wrong way.

### D-02 — Payload shape: K stills in ONE multi-image call. Not `video_url`.

**Decision.** One `POST /v1/chat/completions` per chunk: `[{system}, {user: [text_label, image, text_label, image, …, task_text]}]`, frame time labels interleaved before each image (`Frame 3 (+12.5s):`), the task and the output contract **last**.

**Rejected — `video_url` (the POC's shape).** (i) Server-side decoding moves frame selection into the inference server's decord/av build, outsourcing the `frames.py:8-16` determinism promise to a dependency DP does not pin. (ii) `media_resolution` and `VideoMetadata(start_offset,end_offset,fps)` do not exist on an OpenAI-compatible wire — they are Vertex `google-genai` fields. (iii) The POC's central resolution anxiety (~205 px/frame at 20 min × 1 fps) **is a non-problem at 10–60 s**: the 25.2 M-px volume cap is not binding at 12 frames. Frames also keep the payload identical between mock and real backends, so the headless test exercises the production wire.

**Rejected — keeping N per-frame calls with a better prompt.** See §1.5(1). Not a tuning problem.

**Hard external dependency.** vLLM caps multimodal items per prompt via `--limit-mm-per-prompt`; the default is commonly `image=1`, and `services/inference/serve_vllm.sh:52-55` states verbatim that *"the POC's video knobs … are intentionally omitted."* **WS-A ships `scripts/vlm_probe.py` and it runs before any code is written.** Fallback if it cannot be raised: `screen-clip-single-v1` (K=1, the median frame) — still one call, one record, one dense description, strictly better than today at 1/4 the calls.

### D-03 — Sampling operating point.

| parameter | value | why this number |
|---|---|---|
| `VIDEO_CLIP_SECONDS_PER_FRAME` | **2.5** | K = `clamp(ceil(span/2.5), 2, 12)` → 4 frames @10 s, 12 @60 s (cap binds) |
| `VIDEO_CLIP_MAX_FRAMES` | **12** | hard prefill ceiling: 12 × 360 = 4,320 vision tok. Must be ≤ the server's `--limit-mm-per-prompt image=N` |
| `VIDEO_CLIP_MIN_FRAMES` | **2** | with one frame "what changed" is unanswerable and the model will confabulate |
| `VIDEO_CLIP_FRAME_WIDTH` | **768** | 768×480 = 24×15 = **exactly 360 Qwen3-VL tokens**. The measured problem at 768 was *reading text* — which this design no longer asks the captioner to do (D-06). Clamped at **1024** in the lenient parser with a WARN; 1280 is 2.78× the vision tokens and is the single easiest accidental cost blowup |
| `VIDEO_OCR_FRAME_WIDTH` | **1728** | the mac capture cap (`nucleus_capture.py:219`) — i.e. **no resample at all**, ×0.571 net, 13 pt em at **14.9 px**. CPU pixels are free |
| `VIDEO_ANALYSIS_PERIOD_S` | **2.0** | delta-probe period. Measured: at 4 fps realistic typing collapses to 1–3/255 (indistinguishable from a blinking caret); at 0.5 fps it is **11–19 against a floor of 2**. Sensitivity beats precision here |
| `VIDEO_CLIP_MAX_TOKENS` | **512** | ~2× the 260-token target. Today's 256 must carry caption *and* verbatim OCR |
| `VIDEO_CHARS_PER_SECOND` | **22** (16 caption / 6 ocr) | the dose budget, D-11 |
| `VIDEO_VLM_TEMPERATURE` | **0** | the determinism contract. Do **not** port the POC's 0.5 |

### D-04 — Frame extraction: two ffmpeg passes, no ffprobe, no scene detection, true PTS.

**Pass A (analysis, one decode, ~4 KB out):**

```
ffmpeg -v info -i chunk.bin \
  -vf "select='isnan(prev_selected_t)+gte(t-prev_selected_t\,2.0)',
       format=gray,
       tblend=all_mode=difference,
       lutyuv=y='if(gt(val,24),255,0)',
       scale=32:32:flags=area,
       showinfo" \
  -an -fps_mode passthrough -f rawvideo -pix_fmt gray pipe:1
```

stdout = D × 1024 bytes (one 32×32 change map per delta); stderr = D `pts_time:` lines parsed by the existing `_PTS_RE` idiom (`frames.py:42`).

**Why `select=` and not `fps=`:** measured — `fps=`'s nominal-t=4.0 output frame carried content from source **t≈4.93 s** (17,685 B post-cut vs 3,116 B for a true `-ss 4` seek). Event timestamps derived from `j/fps` would be wrong by ~1 s on a feature whose entire value is timestamps.

**Why binarize-at-full-resolution then area-average:** measured — a whole-frame *mean* absolute difference is blind to typing at **every** resolution tried (0.0000–0.0001 for typing against a 0.0000 static floor). One growing line of 26 px text on a 3024-wide canvas is ~0.03 % of frame area; a mean divides it away. Binarizing first and *maxing* over 32×32 cells recovers it:

| content | `peak` (max cell) | `spread` (cells ≥ 13) |
|---|---|---|
| flat black / white / gray (floor) | **2** | 0 |
| static screen + blinking caret | **2–4** | 0 |
| typing, realistic 40 wpm | **11–19** | 0–2 |
| typing, fast | 22–28 | 5–6 |
| scrolling code | 61–68 | 161–252 |
| dense scroll | 79–122 | 146–628 |
| app switch @ t=4.93 | **255** | **1024** |

The floor is exactly **2/255 and content-independent** (verified on flat black, flat white, flat 50 % gray, and a real static screen) — it is a deterministic artefact of the area downscale and is asserted in CI.

**Anchor accumulation (Python, no extra decode):** per-cell change maps are accumulated since the last OCR read, so slow typing crosses threshold even though no single delta does. This gets P1's sensitivity and P3's anchor property in one pass.

**Pass B (extraction, one decode, `split` to both widths):**

```
ffmpeg -v error -i chunk.bin -filter_complex \
 "[0:v]select='eq(t\,T0)+eq(t\,T1)+…',split=2[hi][lo];
  [hi]scale='min(iw,1728)':-2[h];[lo]scale='min(iw,768)':-2[l]" \
 -map "[h]" -frame_pts 1 -q:v 2 hi_%d.jpg \
 -map "[l]" -frame_pts 1 -q:v 3 lo_%d.jpg
```

**`-frame_pts 1` is mandatory.** Measured: requesting indices `{0,5,10,25}` against a 20-frame stream produced **3 files, exit code 0, empty stderr** — a positional zip onto the request list silently mis-assigns pixels, timestamps and discriminators. Filenames carry the PTS; mapping is by value, and the count is asserted.

Result: **2 subprocesses per chunk (was 6), ~1× realtime decode (was 2.5×).**

### D-05 — Keyframes die as a record identity. Fixed discriminators, fixed spans, a record set of exactly 2.

**Decision.**

| unit | `kind` | `discriminator` | `t_start` / `t_end` |
|---|---|---|---|
| clip description | `caption` | `""` | `None` / `None` → `build_c2` carries the **C1 strings verbatim** (`pipeline.py:79-80`) |
| screen-text digest | `ocr` | `"ocr"` | `None` / `None` → same |

The record **set** — count, discriminators, spans — is a pure function of `(chunk_id, pipeline_version)`. It does not depend on model output, decoder build, threshold outcomes, or which siblings survived a filter.

This single decision retires seven verified defect classes at once:

- **Survivor-ordinal renumbering.** `ocr:{index}` derived from a selection means one flipped borderline frame renames every later discriminator, rewriting records in place and orphaning the rest. Gone.
- **Decoder-dependent identity.** Measured: `fps=2.0` emits n=19 for a 9.667 s container and n=20 for 9.733 s — the 19↔20 boundary sits at ~9.7 s, **inside** the operating range (real segments measured 9.867/9.933 s). Any index-derived discriminator flips there. Gone.
- **Span-from-selection.** An OCR event whose `t_end` is "the next selected event's start" makes its span a function of siblings while its id is not → in-place span rewrite via `db.py:302`. Gone.
- **Model-output-dependent set.** An OCR pass returning `(none)` cannot change the record count. Gone.
- **`_sub_span` drift.** Deleted (`captions.py:46-66`) along with the 67 ms-record class.
- **Timestamp string-form mismatch — verified and severe.** `recording/app/timeutil.py:37-38` stamps `…Z`; `app/timeutil.py:24` `abs_time` returns `.isoformat()` → `…+00:00`; `storage/app/db.py:344-356` compares `t_start >= ?` as **plain strings**, and `'2026-07-25T04:00:00+00:00' >= '2026-07-25T04:00:00Z'` is **False** (`+` = 0x2B, `Z` = 0x5A). A record stamped in offset form at exactly the window boundary is silently dropped from the training window. Because both units pass `t_start=None`, **neither ever calls `abs_time`** and the defect is structurally unreachable. A test asserts `c2["t_start"] == c1["t_start"]` byte-for-byte.
- **Ordering nondeterminism.** `db.py:355` is `ORDER BY t_start ASC, rowid ASC`; rowid is first-write order. Caption and OCR share `t_start` — but they route to *different* `Segment` fields (`daylog.py:114-120`), so their relative order is unobservable downstream. Across chunks `t_start` is distinct. Fully deterministic.

**Rule carried forward (write it in `app/vision/emit.py` beside the aggregation):** *if `VIDEO_OCR_MAX_EVENTS` is ever relaxed to emit >1 ocr record per chunk, the discriminator MUST become a quantisation of a grid that is itself a pure function of the declared C1 span — never a survivor ordinal, never a raw decoder frame index, never a text hash — and every unit MUST get a distinct `t_start`.*

**Always emit the OCR unit**, with `content.text = ""` when nothing legible was found. `""` validates at all four gates (`c2:35` is a bare string, no `minLength`) and `daylog.py:112-113` (`if not text: continue`) drops it downstream at zero cost. Record *presence* is then the coverage signal, which is exactly what the cross-service invariant in R3 needs, and it keeps the set fixed at 2.

### D-06 — OCR: a deterministic CPU specialist at native resolution, behind an HTTP seam. Never the captioner.

**Decision.** `VIDEO_OCR_BACKEND=ppocr` → a co-located loopback sidecar service (`sidecars/ocr/`, its own venv, its own `run.sh`, the `serve_vllm.sh` posture) running PP-OCRv6 det+rec ONNX on CPU. Returns `[(text, bbox, confidence)]`. Default execution provider **CPU**, and the EP is in the version tag because ONNX Runtime is not guaranteed bit-exact across providers. Both model files' sha256 are pinned in config and asserted against the sidecar's `GET /health` **at graph resolution** — a swapped model file fails loudly at resolve, not silently in the corpus.

**Why not the VLM.** Measured cost: making the same 32B read tile-crops is **+1,558 node-s per screen-hour against the 19.6 the entire caption path costs — 3.1× the thing it augments**; full frames is 73×. CPU is **$0.0012–0.007 per screen-hour**. And the quality argument is worse than the cost argument: the POC's Phase-2 sweep, on real footage, measured **Qwen3-VL-32B OCR at 0.143** against Qwen2.5-VL-32B **0.857** and -72B **1.000** — while Qwen3-VL *beat* Qwen2.5-VL on public VideoMME. DP's configured captioner is the exact family the POC ruled out for this task. A CTC decoder can misread a glyph; it cannot fabricate a clause.

**Why a separate service and not a library.** Verified by `pip download`: `paddleocr 3.7.0` → `paddlex[ocr-core]` → **`numpy<2.4`**, which conflicts with DP's installed numpy 2.5.1 (transitive via the faster-whisper stack). It would also drag a pinned `opencv-contrib-python`, `shapely`, `pyclipper`, `pypdfium2`, `pandas`. The seam quarantines all of it and makes the model a **URL change**: PaddleOCR-VL (0.96 B), dots.ocr (3 B), GLM-OCR (1.3 B), or a second vLLM are all one config away.

**Backends:** `off` | `mock` (headless default, no deps, no network) | `ppocr` | `vlm` (the A/B arm, so the comparison can be *run* rather than argued). Single resolver, unknown → `off` in **both** `select()` and `version_tag()` (`audio/diarize/__init__.py:24-33`).

### D-07 — Event-driven OCR: floor grid ∪ change events, rank-free cap, chunk-local.

```
grid            = the Pass-A probe times (true PTS)
accum[k]        = per-cell accumulated change map since the last OCR read
peak[k], spread[k]

class[k] = IDLE   if peak[k] <= VIDEO_OCR_IDLE_PEAK   (8)
         = LAYOUT if peak[k] >  VIDEO_OCR_LAYOUT_PEAK (40) or spread[k] > 32
         = TEXT   otherwise

floor_times   = the FIRST grid point whose absolute epoch second is >= the next
                multiple of VIDEO_OCR_FLOOR_S after epoch(c1.t_start); none if no
                such point exists in this chunk.       # pinned convention, no t_prev ambiguity
change_times  = [t for t in grid if class[t] != IDLE]
selected      = sorted(floor_times ∪ change_times)
if len(selected) > VIDEO_OCR_MAX_EVENTS:
    selected = even_spaced_subset(selected, cap)       # frames.py:133-135 idiom
```

**The cap is rank-free.** A magnitude rank (`sort by -peak`) is not stable: on the measured scroll clip 48 of 49 intervals beat threshold, clustered at median 0.0192 / p90 0.0257 / max 0.0262 — top-1 among 48 values inside a 0.007-wide band. Two ffmpeg builds ranking that cluster differently would emit *different frames* (and, before D-05, different records). Even-spaced subsetting over the **time-sorted** survivors depends only on the boolean threshold, whose measured margins are 130× above the static ceiling and 4.8× below the scroll median.

Post-read, chunk-local only (cross-chunk state is forbidden — a per-process buffer would break fleet determinism):

1. drop boxes with `confidence < VIDEO_OCR_MIN_CONF` (0.60);
2. **use the bbox** to sort into reading order and assign a **region role** — `titlebar | tab | sidebar | main | compose | message | toolbar | statusbar | dialog | notification`. This is the semantically useful 80 % of "location", delivered as a word, at zero contract cost. The pixel geometry is then **discarded** (D-08);
3. drop lines shorter than `VIDEO_OCR_MIN_CHARS` (4);
4. **deterministic secret redaction** — AWS-key shapes, `sk-` / `ghp_` / `xox[baprs]-`, ≥32-char base64 runs, PEM headers, Luhn-valid 13–19-digit runs, and all-bullet/asterisk fields → `[redacted:secret]`. A prompt rule is not an access control; this is. Counted as `dp_ocr_redactions_total`;
5. drop an event whose normalized text is ≥ `VIDEO_OCR_DEDUP_RATIO` (0.92) similar to the previous kept event **in this chunk**;
6. render to a **single line** (no `\n` — see D-12), truncated at the budget on a word boundary.

### D-08 — OCR output shape: separate `kind='ocr'` records, region words in the text, **no bbox in C2**.

**Decision, answering the founder's question 5 directly: BOTH, at different layers.** OCR strings are *injected into the caption prompt* (input layer) **and** emitted as their own `kind='ocr'` record (record layer). The caption's `content.text` contains **no verbatim OCR dump** — `_weave_ocr` is deleted.

**Why separate records win at the record layer:**

| axis | separate `kind='ocr'` | woven into the caption (today's D8) |
|---|---|---|
| downstream channel | `daylog.py:117-118` → `seg.ocr` → **its own `World text (OCR):` line joined by `" \| "`** (`:164`) — event boundaries survive; the same labelled channel the *validated* `SpeedProfile` uses (`profiles/speed.py:27`) | falls into `daylog.py:119-120`'s `else` → concatenated into the single `Scene:` line with a bare space |
| volume | 1 record/chunk | measured: the same OCR string re-transcribed 4–8× per 10 s and ~48× per 2-min block — the *majority* of block characters |
| machine-checkability | a bare string in its own field | prose-embedded OCR is unmatchable by exact comparison: Qwen2.5-VL scored **0.000 strict / 0.09 benchmark / 0.89 lenient** on OCRBench purely because it wraps the answer in prose (`rescore_ocrbench.py:1-9`) |
| hallucination | produced by a non-generative CTC engine with a confidence score | the POC's **#1 hallucination class across all models**; the concrete failure was `"HE 310"` vs visible `"HE 319"` |
| independent filtering | continuum can down-weight, confidence-gate or drop the channel | inseparable |
| envelope cost | +629 B/chunk (77 % of a short record) | 0 |

**Nothing new is invented:** `ocr` is already in the frozen enum (`c2:34`), already emitted (`captions.py:136-145`), already rendered. The founder's instinct not to add a record *type* is honoured exactly.

**Why bbox is NOT emitted.** `grep -rn enrichments product/services/continuum/app/` returns **one hit — `app/synth.py:50`, the synthetic-record generator.** Enrichments reach storage and stop. So `enrichments.text_regions[]` is a four-file, two-service, founders-routed additive edit for a field with zero readers. The diff is written and parked (§6); the trigger to spend it is the first real geometry consumer, paid down in one freeze-additive commit together with root `quality{}`.

### D-09 — OCR-as-input: the captioner is told the text and forbidden to invent it.

**Decision.** The rendered OCR strings are injected into the clip prompt under a labelled `## On-screen text (read by a specialist pass — INPUT, not target)` block, governed by the POC's audio rule transposed (`generators.py:58-60`): **use it to NAME and ground what is visible; never copy it out; never state a name, number or string that does not appear above.**

This converts the #1 hallucination class into a **mechanically checkable property**: every double-quoted span in the caption must appear (case-folded, whitespace-collapsed) in the injected OCR block. Shipped as an eval scorer *and* as a production counter `dp_caption_ungrounded_quote_total`. The POC had to buy a second frontier-model verification pass over 39,547 windows to approximate this; here it is free and exact.

It also resolves the low-res/high-res contradiction: the captioner reads layout at 768 px and *never reads text*, while the OCR pass reads text at 1728 px. Without the injection, a 768 px captioner instructed to "name the thread" would sit next to a 1728 px OCR record in the same block, and the amplifier — told to *"keep every exact colour, number, name and on-screen/world text verbatim"* (`profiles/speed.py:62-64`) — would amplify both sides of a contradiction 48 times.

**Consequence (accepted):** `screentext` must complete before `clipcap` starts. They serialise. Cost quantified in §7; it is affordable at both 10 s and 60 s.

### D-10 — No timeline / interval lines. One paragraph.

**Decision.** The caption is **one paragraph, one line, ≤ budget**. No per-interval lines.

**Rejected — the POC's timeline shape** (10 lines × 83 words at the 1-min tier). It re-creates the 48-fragment problem inside the new record: 12 chunks × 4 mandated interval lines = 48 fragments per 2-min block, each still scoped to a 2.5 s window. And a mandated line count *punishes honesty*: on a static screen — measured SSIM 0.9998 — the model must pad ~440 chars per chunk to satisfy "EXACTLY N lines", which is precisely the redundancy this design exists to remove. The POC's own anti-padding rule does not transfer: a static screen has no "ambient context", only more UI chrome.

Cross-frame reasoning is delivered by the paragraph ("types a two-point reply, sends it, then switches to Slack"), which is the actual goal-2 ask.

### D-11 — The character budget is a chars-per-second-of-life dial, and it is a correctness knob.

**Decision.** `VIDEO_CHARS_PER_SECOND = 22` total, split **16 caption / 6 ocr**, applied as `cap = round(R × span_seconds)` with deterministic sentence-/word-boundary truncation in a pure `app/vision/budget.py`.

This resolves the formula contradiction in the source proposals (4.7 chars/s at `span=10` yields 47 chars — absurd): **4.7 chars/s is a per-block-per-second-of-life measurement, not a per-record cap.** Dose = `48 retellings × ~1,034 chars ÷ source chars per block`, and source chars per block = `R × block_width_seconds`. So the same `R` gives the same dose at any chunk length, which is exactly what makes the design span-parametric.

| configuration | video chars/block | + Heard | dose | vs `EXCERPT_CHARS=6000` |
|---|---|---|---|---|
| today, 4 kf, `seg=10 blocks=12` | 9,600 | 10,250 | **4.8×** | **41 % truncated** |
| **this design, R=22, `seg=10 blocks=12`** | 2,640 | 3,290 | **15.1×** | 45 % headroom |
| **this design, R=22, `seg=60 blocks=2`** | 2,640 | 3,290 | **15.1×** | 45 % headroom |
| this design, R=22, `seg=60 blocks=1` | 1,320 | 1,645 | **30.2×** | 73 % headroom |
| Phase-3 arm that **failed** | 5,740 | — | 8.6× | — |
| validated 5-min baseline | 1,410 | — | 32× | — |

**These are correctness knobs, not tuning knobs. Put that sentence in the stage docstring.** `VIDEO_CHARS_PER_SECOND=15` reaches dose 20×; `=30` drops to 11×. Continuum's `block_segments` is the stronger lever (halving block width doubles dose at fixed `R`) and it costs them 2× amplification generations — escalation E-4.

**This also closes the ordinal-truncation defect.** `daylog.py:158-164` renders header → `Scene:` → `Heard:` → `World text (OCR):`, and `speed.py:117` slices `block.text[:6000]`. OCR is **last**, so truncation eats it whole. At today's caption lengths the `World text (OCR):` label begins at offset ~7,744 and **100 % of the OCR channel is cut before amplification, every block, all day** — silently, with every downstream assertion green. The budget keeps the whole block at ~3,300 chars, so the OCR line always survives. A renderer reorder is escalated (E-4) as belt-and-braces.

### D-12 — `content.text` is a single line.

**Decision.** No `\n` in `content.text`, ever. Separator is `" · "`.

`daylog.py:92` only `.strip()`s the edges and `:160` is `"Scene: " + " ".join(captions)`. A multi-line caption yields `Scene: <line 1>` with lines 2..N floating **unlabelled** in the block — confirmed happening today in real Phase-3 blocks (`Scene: Headline: …` followed by bare `Actions:` lines). Enforced in `app/vision/emit.py` and asserted in tests.

### D-13 — Prompt registry: a git-tree pack whose content digest IS the dialect.

**Decision.** `app/vision/prompts/` holds one file per prompt (`.prompt.md`: front-matter + `[system]` / `[user]`), plus `routes.json` (scenario → pack id) and one Python module (~150 lines) that loads, validates, resolves and hashes them.

```python
PACK_VERSION = "1"                       # human token, greppable, hand-bumped
PACK_DIGEST  = sha256(
    b"\0".join(normalised(spec) for spec in sorted(_PACKS.values(), key=id))
  + b"\0MAP\0" + scenario_map_bytes
  + b"\0FAM\0" + family_default_bytes
).hexdigest()[:8]
```

`normalised(spec)` = id + role + system + user template + output schema + declared decode params, with line endings normalised and trailing whitespace stripped per line. So a reflow, a CRLF checkout or a trailing newline does **not** fork the corpus; every model-facing byte, every decode parameter and the output schema **do**.

Composed into the primary's fragment via the backend module, so the mock captioner — which never reads a prompt — contributes `""` and headless fixtures do not re-key on a prompt edit.

**Plus `cfg_tag(vs)` = `#` + sha8 over an explicit `OUTPUT_AFFECTING` allowlist**, which is the half every source proposal got wrong:

```python
OUTPUT_AFFECTING = ("pipeline","backend","vlm_model","clip_frame_width","clip_seconds_per_frame",
  "clip_max_frames","clip_min_frames","clip_max_tokens","chars_per_second","caption_chars_share",
  "analysis_period_s","pixel_threshold","grid","idle_peak","layout_peak","layout_spread",
  "ocr_backend","ocr_ep","ocr_model_sha_det","ocr_model_sha_rec","ocr_frame_width","ocr_min_conf",
  "ocr_min_chars","ocr_dedup_ratio","ocr_floor_s","ocr_max_events","ocr_max_tokens","ocr_stamp",
  "privacy_filter","scenario","prompt_dir_fingerprint")
OPERATIONAL_ONLY = ("vlm_url","vlm_api_key","vlm_timeout","ocr_url","ocr_timeout","ocr_threads",
  "structured_mode","max_prompt_images_check")
```

`tests/test_emission_law.py` asserts `set(OUTPUT_AFFECTING) | set(OPERATIONAL_ONLY) == set(VisionSettings.__dataclass_fields__)` — **a new field cannot be added without being classified.** This closes the verified hole where `VIDEO_VLM_MODEL`, `VIDEO_OCR_STAMP`, `VIDEO_OCR_REGIONS` and the token budgets all rewrote record bytes under an unchanged `record_id`. Note `vlm_url` and `ocr_url` are deliberately **excluded**: moving an endpoint to a new host with the same served model cannot change output, and forking the corpus on a DNS change would be a self-inflicted double-count event.

**Loading discipline.** Packs are read **once per process at import** and never re-stat'd. A `mtime` cache would create a TOCTOU window: `pipeline_version` is computed at ACCEPT (`main.py:358`) and the prompt is rendered at RUN in a worker (or, under `INGEST_ISOLATION=subprocess`, in a different process) — a file edited while a backlog drains would stamp prompt A's digest onto text produced by prompt B, reintroducing the exact bug this mechanism exists to close. Production bakes prompts into the image; `VIDEO_PROMPT_DIR` set ⇒ WARN at startup and `prompt_source=dir` in `/health`.

**Resolver discipline.** Unknown `VIDEO_CLIP_PROMPT` resolves to the **pinned default in both `select()` and `version_tag()`** — never fork, never collide. An unvalidated id is never stamped verbatim.

**Tooling** (goal 4 — evolvable without a code change): `python -m app.vision.prompts show --pack screen-clip-v1 --frames 12 --span 60` prints the exact assembled wire text; `python -m app.vision.prompts relock` bumps `PACK_VERSION`, rewrites `LOCK.json`, archives every pack's full text to `archive/p{N}.json`, and prints the before/after `pipeline_version` side by side. A researcher edits a markdown file and redeploys; the dialect forks automatically; there is no version bump to forget.

**Scenario selection is per-deployment, not per-chunk.** `Stage.version_fragment(settings)` receives only `Settings` (`stage.py:179-182`); `main.py:358` computes the dialect before the blob is fetched. Making the scenario per-chunk requires touching **three** `pipeline_version` producer sites — `main.py:358` (accept), `main.py:204` (`_redrive_pending`), and `main.py:279` `_current_pv(modality)`, whose signature structurally cannot see `device_id`. Breaking the third one **defeats the durable dedup backstop for all video**: after any restart every redelivery would fail the receipt comparison at `journal.py:309-312` and fully reprocess, minting a second disjoint record set. Rejected. Instead: `VIDEO_SCENARIO` is env (in `OUTPUT_AFFECTING`), and a `device_id`-prefix **mismatch detector** (`mac-cli-` / `ext-chrome-` / `phone-web-`) emits `dp_video_scenario_mismatch_total{expected,seen}` + a once-per-prefix WARN, so a misrouted fleet is visible in ten seconds. Open question O-3.

### D-14 — Legacy coexists byte-identically; the flip and the rollback are one env var and are honest about what they can undo.

**Decision.** `VIDEO_PIPELINE ∈ {keyframe, clip}`, resolved by a **single** `resolve_pipeline()` with **unknown → `keyframe`** (the shipped, safe path). Both stage sets gate on the resolved name, never on the raw env string — otherwise a typo disables *both* graphs, `resolve()` finds zero enabled primaries and raises `GraphResolutionError` from `main.py:358` **before** the dedup fast path, 500-ing every video ingest.

`keyframes.version_fragment()` returns `""` in legacy mode via a **single-entry frozen exemption** to the registration rule (R1), so the legacy dialect reproduces `vidproc-vlm-v0` **byte-for-byte**. The exemption set is a named frozen constant with a comment: *it exists solely to reproduce pre-migration record_ids; no new stage may join it.* A test asserts `pipeline_version(legacy) == "vidproc-vlm-v0"` exactly.

**Rollback restores behaviour, not the corpus.** New-dialect records persist (`journal.py:296-298`); `/context` has no delete. Stated in the runbook in those words. `DP_DIALECT_FREEZE=1` makes `_current_pv` return `None` so a stale-dialect receipt is *served* rather than reprocessed during the flip window — a DP-local, five-line mitigation for the mass-reprocess-on-redelivery hazard.

**Deploy discipline: drain-and-replace, never rolling.** During a rolling restart two replicas resolve two dialects, and a single 2-min day-log block contains records from both — `daylog.py` has no `pipeline_version` filter (`context_reader.py:7` says filters exist only *"when they're ratified"*).

### D-15 — Local-first; Vertex/Gemini is an eval oracle, not a serving path.

**Decision.** Ship self-hosted. The captioner backend seam (`mock | vlm | vertex`) exists and `vertex.py` ships as a documented stub with the verified call shape (`Part(file_data=…) + VideoMetadata(fps=…) + GenerateContentConfig(media_resolution=MEDIA_RESOLUTION_HIGH, response_schema=…)`), but the path is not enabled.

Measured POC rate: `HIGH`@2fps = **527.5 prompt tok per second of video** → an 8 h screen day is 15.2 M prompt tokens → **$30.4/user-day online, $15.2 batched** at the POC's coded Pro pricing. That is 36–72× the self-hosted figure in §7. Indefensible for continuous capture.

**The correct role, and it is worth doing:** a **one-off graded oracle** — 200 windows at HIGH ≈ **$70 total** — judged blind with the POC's frame-grounded rubric (`phase3.2_evals/judge_panel.py:35-43`). It is the only cheap way to get a quality *ceiling* for a local model whose family the POC's own data predicts will underperform. Budget it as a line item in WS-H, not as an architecture.

---

## 4. The record-vs-mutation law

*Destined for `CHARTER.md`. Written to that standard.*

### 4.1 The invariant

> A C2 record is not "a thing we computed". It is **one independently-placeable, independently-labelable, independently-losable claim about a span of the user's life.**
>
> - **Placement** is `t_start`, and only `t_start` (`daylog.py:109-114`; `t_end` is read zero times).
> - **Labeling** is `content.kind`, and only `content.kind` (`daylog.py:115-120` → three labelled lines at `:159-164`).
> - **Identity** is `record_id = sha256(chunk_id ␀ pipeline_version [␀ discriminator])` (`pipeline.py:33-46`), and `/context` is a blind upsert on it (`storage/app/db.py:302`).
> - **Loss** is per-stage: a failure removes exactly the units that stage alone emits.
>
> Therefore: **information deserves a record iff it needs its own place, its own label, or its own failure. Information that makes an existing record's declared *structure* more precise is a mutation. Information about *how* a claim was obtained is an enrichment — or nothing.**

### 4.2 The decision procedure — five ordered tests, first match wins

Apply to any signal **S** derived from one C1 chunk.

**T1 — DERIVABLE.** Is S a pure function of *this* chunk's bytes plus config — no neighbour chunk, no other stream, no day context?
→ **No: not a DP record.** It belongs to continuum or to a windowed pass that does not exist. DP's ingest is per-chunk end to end: `process_chunk` takes one C1 + one blob; dedup is keyed on `chunk_id`; the journal is keyed on `chunk_id`; delivery is at-least-once; a chunk emitting zero units is a *terminal* dead-letter (`ingest_core.py:149-154`).

**T2 — REACHABLE.** Does S reach a consumer that exists **today**? Only `content.text` of a frozen `kind` reaches the trainer. `content.segments` is read **only** for `kind=='transcript'` (`daylog.py:94`). `enrichments` is read **nowhere** in continuum.
→ **Neither reachable nor read by any live consumer: do not emit it.** Store nothing you cannot spend. (Re-apply this test when a consumer lands; it is a gate on *when*, not a permanent veto.)

**T3 — SPINE.** Is S the modality's answer to "what happened in these bytes" — the thing whose absence means the chunk was not processed?
→ **PRIMARY unit(s)** from `assemble()`. Exactly one enabled primary per modality (`executor.py:114-119`); its fragment is the base dialect and must be non-empty (`:121-126`).

**T4 — EDITS.** Does producing S change bytes a record already claims?
→ **Structure-fill** — it fills a field the parent already declared and left empty (`segments[].speaker`, which `c2:47` pins as *required-nullable so the key never appears/disappears*; `enrichments.*`, whose stated purpose at `c2:64` is *"shape stable so world-data enrichment never changes C2"*) → **MUTATE**: `kind='mutate'`, `writes ⊆ primary.mutable_slots`, non-empty `version_fragment` mandatory and structural (`stage.py:184-188`, enforced at registration `:226-230`).
→ **String-change** — it would make `content.text` differ from what a previous run wrote → **FORBIDDEN as a mutation.** `content.text` is the training target; a rewritten target under a stable `record_id` is invisible to every diff and silently overwrites. Either do the refinement *inside the producing stage before assembly*, so exactly one target string per claim ever exists, or fork `pipeline_version` and mint a new record.
  *Mechanical test:* could two workers on different config both honestly claim to be right? If yes → fork, not edit.

**T5 — CHANNEL / SPAN.** Does S own a frozen `kind` that routes to a different day-log line, **or** a span that is independently addressable at continuum's `segment_seconds`?
→ **NEW RECORD** with its own discriminator. Otherwise it is a field of an existing record's text, or a stage-internal slot.

**Fallthrough → ENRICHMENT** (subject to T2) or **stage input**.

### 4.3 The five riders

**R1 — FORK RIDER (mechanised).** *Any enabled stage whose configuration can change the bytes of a record it does not itself emit MUST contribute a non-empty `version_fragment`.* Mechanised as: **a sidecar declaring a non-empty `provides` must return a non-empty fragment when enabled** — a provided slot exists only to be consumed, i.e. to change someone else's bytes. One frozen exemption (`keyframes`, legacy reproduction only). Conversely, a sidecar that only **adds** records and feeds nothing declares **no** fragment (`translate/__init__.py:4-7`, `injected_caption.py:25-28`), because forking the whole chunk's dialect on an additive toggle re-keys the primary for a change that did not touch it.

**R2 — INDEPENDENCE RIDER.** Two records may describe the same second only if a consumer can use either **without** the other. If B is meaningless or misleading without A, B is a mutation, an enrichment, or a field of A's text. Corollary: every sidecar record must be **self-anchored** — it carries its own context (app, region, time offsets) inside its own `content.text`. **Corollary 2 (added 2026-07-24, coupling ratification):** where record B's specific strings are *grounded in* record A's (the OCR record's strings are injected into the caption under D-09), the pair is **one witness rendered on two channels** — no consumer may treat their agreement as corroboration. This bounds the amplifier's 48×-per-block restatement of a jointly-sourced pair. (The broader "R2 is judged only at consumption, R1's fragment discharges the derivation" reading was considered and **rejected**: R1 forces only a `version_fragment`; it cannot discharge R2's "misleading without A" clause.)

**R3 — DIALECT-HONESTY RIDER.** `pipeline_version` states the **attempted** dialect, never what succeeded. It is resolved before any stage runs (`executor.py:228-237`), so it cannot vary with outcome — which is what preserves determinism. Therefore: (a) `best_effort` ⇒ additive-only, never a mutate (structural, `stage.py:221-225`), and never upstream of a required stage (`executor.py:202-219`); (b) **never `best_effort` + a non-empty fragment** — that stamps a dialect claiming a property the record set may not have; (c) **never use the `discriminator` as a back-door dialect carrier** — two records under one `pipeline_version` with different producers is exactly the lie the consistent-dialect promise forbids; (d) absence is diagnosed by **record presence + a metric**, never by the dialect string, and **never by fabricating a placeholder claim about the user's life**; (e) the cross-service invariant that follows: *continuum must never infer "no on-screen text" from an absent OCR record.*

**R4 — SET-STABILITY RIDER.** The record **set** — count, discriminators, spans — must be a pure function of `(chunk bytes, settings)` and must not depend on model output, decoder build, **stage outcome** (added 2026-07-24: this makes `best_effort`'s illegality for a fragment-bearing stage enforceable from R4 as well as R3(b)), or which siblings survived a filter. Prefer fixed discriminators and chunk-span records. Where the set must vary, discriminators are quantised from a grid that is itself a pure function of the **declared C1 span** — never a survivor ordinal, never a raw decoder frame index, never a hash of model output — and every unit gets a distinct `t_start`.

**R5 — BUDGET RIDER.** Every new record class must name (i) its consumer today, and (ii) its characters-per-second-of-life budget against the day-log block. A class that cannot answer both does not ship. Block characters are the training currency: acquisition was measured falling **3.2× for a 3.7× rise** in chars/block.

### 4.4 The worked table

Verdicts: **PRIMARY** / **RECORD** / **MUTATE** / **ENRICH** / **SLOT** (stage-internal) / **NOT-C2**.

| # | Signal | First test that fires | Verdict | Fragment? |
|---|---|---|---|---|
| 1 | Clip-level dense description | T3 | **PRIMARY**, `discriminator=""`, C1 span verbatim | yes (base) |
| 2 | Per-keyframe caption | T5 fails (no distinct channel; measured SSIM 0.9998 on 3 of 4 transitions) | **RETIRED** — 4× records to render 4 near-identical fragments into one space-joined line | — |
| 3 | OCR text | T5 (own frozen kind → own labelled line) | **RECORD**, `discriminator="ocr"`, one per chunk, aggregated with `+Ns` offsets | **yes** — it is an *input* to the primary (D-09), so its config is caption-affecting: the fork test says fork |
| 4 | OCR bbox geometry + per-region confidence | **T2 fails** (continuum reads `enrichments` nowhere) | **NOT EMITTED.** Used internally for reading order + region role, then discarded | n/a |
| 5 | Region role (`compose`, `titlebar`, …) | T5 fails; T2 passes via `content.text` | **field of the OCR record's text** | inherited |
| 6 | UI / frontmost-app identity | T5 fails | **field of the caption's text** (and the `app` key of the guided-JSON schema) | inherited |
| 7 | Window title | T5 fails; it *is* on-screen text | **inside the OCR record**, role = `titlebar` | inherited |
| 8 | Detected user intent | T3 — this is the primary's job | **caption content.** An "intent" record has no channel and is unreadable alone (R2) | inherited |
| 9 | Scroll / typing activity | T2 as a record (no channel expresses it) | **SLOT** — drives the idle gate and OCR event selection; at most a clause in the caption | it *is* a fragment input |
| 10 | Idle / no-activity | T3 | **caption content** — an explicit short idle description. Emitting nothing is indistinguishable from a capture gap | inherited |
| 11 | Speaker identity (a name for a diarized label) | T4 structure-fill | **MUTATE** on the audio primary, chained after `diarize` by `(order,name)` | **mandatory** |
| 12 | Face identity | T2 fails (`enrichments.faces` unread) | **DEFER.** If the name must be learned it belongs in the caption text, making the captioner its consumer | — |
| 13 | Geolocation | T2 fails; it is *carried*, not derived | **NOT a mutate stage.** A mutate with an empty fragment can never run; forking the dialect to copy a C1 field is dishonest. The primary fills its own `enrichments.places` if a consumer ever lands | **no** |
| 14 | Translation | T5 (same span, different language, parallel text) | **RECORD**, `discriminator="translation"` (shipped) | none — additive only |
| 15 | Acoustic events | T5 (+ exists when the primary is empty) | **RECORD**, `discriminator="acoustic"` (shipped) | none today — **flagged**: by R1 this is a latent hole; retro-fit `+ac-<backend>-v1` when a real backend lands. Audio owner's call |
| 16 | OCR correction / verify pass | T4 string-change | **NOT a mutate and NOT a second record** — selective in-stage re-read of sub-threshold regions, drop on disagreement, so exactly one OCR string per event ever exists | — |
| 17 | Per-record quality / confidence | **T2 fails** (`daylog.py:38`: *"C2 v0 has no quality field yet"*; `corpus_blocks(quality_min)` gates on nothing) | **NOT EMITTED.** Honest home is a root `quality{}` at the next freeze-additive | — |
| 18 | Summary-of-summaries (5-min / hourly) | **T1 fails** | **NOT-C2 — continuum's job.** `source.chunk_id` + `blob_ref` cannot be honestly filled; the reprocess path re-pulls exactly one blob | — |

---

## 5. v1 prompts

### 5.1 `screen-clip-v1` — the dense screen-capture caption (active chunks)

Front-matter: `id: screen-clip-v1 · role: clip · scenario: screen-mac,screen-browser,screen-generic · max_tokens: 512 · temperature: 0 · schema: clip-json-v1`

**[system]**

```
You are a screen-recording annotator for a personal memory system. You are shown still
frames sampled in time order from ONE continuous clip of one person's computer display,
plus the on-screen text a specialist pass has already read from those same frames.

Report what the PERSON was doing and WHAT CHANGED across the frames. The frames are one
continuous scene, not separate pictures — never describe them one at a time.

RULES, applied strictly:

1. NAME THE SURFACE. Start from the application or website in focus and the specific view
   inside it. Use the supplied on-screen text to get the name right. If you cannot
   identify it, write "an unidentified <kind> window" and move on. Never guess a brand.

2. SAY WHAT THE PERSON IS DOING, from evidence across the frames: typing (text grows
   between frames), reading (content stable, caret still), scrolling (same document,
   content shifted), switching window or tab, selecting, dragging, filling a form, or
   watching a video playing inside the screen. If a video, call, game or shared screen is
   playing inside a window, you are describing A RECORDING OF A SCREEN, not the world:
   write "a video playing in <app> shows ...", never as if the person were in that scene.

3. USE THE SUPPLIED TEXT TO NAME, NOT TO TRANSCRIBE. The on-screen text block below is
   INPUT, not output. Use it to name the thread, the file, the page, the person. Do NOT
   copy it out. You may quote at most ONE short phrase, in double quotes, and only if that
   exact phrase appears in the supplied text. NEVER state a name, number, price, address
   or quoted string that does not appear there. A separate record carries the verbatim
   text; your job is the action.

4. CONTENT OVER CHROME. The subject is what the person is working on — the sentence being
   written, the code being edited, the message being read. Menu bars, docks, toolbars, tab
   strips, clocks and badges are chrome: mention them only when they carry the meaning (a
   notification arriving, the tab just switched to).

5. IF LITTLE CHANGED, SAY SO AND STOP. Do not pad and do not repeat yourself. Describe
   what IS there: which window has focus, which document is open, where in it the person
   is. Two sentences is a complete answer for a still minute.

6. SENSITIVE CONTENT. If a password or passphrase field, a one-time code, a full card
   number, an API key, a private key or an obvious secrets file is visible, state the FACT
   ("a password field is focused"), set "sensitive" to true, and stop. Never reproduce the
   value, not even partially.

7. NO SPECULATION AND NO META. Describe only what these frames show. No clock times, no
   dates, no inferences about mood or intent beyond the screen. Never mention frames,
   clips, sampling, models, this task, or what you can or cannot see. This record must be
   understandable alone: never write "continues", "as before", or "the previous clip".
```

**[user]**

```
This clip is {span_s:.0f} seconds of {scenario_label}. {n} frames follow in time order, at
{offsets} seconds from the clip start.

## On-screen text, read at full resolution by a specialist pass (INPUT, not target)
{ocr_block}

Reply with ONE JSON object and nothing else:
{"app":        "<application, site or window in focus, or 'unknown'>",
 "activity":   "<at most 12 words, verb first; or 'unclear'>",
 "description":"<{words_lo}-{words_hi} words, ONE paragraph, no line breaks, following the
                rules above>",
 "sensitive":  <true|false>}
```

*Frame labels* `Frame {k} (+{t:.1f}s):` are interleaved as text parts before each image.
*Render* (deterministic, DP-side, single line): `f"{app} — {activity}. {description}"`, truncated at `budget.caption` on a sentence boundary.
*`{words_lo}-{words_hi}`* derive from the budget: at `span=60, R=16` → `130-160`; at `span=10` → `20-28`.

**Ships alongside:** `screen-clip-idle-v1` (fires when the accumulated delta never crosses the idle threshold — 3 frames, rules 1/4/5/7 only, `activity` may be `"unclear"`, 25-40 words, `max_tokens: 160`); `screen-clip-single-v1` (the `--limit-mm-per-prompt` fallback, K=1); `camera-clip-v1` (stub, proves the seam); `per-frame-v0` (the frozen legacy text, fingerprinted so even the rollback is covered).

### 5.2 `screen-ocr-v1` — the VLM OCR arm (A/B and fallback only; `ppocr` takes no prompt)

Front-matter: `id: screen-ocr-v1 · role: ocr · max_tokens: 900 · temperature: 0 · schema: ocr-json-v1`

**[system]**

```
You transcribe text that is visible on a computer screen. Output the text only.

1. VERBATIM OR NOTHING. Copy characters exactly as rendered — spelling, case, punctuation,
   digits, currency, units. If a word, digit or string is too small, blurred, clipped or
   ambiguous, OMIT IT. Never complete a truncated string. Never correct a typo. Never
   translate. Never infer a value from context. Substituted digits and invented strings
   are the most damaging error in this task; omission is always the right choice when
   unsure.

2. MEANING FIRST. Return the text a person would care about later: the document, the
   message, the code, the field being filled, the subject line, the error, the search
   query. Do not dump the whole interface.

3. NO CHROME. Skip menu bars, docks, toolbars, sidebars of unrelated items, button labels,
   ads, boilerplate footers and the system clock — unless that element IS the event (a
   notification arriving, an error banner, a tab title that just changed).

4. GROUP AND LOCATE. Group text into reading-order regions — a paragraph, a field, a title
   — never individual words. Give each region a role from: titlebar, tab, sidebar, main,
   compose, message, toolbar, statusbar, dialog, notification.

5. SECRETS. Never transcribe a password, a value in a masked field, a one-time code, a
   card or account number, a government ID, an API key or a private key. Emit the region
   with its role and the text "[redacted: password]" (or card / id / key), so the layout
   is still recorded.

6. NO DESCRIPTION, NO SUMMARY, NO COMMENTARY.
```

**[user]**

```
One screenshot at native capture resolution.
Reply with ONE JSON object and nothing else:
{"regions":[{"role":"<one of the roles above>","text":"<verbatim>"}]}
At most {max_regions} regions, in reading order. If nothing legible and meaningful is on
screen, return {"regions": []}.
```

### 5.3 Output contracts and the parse ladder

`clip-json-v1` and `ocr-json-v1` are JSON Schemas sent via `response_format: {"type":"json_schema"}` when `VIDEO_VLM_STRUCTURED` resolves to available (probed once per process, cached). **Guided decoding is the primary discipline lever, not an optimisation** — the POC measured the *same* rule block scoring 4.10 on Pro and **3.00 on Flash** (*"discipline rules wreck the weak model"*), and a self-hosted 32B is Flash-class. For a weak model, move discipline from a rule it can ignore into a constraint the sampler cannot violate. **The schema is inside `PACK_DIGEST`**, so a schema edit forks like a text edit.

The tolerant parser remains the contract of record, and is a pure function of the reply:

1. strip ``` fences and any prose before the first `{` / after the last `}`;
2. scan for the first **balanced** `{…}` (brace counter, string-aware) and `json.loads`;
3. one syntactic repair — trailing commas, smart quotes, unescaped newlines inside strings;
4. line mode (`App:` / `Activity:` / `Description:`);
5. whole-reply mode → `{"app":"", "activity":"", "description": <bounded reply>}`;
6. coerce, strip, collapse whitespace, **strip all newlines** (D-12), truncate at budget.

Every fallback increments `dp_video_parse_fallback_total{pack,step}`; `finish_reason == "length"` increments `dp_video_truncated_total{pass}`. An empty reply raises (chunk not marked done → at-least-once redelivery), matching `vlm.py:119-121`. **No repair call** — a retry costs a full inference; a 0.5 % fallback rate costs nothing and a 10 % rate is a prompt bug the offline harness must have caught.

---

## 6. Contract impact

### 6.1 Legal today — zero schema edits, zero ratification

| element | deciding line | status |
|---|---|---|
| one `kind='caption'`, `discriminator=""` | `pipeline.py:43-45` folds the discriminator only when non-empty → the canonical v0 two-component id | LEGAL |
| one `kind='ocr'`, `discriminator="ocr"` | `c2:34` — `ocr` is **in the frozen enum**; `c2:12` *specifies* the discriminator mechanism; already emitted at `captions.py:136-145`; already rendered at `daylog.py:117-118` → `:164`; `storage/app/models.py:19` `ContentKind` already contains `ocr` | LEGAL, already shipped |
| both records span the C1 span verbatim | `pipeline.py:79-80` substitutes per-unit spans; passing `None` carries the C1 strings | LEGAL, and strictly safer than today |
| `content.text = ""` on a no-text chunk | `c2:35` is a bare string, no `minLength`; verified at all four gates | LEGAL |
| relative offsets + region-role words inside `content.text` | `c2:32` `required:["kind","text"]`, text unconstrained | LEGAL |
| 58-char `pipeline_version` with `@ # + . -` | **`c2:66` has no `minLength`, `maxLength`, `pattern` or `enum`**; `TEXT` in `db.py:84` and `journal.py:72`; hashed at `pipeline.py:41-42` | LEGAL |
| `enrichments` = four empty arrays | `processing/base.py:32-34` unchanged | LEGAL |

All four gates pass unmodified: DP schema (`ingest_core.py:167`), DP pydantic (`extra="forbid"`), storage schema (`storage/app/main.py:152-157`), storage pydantic (`storage/app/models.py:117-130`).

### 6.2 Additive edit — written, **parked**, not taken

`enrichments.text_regions[]`. Insert into `enrichments.properties` after `c2:62`, leaving `required` at `c2:57` **untouched** (adding an optional property widens the accepted set; adding to `required` narrows it and breaks every stored record):

```json
"text_regions": {
  "type": "array",
  "items": {
    "type": "object", "additionalProperties": false,
    "required": ["text", "bbox"],
    "properties": {
      "text":       { "type": "string" },
      "role":       { "type": "string", "description": "Coarse named region (titlebar|tab|sidebar|main|compose|…)." },
      "bbox":       { "type": "array", "items": {"type":"number"}, "minItems": 4, "maxItems": 4,
                      "description": "[x,y,w,h] NORMALIZED to [0,1] against the frame the OCR pass read — never pixels, so a VIDEO_OCR_FRAME_WIDTH change does not invalidate stored geometry." },
      "confidence": { "type": "number", "minimum": 0, "maximum": 1 },
      "t_start":    { "type": "string" }, "t_end": { "type": "string" }
    }
  },
  "description": "OCR-specialist output with frame location (CHARTER OQ14b). Optional — omitted by every pre-OCR record, so this widening is additive."
}
```

**Four edit sites, and the mirror requirement is asymmetric in BOTH directions — this is the footgun:**

1. `product/contracts/c2_processed_record.v0.json`
2. `product/ARCHITECTURE.md:152-160` (the C2 prose shape) — **edited first**, per `ORG.md:44-45`
3. `product/services/storage/app/models.py:108-114` — `Enrichments` declares its four fields with **no defaults**
4. `product/services/data-processing/app/models.py` — `C2Enrichments` uses `default_factory=list`

Measured: **without the field declared, NEW records fail at *both* pydantic mirrors** (`extra_forbidden`) — DP's `default_factory` does nothing for an *undeclared* key. **Declared without a default, PRE-EXISTING records fail at the storage mirror** (missing). Both mirrors need `text_regions: list[Any] = Field(default_factory=list)`, and the deploy order is: mirrors first, then contract JSON, then DP emits. A mirror lag is an **uncaught `ValidationError` → HTTP 500** from `storage/app/main.py:159`, which DP classifies as **transient** → 3 retries → dead-letter → recording reads it as permanent data loss.

**Trigger to spend it:** the first real geometry consumer. Pay it down in **one** freeze-additive commit together with root `quality{}` (CHARTER `:119`).

### 6.3 Explicitly rejected

| tempting | why not |
|---|---|
| new `content.kind` (`ui_event`) | schema-additive, **operationally breaking**, and the danger is inverted: the two services that *validate* fail loudly and are one-`Literal` fixes (`dp/models.py:69`, `storage/models.py:19`), while **continuum validates nothing** and `daylog.py:119-120`'s `else` renders an unknown kind as a **caption** — silent, permanent mislabelling in the one service that trains on it. Buys nothing `kind='ocr'` does not |
| `content.segments[]` on an ocr record | legal by the letter; ruled against — `c2:42` forces a lying `speaker: null` on thousands of records/day (destroying the `c2:47` promise that the key's presence is meaningful), `c2:41` `additionalProperties:false` on the item **structurally forbids `bbox`** so it ships half the feature and burns the honest option, and `daylog.py:94` discards it for every kind but `transcript` |
| `content.regions[]` | `c2:31` closes `content`, and `content` is the training target — coordinates rendered into a training target are noise. `c2:64` reserves `enrichments` for growth |
| `enrichments.objects[]` squat | legal today (`c2:62` `items:{}` is the empty schema, verified at all four gates), and declined: it would accumulate an undocumented shape at scale in `/context` for a value nothing reads |
| record span ⊃ chunk span | schema-silent, and wrong: `c2:22` makes provenance a false claim, and `daylog.py:110-114` never reads `t_end` |
| `source.window` / multi-chunk records | `c2:17` requires a singular `chunk_id`; architecturally blocked regardless (D-01) |
| a separate `prompt_version` C2 field | root `c2:7` is closed, **and** it would let prompt identity drift *out of* `record_id` — the exact bug class `stage.py:13-24` made structural |

### 6.4 Cross-service — all in §10, none silently in the plan

recording `--segment-seconds`; storage `DELETE /context/records`; continuum renderer + recipe; inference/platform serving flags and GPU allocation.

---

## 7. Numbers

### 7.1 Stated assumptions (each with its verification)

| assumption | verification |
|---|---|
| Qwen3-VL: patch 16, spatial merge 2 → factor **32**, `tokens = ⌈H/32⌉·⌈W/32⌉`. 768×480 = 24×15 = **360 tok/frame** | one curl with a known-size image; read `usage.prompt_tokens`. If the deployed `qwen-vl-utils` defaults `image_patch_size=14` (factor 28) every vision figure inflates **+31 %** |
| The server does **not** silently downscale. `--mm-processor-kwargs` is unset in `serve_vllm.sh` | same curl: `usage.prompt_tokens == 360` for one 768×480 frame. If materially lower, `max_pixels` is clamping and the flag is mandatory, not optional |
| prefill **12,000 tok/s**, aggregate decode **2,000 tok/s**, single-stream decode **45 tok/s** (Qwen3-VL-32B, TP=8) | `vllm bench serve` at target concurrency, 30 min. **Ratios in the table are robust to this; absolute dollars are not** |
| node price **$16/node-hour** (8×H100 @ $2/GPU-h) | procurement. Public list for an a3-mega shape is materially higher; a second column at **$60/node-hour** is shown |
| PP-OCRv6 CPU **0.6 s per 1728×1080 frame**, 4 threads | run over 200 real extracted frames and `time` it (20 min) |
| **40 % of screen chunks are idle** | run the delta gate offline over one real screen-hour and count (10 min). Everything below scales linearly; the design does not depend on it |

### 7.2 Per chunk

| | today (10 s, 4 kf) | **design @10 s** | **design @60 s** |
|---|---:|---:|---:|
| K frames to VLM | 4 (in 4 calls) | 4 active / 2 idle | 12 active / 3 idle |
| VLM calls | **4** | **1** | **1** |
| prefill tok (weighted 60/40) | 1,968 | **1,517** | **3,512** |
| output tok (weighted) | 440 | **60** | **210** |
| OCR frames (CPU) | 0 | ~0.6 | ~3 |
| C2 records | **4** | **2** | **2** |
| ffmpeg subprocesses | **6** | **2** | **2** |
| video decoded | 24.8 s (2.5×) | ~10 s (1.0×) | ~60 s (1.0×) |

*Working, 60 s active:* `12 × 360 = 4,320` vision + `12 × 2 = 24` delimiters + system 240 + task 125 + injected OCR 200 + ChatML 25 = **4,934**. Idle: `3 × 360 + 6 + 240 + 60 + 25 = 1,411`. Weighted `0.6 × 4,934 + 0.4 × 1,411 = 3,512`.
Output: budget `16 × 60 = 960` chars ≈ 260 tok + 30 JSON = 290 active; 90 idle → `0.6 × 290 + 0.4 × 90 = 210`.

### 7.3 Per screen-hour and per 8 h day

| | today | design @10 s | **design @60 s** |
|---|---:|---:|---:|
| chunks / hour | 360 | 360 | **60** |
| VLM calls / hour | 1,440 | 360 | **60** |
| prefill tok / hour | 708,480 | 546,120 | **210,720** |
| output tok / hour | 158,400 | 21,600 | **12,600** |
| **node-seconds / screen-hour** | **138.2** | **56.3** | **23.8** |
| **$ / screen-hour @ $16/node-h** | **$0.614** | **$0.250** | **$0.106** |
| $ / screen-hour @ $60/node-h | $2.30 | $0.94 | **$0.40** |
| **node share, one continuous user** | **3.84 %** | 1.56 % | **0.66 %** |
| C2 video records / 8 h day | 11,520 | 5,760 | **960** |
| stored video JSON / 8 h day | 9.55 MB | 4.26 MB | **1.24 MB** |
| ffmpeg subprocesses / 8 h day | 17,280 | 5,760 | **960** |
| CPU OCR | — | 0.036 core | **0.03 core** |
| per pilot-user-year (250 d) | 2.88 M rows / 2.39 GB | 1.44 M / 1.07 GB | **240 K rows / 0.31 GB** |

`node-seconds = prefill/12,000 + output/2,000`. **@60 s: 210,720/12,000 = 17.6 + 12,600/2,000 = 6.3 = 23.9.**

**Headline for the founder — capacity, not $/user-day.** One continuously-recording screen user consumes **0.66 % of one 8×H100 node** at the 60 s point, versus **3.84 %** today. At 20 pilot users that is 13 % of the node, leaving the interactive tenant intact. The $/user-day figure is only meaningful at saturation, which is a year-two number.

**The OCR cost comparison, stated once:** CPU OCR = **$0.0012–0.007 per screen-hour**. The same reads through the 32B VLM = **$0.30–0.40 per screen-hour**, i.e. **164×**, i.e. **3.1× the entire caption pipeline it augments**, to obtain the field the POC named the #1 hallucination class from the model family it measured at 0.143.

### 7.4 Latency

| step | @10 s | @60 s |
|---|---:|---:|
| blob GET + sha256 (off-loop) | 0.05 s | 0.10 s |
| ffmpeg pass A (delta + PTS) | 0.24 s | 1.40 s |
| Python delta/anchor | 0.03 s | 0.15 s |
| ffmpeg pass B (extract, both widths) | 0.27 s | 1.60 s |
| CPU OCR (serial with clipcap by design) | 0.36 s | 1.80 s |
| VLM prefill | 0.13 s | 0.41 s |
| VLM decode (single-stream 45 tok/s) | 1.64 s | 6.44 s |
| 2 × `/context` POST | 0.04 s | 0.04 s |
| **total** | **~2.8 s** | **~12.0 s** |
| **duty cycle** | **28 %** | **20 %** |

Threadpool: `clipprep` + `screentext` are `run_sync` (2 anyio tokens), `clipcap` is `run_async` (0). With `INGEST_MODALITY_LIMITS=video=3` the worst case is **6 of anyio's 40** tokens, versus today's `4 workers × 8 keyframes = 32`, which can starve the durable-journal writes at `ingest_core.py:189-192`.

### 7.5 Training dose

Already in D-11. Recommended production: `segment_seconds=60, block_segments=2` → **3,290 chars/block, dose 15.1×, 45 % headroom under `EXCERPT_CHARS`**. `block_segments=1` → **30.2×** at 2× continuum's amplification generations (480 blocks/day vs 240).

---

## 8. Risks & accepted caveats

**Every adversarial finding is listed. Fixed items name the decision; accepted items say so.**

### Fixed in the design

| # | finding | where fixed |
|---|---|---|
| 1 | unknown `VIDEO_BACKEND`/`VIDEO_PIPELINE` disables both graphs → 500 on every video chunk | D-14: single resolver, unknown → `keyframe` |
| 2 | top-k-by-MAD OCR selection is not stable across builds → record substitution | D-07: rank-free even-spaced cap |
| 3 | sidecar fragment re-keys the caption for a change that did not touch it | D-08/§4.3 R1: OCR **is** an input to the caption, so the fork is correct, not accidental. Documented as a deliberate cost |
| 4 | `N` clamped by ffprobe duration → decoder-dependent grid and discriminators | D-04: ffprobe deleted, grid from the declared C1 span. D-05: discriminators fixed |
| 5 | mock deriving text from JPEG byte length → CI text varies by ffmpeg build | mock text from `(n_frames, span_seconds, chunk_id)` only |
| 6 | content-thresholded gate makes the **record set** nondeterministic | D-05: set is fixed at 2 |
| 7 | `ocr_url` in the config signature → DNS change forks the corpus | D-13: urls/keys/timeouts in `OPERATIONAL_ONLY` |
| 8 | floor-grid `t_prev` at k=0 unspecified → two legal implementations, two record sets | D-07: convention pinned and tested at a `t_start` that is an exact multiple of `FLOOR_S` |
| 9 | rolling deploy mixes dialects in one training window | D-14: drain-and-replace mandated; `dp_pipeline_dialect` gauge + alert |
| 10 | `Z` vs `+00:00` timestamp form vs storage's lexicographic window compare → boundary records silently dropped | D-05: C1 strings carried verbatim; `abs_time` never called |
| 11 | additive `enrichments` rejected by **both** mirrors, and the failure direction was stated inverted | §6.2: both mirrors, both directions, deploy order |
| 12 | `INGEST_ASYNC=0` makes every concurrency knob inert and inverts the timeout hierarchy | §8 "Required production configuration" below — stated as a precondition |
| 13 | `VIDEO_VLM_TIMEOUT=120` > `RECORDING_HTTP_TIMEOUT=30` | timeouts re-tuned below |
| 14 | queue horizon ~21 min against a multi-hour outage | `INGEST_QUEUE_MAX=4096` |
| 15 | metrics dark under `INGEST_ISOLATION=subprocess`; wrong metric name cited (`dp_stage_skipped_total` does not exist) | §8 observability: load-bearing counters emitted in the **parent**; correct name is `dp_graph_stage_failures_total{modality,stage,reason}` |
| 16 | TOCTOU between accept-time prompt hash and run-time prompt text | D-13: load once per process, no mtime cache |
| 17 | model id, token budgets, stamp mode, region flag all outside the fragment | D-13: `cfg_tag` + the completeness test |
| 18 | OCR channel 100 % truncated by `EXCERPT_CHARS` because it renders last | D-11 budget (+ E-4 as belt-and-braces) |
| 19 | redundancy relocated into mandated interval lines | D-10: no interval lines |
| 20 | low-res captioner instructed to name specifics the high-res OCR pass is reading | D-09: injection + the ungrounded-quote counter |
| 21 | forced `activity` + line-count floor manufactures activity on a static screen | D-10 + `activity: "unclear"` + `screen-clip-idle-v1` |
| 22 | chars-per-second formula contradicted its own knob by 9× | D-11: per-block-per-second-of-life, derived caps |
| 23 | multi-line `content.text` breaks the labelled-line contract | D-12 |
| 24 | batched `select=` extraction silently emits fewer files (measured: 4 requested, 3 written, exit 0) | D-04: `-frame_pts 1`, map by value, assert count |
| 25 | `fps=` output frames carry slot PTS, not content PTS (measured ~1 s error) | D-04: `select=` + `-fps_mode passthrough` + `showinfo` |
| 26 | whole-frame mean difference is blind to typing at every resolution | D-04: binarize at full res, then area-average, then **max** |
| 27 | `best_effort` + a fragment stamps a dialect claiming a property the record set may lack | §4.3 R3(b); `screentext` is `required` |
| 28 | ffmpeg-absent path returns success silently, poisoning a fleet | must **raise** under any non-mock backend, matching `keyframes.py:38-44` |
| 29 | pack-digest hashing raw file bytes forks on CRLF / trailing newline | D-13: hash the normalised parsed spec |
| 30 | mock stamps a prompt tag for a prompt it never reads | D-13: the backend module owns `prompt_tag`; mock returns `""` |

### Required production configuration (these are part of the design, not suggestions)

```
INGEST_ASYNC=1                 # PRECONDITION. Without it INGEST_WORKERS / MODALITY_LIMITS /
                               # QUEUE_MAX are all inert and inline mode has zero retries.
INGEST_WORKERS=6
INGEST_MODALITY_LIMITS=video=3,audio=2
INGEST_QUEUE_MAX=4096          # ~5.7 h of buffer at 0.2 chunk/s, vs 21 min at the default 256
INGEST_MAX_RETRIES=3           # do not raise; a retry re-pays the whole chunk's GPU
INGEST_DRAIN_TIMEOUT=120       # a 60 s chunk takes ~12 s; 30 s cancels mid-flight work
VIDEO_CLIP_TIMEOUT=25          # < RECORDING_HTTP_TIMEOUT=30
VIDEO_OCR_TIMEOUT=15
VERIFY_BLOB_SHA256=1           # but MOVE the hash off the event loop (ingest_core.py:105-107)
VIDEO_PROMPT_DIR=''            # packaged prompts in production
```

Plus a **VLM circuit breaker**: N consecutive connect-refused → fast-fail *before* the ffmpeg passes, so an endpoint outage costs ~0 CPU per chunk instead of the full prep, and stop consuming the retry budget.

### Observability (must ship with the build)

**Parent-side** (`ingest_core.py`, survives subprocess isolation, `metrics` already in scope and null-guarded): `dp_units_total{modality,kind}`, `dp_content_chars{modality,kind}` (histogram), `dp_empty_output_total{modality,kind}` (drop the `modality == "audio"` guard at `ingest_core.py:157`), `dp_partial_write_total{modality}`.
**Stage-side** (blind under isolation — documented): `dp_video_parse_fallback_total{pack,step}`, `dp_video_truncated_total{pass}`, `dp_video_delta_peak` (histogram — validates the whole idle assumption from day one), `dp_video_ocr_events`, `dp_caption_ungrounded_quote_total`, `dp_ocr_redactions_total`, `dp_video_scenario_mismatch_total{expected,seen}`, `dp_ocr_frame_errors_total`.
**Dialect**: `video_pipeline_version` in `/health`; gauge `dp_pipeline_dialect{modality,pipeline_version}=1` via the existing `add_gauge_source` (`main.py:106-124`); **alert on `count by (modality) (dp_pipeline_dialect) > 1`**.

### Accepted caveats

**A-1 — Determinism has an honest limit, and it is smaller than today's but not zero.** DP guarantees a deterministic record **set** (count, ids, spans) as a pure function of `(chunk_id, settings)`. Record **text** is deterministic given `(bytes, settings, pinned ffmpeg build, pinned OCR model files)` for the OCR record, and additionally given the served model's batch composition for the caption — `temperature: 0` does **not** make a batched vLLM server bit-exact. Delete the overclaim at `vlm.py:16-19`. State the invariant in those words in the handoff.

**A-2 — ffmpeg is NOT in the fragment.** The judges' graft asked for `+ff:<major.minor>`. Declined, because D-05 changed the calculus: with a fixed record set, a decoder difference can only alter record *text*, never identity, so a fragment would fork the entire corpus on every base-image bump for a change that usually alters nothing. Instead: `VIDEO_FFMPEG_PIN` asserted at startup (fail loud on drift), reported in `/health`, and a deliberate upgrade that changes output is a manual `PACK_VERSION` bump. **Residual: two workers on different ffmpeg builds can write different OCR/caption text under the same `record_id`.** Bounded by the pin.

**A-3 — Rollback restores behaviour, not the corpus.** New-dialect records persist. There is no operation in the system that removes them until storage ships E-2.

**A-4 — Write atomicity is at assembly, not at the wire.** `ingest_core.py:164-187` posts units sequentially; a failure on unit 2 leaves unit 1 durably written. A caption with no OCR record is byte-indistinguishable from a screen with no text — **which is exactly the inference §4.3 R3(e) forbids continuum from making.** `dp_partial_write_total` is the only signal. Fixing it properly needs a storage batch-write endpoint; filed, not claimed.

**A-5 — Cross-chunk OCR repetition is unfixable inside DP.** Dedup is within-chunk only, because cross-chunk state would break the fleet-determinism promise. A static screen still emits one OCR read per `VIDEO_OCR_FLOOR_S`. At 60 s chunks + `FLOOR_S=120` that is 30 reads/hour instead of 60. The continuum-side dedup line (E-4) removes the rest. **This is a direct consequence of the idempotency contract and is worth the trade.**

**A-6 — Goal 3 is delivered to ±1 minute today, to ±1 second after E-4.** DP emits **relative** offsets (`+11s`) inside `content.text`, because C1 carries no timezone (verified: only optional `device_location{lat,lon}` and `device_clock`) and rendering UTC under a local-time block anchor would train two clocks four hours apart — the exact hazard `daylog.py:145-148`'s own docstring warns about. `VIDEO_OCR_STAMP=utc` exists as an escape hatch, forks the dialect, and is recommended against.

**A-7 — A storage fault re-pays the GPU.** A `/context` blip on the second record retries the whole chunk, recomputing the VLM call. Upserts are idempotent so correctness is fine; GPU spend has a tail driven by storage availability. Keep `INGEST_MAX_RETRIES` at 3 and `DP_HTTP_TIMEOUT` generous — a short storage timeout is a GPU-cost amplifier here, not a safety feature.

**A-8 — Dose is a bet on an unresolved experiment.** The Phase-3 decomposition fork (`phase-3-report.md:372-390`) has **not run**, and the report says *"Do not start the follow-on until that one lands."* Until it does we do not know whether the `Scene:`-concatenating renderer itself costs recall, or only the character dose. This design is the right bet under **both** branches — collapsing N near-duplicate captions into one description reduces the denominator *and* removes the concatenation — but 15.1× is a target, not a result.

**A-9 — Latency roughly triples per chunk**, because one long generation replaces four short parallel ones, and because `screentext` serialises before `clipcap` by design (D-09). ~2.8 s @10 s / ~12.0 s @60 s. Fine for async ingest, where the ratified invariant is only `dp_acked=1 ⇔ C2 durably written` and says nothing about speed. The exposed edge is **C8 interactive** (`CHARTER.md:34,55,65`), where ~12 s is not an interactive answer. The charter already permits the mitigation (OQ2, *"a lighter captioning profile — same code, config-only difference"*): `VIDEO_PIPELINE=clip` + `VIDEO_CLIP_MAX_FRAMES=2` + `screen-clip-idle-v1` + `VIDEO_OCR_BACKEND=off` → ~2 s. It **forks `pipeline_version`** via `cfg_tag`, which is correct and version-forward, and must be stated when M6 lands rather than discovered. The charter's own mitigation — *"contract test diffs both paths on shared fixtures"* — stays satisfiable because both profiles run the **same stages, same assemble, same `build_c2`**; only config differs.

**A-10 — `screentext` is `required`, so an OCR-sidecar outage fails video chunks.** Deliberate: a skipped OCR silently changes the caption (it is an input), which is the "a lost mutation would be a silent lie" class that `stage.py:221-225` forbids by construction for mutates. Mitigations that make it affordable: the sidecar is loopback CPU with **no shared failure domain with the GPU**; per-frame errors are absorbed and counted, with only >50 % of frames failing raising; the circuit breaker fast-fails before the ffmpeg passes. **The honest escape is `VIDEO_OCR_BACKEND=off`** — which changes `cfg_tag` and therefore says so in the dialect — **not** a policy flip to `best_effort`, which would keep the claim and drop the data.

**A-11 — The `acoustic` sidecar has the same latent hole.** By §4.3 R1, flipping `ACOUSTIC_BACKEND` rewrites the same `record_id`. Not fixed here — audio's owner, retro-fit `+ac-<backend>-v1` when a real backend lands. Noted so it is not silently inherited.

**A-12 — `injected_caption` emits per-unit sub-spans via `abs_time`**, so it *is* exposed to the `Z` vs `+00:00` boundary defect (A-fixed-10) that this design avoids structurally. Flagged to the audio/replay owner; not fixed here.

**A-13 — Recording's `failed` state is terminal.** A 503 is recoverable (recording retries from its durable ledger); a `failed` segment needs a manual `POST /capture/sessions/{id}/retry` (`emitter.py:116-120,222-226`). Recording's retry budget is 4 attempts × 0.25 s backoff = **1.5 s**, which is not a backpressure policy against a GPU stall. Escalated as E-6; until then the queue depth is the only defence and `INGEST_QUEUE_MAX=4096` is what makes it one.

**A-14 — Region roles are model-/engine-asserted, not verified.** `titlebar`/`compose`/`statusbar` come from bbox position heuristics over the OCR engine's output. Good enough to be the semantic 80 % of "location"; not a substitute for `text_regions[]` when a consumer exists.

**A-15 — Every knob in `OUTPUT_AFFECTING` forks the corpus.** That is correct and it is also a cost: an operator nudging `VIDEO_CLIP_FRAME_WIDTH` from 768 to 1024 mints a new dialect for that day. The resolved config tuple is logged at graph resolution so `cfg_tag` is greppable back to its inputs, and "these knobs fork the corpus" sits beside the table in the handoff.

**A-16 — Two cost blowups are one keystroke away.** `VIDEO_CLIP_FRAME_WIDTH` 768→1280 is **2.78×** the vision tokens on every call (clamped at 1024 with a WARN). `VIDEO_OCR_MAX_EVENTS` 3→8 is ~2.7× the CPU OCR and, with `VIDEO_OCR_BACKEND=vlm`, a 3.8× jump in daily prefill. The token arithmetic goes in each knob's docstring so the price is read before the dial is turned.

---

## 9. Open questions

**O-1 — Which change metric, at which threshold?** Two measured candidates: full-res binarize + 32×32 area-average (floor exactly 2, typing 11–19, scroll 61–122, switch 255) versus anchor-referenced 16×10 tile MAD at 256×160 (caret 1.24, typing 5.68→7.73, scroll 28, switch 239). §D-04 ships the former plus a Python anchor accumulator, which is a synthesis, not a measured configuration.
**Recommendation:** ship as specified; run `scripts/calibrate_delta.py` over **one real captured hour** before the pilot and pin the thresholds from the observed histogram, not from the synthetic clips.
**Evidence that settles it:** the `dp_video_delta_peak` histogram from a real desktop, hand-labelled into idle / typing / scroll / switch windows. A real desktop has notification badges, animated cursors, a ticking menu-bar clock — none of which are in the synthetic fixtures, and any of which could raise the idle floor above 8.

**O-2 — Can PP-OCRv6 (or any current OCR model) read 13 pt macOS UI text at 1728 px through CRF 28?** Every candidate is trained on *documents*; a dark-theme code editor with sub-pixel antialiasing and x264 ringing is out of distribution. The vendor "Screen 82.5" benchmark is the vendor's own test set with chat models as the comparison row.
**Recommendation:** gate the pilot on it. Ship `VIDEO_OCR_BACKEND=mock` until it passes.
**Evidence:** 200 hand-labelled real macOS frames (IDE, Gmail, Slack, browser article, terminal, spreadsheet), scored on exact-string recall of ≥5-char strings **with lenient substring matching, not exact equality** — the POC measured 0.000 strict on a model that was reading correctly and wrapping the answer in prose. Run `{ppocr@1728, ppocr@1152, Qwen3-VL-32B@1536, Qwen2.5-VL-32B@1536}`. ~1 day, no new code. Gate: ≥0.85 key-string recall, ≤0.10 CER on the focused region.

**O-3 — Should the scenario be per-chunk rather than per-deployment?** D-13 chose per-deployment because per-chunk needs three shared-core edits and breaks `_current_pv`'s durable dedup backstop.
**Recommendation:** stay per-deployment. Revisit only when a single fleet must serve two capture surfaces from one DP instance.
**Evidence:** a non-zero `dp_video_scenario_mismatch_total` in production. If it stays zero, the question is answered.

**O-4 — Does a 32B write a *better* clip description than four keyframe captions?** There is **no per-frame vs per-clip A/B on the same model anywhere in the repo** — the POC's frames-only runs were a different model (Claude) or confounded with audio removal. Everything else in this design is measured; this is the one premise that is argued.
**Recommendation:** run it before the flip, and make the terse-vs-dense prompt an arm of the same run.
**Evidence:** 30 real clips × {`per-frame-v0`, `screen-clip-v1`}, judged blind with the POC's frame-grounded rubric on (cross-frame reasoning present / invented detail / usable as a day-log line), plus the $70 Gemini oracle as an upper bound. Two files in the pack registry; the fork is automatic and the two arms cannot collide.

**O-5 — Is 15.1× the right dose, or is the renderer the problem?** A-8. **Recommendation:** ship `R=22` and `block_segments=2`; request `block_segments=1` (dose 30.2×) if continuum can afford 2× the amplification generations.
**Evidence:** the Phase-3 decomposition fork, which is continuum's to run and which their own report says must land before any follow-on.

**O-6 — What is the real node price and the real serving throughput?** Every dollar figure in §7 is an input-times-assumption. The **ratios** (5.8× cheaper at 60 s, 164× for CPU-vs-VLM OCR) are ratios of token counts and are robust; the absolutes are not.
**Evidence:** `vllm bench serve` at target concurrency (30 min) plus a procurement answer on the a3-mega rate.

**O-7 — Does the sidecar's `/health` model pin belong in `cfg_tag` or only in the assertion?** Currently both (`ocr_model_sha_det/rec` are in `OUTPUT_AFFECTING`). This means a model-file upgrade forks the corpus — correct, but it also means the pin must be updated in DP's config, not just in the sidecar. **Recommendation:** keep it; the alternative is a silent output change. Revisit if model updates become frequent.

---

## 10. ESCALATIONS

Each is a crisp ask. **None blocks a build; two block the cutover.**

**E-1 — recording: `--segment-seconds 10 → 60` for the mac capture.**
*Ask:* change the default at `clients/mac/nucleus_capture.py:647`.
*Why:* the single largest lever in the system — **5.8× on GPU cost**, 12× fewer records, and it is the only route to a window over which a description can reason. **Zero contract surface**: C1 places no constraint on chunk duration (`c1:19-20` are bare strings; only `sequence` density is constrained at `c1:15`).
*Cost recording bears:* a lost or dead-lettered chunk now loses 60 s of a user's day, not 10 s; upload latency per unit rises ~6×; the retry blast radius rises 6×. **And it moves the audio stream too** — recording demuxes both legs from the same segment — which is probably *better* for ASR (6× fewer boundary-truncated words; the POC transcribed 20-minute windows) but is the audio owner's call, not ours.
*If refused:* DP ships at 10 s. Everything works; the dose is identical by construction (D-11); the cost is 2.4× rather than 5.8× better than today; the caption cannot reason across a task-step.
*Owners:* recording + data-processing (audio). A joint note in both HANDOFF canvases per `ORG.md:44-45`. **Not a founders' escalation** — it is config with no contract surface.

**E-2 — storage: `DELETE /context/records?user_id=&from=&to=&pipeline_version=&kind=`.**
*Ask:* a retraction primitive.
*Why:* **this blocks the cutover, not the build.** `record_id` forks by design (`pipeline.py:12-14`), old records persist (`journal.py:296-298`), and `daylog.py` filters on neither `kind` nor `pipeline_version` (`db.py:344-351`) — so any day re-consolidated across the cutover renders both dialects and double-counts. It is also required for right-to-be-forgotten and for the version-forward reprocess story `c2:66` already promises.
*Critical design note:* **the key must include `content.kind`.** Verified against `continuum/handoff/phase-3-report.md:132-135`: the Phase-3 replay emitted **13,020 records**, of which **12,221 captions + 621 transcripts** landed in-window (the difference is the out-of-window tail), **all** stamped the single dialect `asr-fw-v1+diar-pyannote-v1` — captions and transcripts sharing one `pipeline_version`, because `injected_caption` declares no fragment. A kind-blind retraction would delete transcripts to remove captions. Build it kind-aware or it ships unusable.
*Until it exists:* cut over **forward-only, at a UTC day boundary, on a fresh `user_id`**, with `DP_DIALECT_FREEZE=1` during the flip window. Never backfill.
*Owner:* storage.

**E-3 — inference/platform: serving flags and a dedicated captioner allocation.**
*Ask (a), immediate:* launch the served model with `--limit-mm-per-prompt '{"image":16}'` **and** an explicit `--mm-processor-kwargs` `max_pixels` ≥ the frames we send. `serve_vllm.sh:52-55` states verbatim that these are *"intentionally omitted"*. Without (a) the multi-image call 400s on the first chunk — loud, and the fallback pack covers it. Without the second flag the server may **silently downscale**, discarding the pixels we are paying for, with no error and no metric.
*Ask (b), before scale-up:* a captioner endpoint **distinct from `:8000`**. Verified: `VIDEO_VLM_URL` defaults to `127.0.0.1:8000` and inference's `VLLM_URL` defaults to `localhost:8000` — the same single Qwen3-VL-32B TP=8 instance on node-7 at `gpu_memory_utilization=0.90` that serves user-facing chat. DP's prefill bursts land in the same continuous batch as the assistant's decode steps, and the failure mode is TTFT on the assistant, which no GPU-percent figure shows. Worse: `product/HANDOFF.md:262-268` records the serve loop as routinely **down** during the learn loop, and `platform/CHARTER.md:73,83` lists the serving-vs-training allocation policy as an unresolved **proposal** (this is DP's `CHARTER.md:96` OQ3, unchanged). During a 4 h nightly window at 60 chunks/hour, DP would dead-letter **240 chunks** — 4 hours of a user's screen life — after paying the full ffmpeg prep on each. A 7B-class VL model on 1–2 GPUs handles this load with room to spare and isolates DP from both tenants.
*Owner:* platform + inference; (b) is a founders' call because it closes CHARTER M0/OQ3.

**E-4 — continuum: three renderer changes and one recipe fork.**
*Ask (a), the goal-3 completion, ~5 lines:* in `_render_block` (`daylog.py:144-172`), carry each fragment's own `t_start` (already stored for ASR at `:107/:116`, never rendered at `:153-154`) and prefix it in **`win.tz`**, the timezone continuum already holds at `:147`. Today the only clock a trainer ever sees is the block anchor at `%H:%M` from bucket boundaries, so *"at 13:04 the user was writing an email saying X"* is structurally unreachable no matter what DP emits. DP cannot do this itself — C1 carries no timezone.
*Ask (b), ~2 lines:* drop an `ocr` string equal to the previous one in the block and suffix `(unchanged ×N)`. Removes A-5's residual and fixes today's path too.
*Ask (c), ~2 lines, belt-and-braces:* emit `World text (OCR):` **before** `Scene:`, or interleave per segment. Truncation is ordinal, and OCR is currently last; D-11's budget keeps us clear, but a long `Heard:` line is the one thing that can still eat it.
*Ask (d), the recipe:* fork `recipe_id` to `segment_seconds=60, block_segments=2` (dose 15.1×), or `block_segments=1` (dose 30.2×) at 2× the amplification generations — 23,040/day vs 11,520. Their trade, their GPU. Precedent: Phase 3 already forked the recipe to match a caption cadence (`consolidation-test-1min-v1.0.json`) rather than bending records to the recipe.
*Owner:* continuum. (a)–(c) fork `recipe_id` and re-run the parity ensemble per `speed.py:4-8`; (d) is a recipe change by definition.

**E-5 — founders + storage: the parked additive C2 edit.**
*Ask:* nothing yet. `enrichments.text_regions[]` + root `quality{}`, in **one** freeze-additive commit, when the first geometry or quality-gating consumer exists. The exact diff, the four edit sites and the asymmetric mirror footgun are in §6.2 so the ratification session gets a decision, not a project. This cashes CHARTER OQ14b and `CHARTER.md:119` together.
*Owner:* founders' session; edit `ARCHITECTURE.md` §Contracts first, then rows in the data-processing and storage canvases, per `ORG.md:44-45,88-90`.

**E-6 — recording: auto-retry `failed` segments.**
*Ask:* re-enqueue `state='failed'` on a timer, or widen `RECORDING_RETRY_ATTEMPTS`/`_BACKOFF` beyond the current 1.5 s total.
*Why:* a 503 from DP is a *recoverable* signal, but recording converts it into a terminal `failed` segment in 1.5 s (`clients.py:44-78`, `emitter.py:222-226`), recoverable only by a manual `POST /capture/sessions/{id}/retry`. Any GPU unavailability longer than the queue horizon becomes permanent capture loss requiring a human. This is not caused by this design, but this design is what makes GPU unavailability a routine, scheduled event (E-3).
*Owner:* recording.

---

## 11. Workstream split

Discipline: **one workstream, disjoint files, zero shared-core edits.** Two workstreams below do touch shared core; both are named, both own those files exclusively, and neither is on anyone else's critical path.

### The frozen interface, agreed here so everything parallelises from minute one

**LEAD CORRECTION (2026-07-24):** the shapes below do **NOT** go in `app/vision/result.py` — that
file already exists and the retained `VIDEO_PIPELINE=keyframe` legacy path (WS-G) imports
`Keyframe`/`KeyframeCaption` from it. The clip shapes live in a **new** file
**`app/vision/clip_types.py`**, and the single mode resolver in **`app/vision/mode.py`**. Both are
**committed by the lead to the fan-out base commit** (`app/vision/clip_types.py`,
`app/vision/mode.py`) — every workstream **imports** them; no branch re-creates them, so there is
no add/add merge to reconcile. The pinned shapes (authoritative copy is the committed file):

```python
# app/vision/clip_types.py  — import, do not redefine
@dataclass(frozen=True)
class Frame:        index: int; t_offset_s: float; jpeg_lo: bytes | None; jpeg_hi: bytes | None
@dataclass(frozen=True)
class DeltaCell:    peak: int; spread: int          # 0..255, 0..1024
@dataclass(frozen=True)
class Delta:        times: tuple[float,...]; cells: tuple[DeltaCell,...]; accum: tuple[int,...]
@dataclass(frozen=True)
class ClipFrames:   frames: tuple[Frame,...]; ocr_times: tuple[float,...]; idle: bool; span_s: float
@dataclass(frozen=True)
class OcrRegion:    text: str; role: str; bbox: tuple[float,float,float,float]; conf: float
@dataclass(frozen=True)
class OcrRead:      t_offset_s: float; regions: tuple[OcrRegion,...]
@dataclass(frozen=True)
class ClipDesc:     app: str; activity: str; description: str; sensitive: bool; raw: str; parsed: bool

# app/vision/mode.py  — import, do not redefine
def resolve_pipeline() -> str: ...   # VIDEO_PIPELINE -> "keyframe" (default/unknown) | "clip"
```

Slot contract: `clipprep` → `clip_frames: ClipFrames`, `delta: Delta`, `vision_settings`. `screentext` → `ocr_text: str` (the rendered single-line injection block). `clipcap` → `clip: ClipDesc`.

### Worker house rules (every WS-VC workstream — read before you touch a file)

A build session picking up any WS below follows these six rules. They exist because eight branches
touch one 173-test service.

1. **Own your files, and only yours.** Edit only the files your WS's *Files owned* list names. Never
   touch shared core you do not own: `app/main.py`, `app/ingest_core.py`, `app/pipeline.py`,
   `app/processing/base.py`, `app/stagegraph/**`, and `app/vision/config.py` (WS-D's alone). WS-F is
   the sole owner of `main.py`/`ingest_core.py`; WS-E2 is the sole (last) editor of
   `app/stagegraph/stage.py`.
2. **Ship disabled-by-default; keep the suite green on your branch alone.** Every new clip stage's
   `enabled()` returns `resolve_pipeline() == "clip"`, and the default is `keyframe`, so the legacy
   graph runs and your stages stay dormant under the existing fixtures. Before you call anything
   done, run `ASR_BACKEND=mock ./.venv/bin/python -m pytest -q` from
   `product/services/data-processing` — it must show **≥ 173 passed**. The full clip-mode E2E is an
   *integration* deliverable (the per-window consolidation tab), not yours — your unit tests drive
   your stage directly.
3. **Foundation files are committed — import, never redefine.** `app/vision/clip_types.py`
   (the frozen dataclasses) and `app/vision/mode.py` (`resolve_pipeline`) are already in the tree.
   Import from them. Do not edit or re-create them.
4. **Do not edit `HANDOFF.md` or `CHARTER.md`.** Append your notes under a new
   `## Build log — WS-X` section at the **end** of this file (`handoff/ws-video-clip.md`) only. The
   lead reconciles the service canvas and the CHARTER record-law extract at integration.
5. **Headless + offline in tests.** No GPU, no network, mock backends. Fixtures are generated at
   test time (e.g. `ffmpeg lavfi`); commit no binaries.
6. **Determinism is a contract.** Identical bytes + settings must yield an identical record *set*
   (count, discriminators, spans) and identical `record_id`s on any worker in the fleet — see §4 R4
   and §3 D-05. If a change could make the set depend on model output, decoder build, or which
   siblings survived a filter, it is wrong.

**Git:** one worktree + branch per WS (`svc/vc-ws-a` … `svc/vc-ws-h`), all based on the commit that
carries this file. A multi-tab WS (C, D) shares one worktree/branch across its tabs; the tabs own
disjoint files and a third consolidation tab closes the WS. Commit messages are clean — no
attribution, no mention of AI / Claude / Anthropic.

---

### WS-A — Wire probe & serving prerequisites · **START IMMEDIATELY** · blocked on nothing

**Scope.** Verify every external assumption before a line of app code is written, and hand the results to WS-D and WS-C.
**Files owned:** `scripts/vlm_probe.py`, `scripts/ocr_probe.py`, `handoff/ws-video-clip-probe.md`. No `app/` files.
**Deliverable.** Four curls and a capability report: (1) N `image_url` parts in one user message → is `--limit-mm-per-prompt` raised? (2) `response_format: {"type":"json_schema"}` → is guided decoding available? (3) `usage.prompt_tokens` for one 768×480 frame → **is it 360 (factor 32), 470 (factor 28), or materially lower (server-side `max_pixels` clamping)?** (4) `video_url` data-URI → informational only, for O-4's future.
**Depends on:** access to the served endpoint.
**Exit criteria.** The report exists, is committed, and names the exact launch flags required. If (1) fails, WS-D's default pack becomes `screen-clip-single-v1` and that is recorded in the report, not discovered in production.
**Escalation:** feeds E-3(a). **Not blocked by it** — the probe's job is to make the ask precise.

### WS-B — Frame prep & the delta gate · **START IMMEDIATELY** · blocked on nothing

**Scope.** D-03, D-04, D-07's selection half. The two ffmpeg passes, true-PTS parsing, the binarized 32×32 change map, the anchor accumulator, the OCR-frame selector, the deterministic frame grid, the `clipprep` stage.
**Files owned:** `app/vision/clip.py` (new), `app/vision/delta.py` (new), `app/vision/result.py` (new dataclasses), `app/stages/video/clipprep.py` (new), `scripts/calibrate_delta.py` (new), `tests/conftest_video.py` (ffmpeg-lavfi fixture builders), `tests/test_clipprep.py`, `tests/test_delta.py`.
**Depends on:** nothing. Fixtures are generated at test time by `ffmpeg lavfi` (six clips: flat black/white/gray, static+caret, typing at 40 wpm via 33 `drawtext` layers with `enable='between(t,a,b)'`, fast typing, scroll, dense scroll, app switch), built into `tmp_path` in ~4 s. **No binaries committed. No GPU. No network.**
**Exit criteria.**
- The measured floor is **exactly 2** on flat black, flat white and flat 50 % gray — asserted.
- The six calibration vectors of §D-04 reproduce.
- Two runs over the same fixture produce byte-identical `ClipFrames` and `Delta`.
- Requesting a frame index the stream does not contain **raises**, not silently drops (the measured `-frame_pts 1` guard).
- `ffprobe` appears nowhere; `scene` appears nowhere.
- ffmpeg-absent under a non-mock backend **raises**.
- `scripts/calibrate_delta.py` runs over a folder of clips and prints the peak/spread histogram plus the events/chunk each candidate threshold would yield.
**Blocked on escalation:** no. **Pinning the production thresholds is gated on O-1's real-capture hour, which is a config change after the build.**

### WS-C — OCR sidecar service & the DP seam · **START IMMEDIATELY** · soft-depends on WS-B's frozen dataclasses only

**Scope.** D-06, D-07's post-processing half, D-08's record. The new deployable, the backend seam, region-role assignment, dedup, redaction, budget, the `screentext` stage and its unit.
**Files owned:** `sidecars/ocr/**` (new deployable: `app.py`, `requirements.txt`, `run.sh`, `README.md`, its own venv), `app/vision/ocr/**` (new package: `__init__.py` with `_TAGS`/`_resolve`/`select`/`version_tag`, `mock.py`, `ppocr.py`, `vlm.py`, `assemble.py`, `redact.py`), `app/stages/video/screentext.py`, `tests/test_ocr_assemble.py`, `tests/test_screentext.py`, `tests/fixtures/ocr_truth/**` (30 hand-labelled real frames + JSON ground truth).
**Depends on:** WS-B's `Frame`/`ClipFrames` dataclasses (frozen above, so no wait) and `app/vision/budget.py` from WS-D (also frozen: `caption_cap(span, vs)`, `ocr_cap(span, vs)`, `truncate_sentence`, `truncate_word` — WS-C may stub it locally for a day if WS-D lags).
**Exit criteria.**
- `mock` backend needs no network, no GPU, no new DP dependency; the full DP suite is green with `VIDEO_OCR_BACKEND=mock`.
- `/health` returns both model-file sha256s + ORT version + EP; DP asserts them against config **at graph resolution** and fails loud on mismatch.
- Redaction: 6 synthetic cases (AWS key, `sk-`, `ghp_`, base64 blob, PEM header, Luhn card) → `[redacted:secret]`, counter incremented.
- Rendered text contains **no `\n`** — asserted.
- Exactly one `kind='ocr'` unit is emitted, always, with `discriminator="ocr"` and `t_start=None`; the unit exists with `content.text == ""` when nothing legible was found.
- Per-frame OCR error is absorbed and counted; >50 % of frames erroring raises.
- `VIDEO_OCR_BACKEND` unknown → `off` in **both** `select()` and `version_tag()` — asserted.
- **O-2's bake-off report is committed** (this is the workstream that produces it).
**Blocked on escalation:** no. **The production `ppocr` default is gated on O-2's result**, which is this workstream's own deliverable.

### WS-D — Prompt pack, config/version plumbing, and the clip primary · **START IMMEDIATELY (mock); real backend gated on WS-A**

**Scope.** D-02, D-09, D-10, D-11, D-12, D-13. The pack registry and digest, `cfg_tag` and the `OUTPUT_AFFECTING` classification, the budget module, the multi-image payload, the parse ladder, the deterministic renderer, the `clipcap` primary.
**Files owned:** `app/vision/prompts/**` (registry, `routes.json`, `LOCK.json`, `archive/`, `show.py`, `relock.py`, and the `.prompt.md` files: `screen-clip-v1`, `screen-clip-idle-v1`, `screen-clip-single-v1`, `screen-ocr-v1`, `camera-clip-v1`, `per-frame-v0`), `app/vision/budget.py`, `app/vision/version.py` (`cfg_tag`, `OUTPUT_AFFECTING`, `OPERATIONAL_ONLY`), `app/vision/clipcap/**` (`__init__.py` seam, `mock.py`, `vlm.py`, `vertex.py` stub), `app/vision/parse.py`, `app/vision/emit.py`, `app/vision/config.py`, `app/stages/video/clipcap.py`, `tests/test_prompt_pack.py`, `tests/test_budget.py`, `tests/test_parse.py`, `tests/test_clipcap.py`.
**Depends on:** WS-B's `ClipFrames`, WS-C's `ocr_text` slot (both frozen above). WS-A's report for the real-backend integration test only.
**Exit criteria.**
- `PACK_DIGEST` is stable across a whitespace-only edit and changes on any semantic edit — both asserted.
- Unknown `VIDEO_CLIP_PROMPT` resolves to the pinned default in **both** resolvers.
- `mock` backend's `prompt_tag` returns `""` — a prompt edit does **not** re-key the headless corpus.
- Mock text derives from `(n_frames, span_seconds, chunk_id)` only — **never from pixel bytes**.
- `set(OUTPUT_AFFECTING) | set(OPERATIONAL_ONLY) == set(VisionSettings.__dataclass_fields__)` — **this test fails until a newly added field is classified.**
- Editing one byte of a `.prompt.md` changes `pipeline_version` and therefore `record_id` — asserted end to end.
- Exactly one `kind='caption'` unit, `discriminator=""`, `t_start=None`; `c2["t_start"] == c1["t_start"]` **byte-for-byte**.
- `content.text` contains no `\n` and respects `caption_cap(span, vs)`.
- The parse ladder over 12 malformed replies (fenced, prose-prefixed, truncated mid-JSON, wrong keys, refusal, prompt echo, empty) — every fallback counted; empty raises.
- `python -m app.vision.prompts show` prints the exact wire text; `relock` bumps, rewrites and archives.
- The request carries K `image_url` parts with `Frame k (+t s):` labels interleaved and the task text **last**.
**Blocked on escalation:** the real-backend integration test is blocked on E-3(a). Every other exit criterion runs headless.

### WS-E — The emission law as an executable test · **START IMMEDIATELY** · zero shared-core edits

**Scope.** §4 as CI, not as prose.
**Files owned:** `tests/test_emission_law.py`, `docs/` note for the CHARTER.md extract.
**Content.** Over the live registry: every enabled `sidecar` with non-empty `provides` has a non-empty `version_fragment`, with a single named frozen exemption (`keyframes`); no `best_effort` stage carries a non-empty fragment; no `mutate` overrides `enabled()`; every stage's `writes ⊆ primary.mutable_slots`; the 18-row worked table's verdicts are encoded as assertions where mechanically checkable.
**Depends on:** WS-B/C/D's stage declarations existing (it can be written first and turn green as they land).
**Exit criteria.** Green, and it **fails** if a new stage violates R1.
**Follow-on (WS-E2, sequenced last, not parallel):** promote the rule to a registration-time `StageRegistrationError` in `app/stagegraph/stage.py` — ~8 lines plus the frozen exemption set. **This is the one sanctioned shared-core edit and it deliberately lands after everything else, so it can never block a build.**

### WS-F — Observability, failure semantics, dialect visibility · **START IMMEDIATELY** · owns two shared-core files exclusively

**Scope.** §8's metric list, the timeout/queue re-tune, the circuit breaker, `DP_DIALECT_FREEZE`, the sha256 move.
**Files owned:** `app/main.py`, `app/ingest_core.py`, `app/vision/circuit.py` (new), `tests/test_metrics_video.py`. **No other workstream may edit `main.py` or `ingest_core.py`.**
**Changes.** Declare the new families; add the parent-side `dp_units_total{modality,kind}` / `dp_content_chars` / `dp_empty_output_total` in the existing per-unit loop (`ingest_core.py:164-186`, where `metrics` is in scope and already null-guarded, so they survive `INGEST_ISOLATION=subprocess`); drop the `modality == "audio"` guard at `:157`; move `hashlib.sha256` off the event loop (`:105-107`); add `video_pipeline_version` to `/health` and the `dp_pipeline_dialect` gauge via the existing `add_gauge_source`; `DP_DIALECT_FREEZE=1` → `_current_pv` returns `None`.
**Depends on:** nothing structurally; the metric *names* are frozen by §8 so WS-B/C/D can emit against them from day one.
**Exit criteria.** All counters visible on `/metrics` at zero before any traffic; `/health` reports the dialect; a two-dialect fixture trips the alert expression; a graph run with `resources=None` (the isolation shape) does not raise.
**Blocked on escalation:** no.

### WS-G — Legacy freeze, migration, runbook · **START IMMEDIATELY** · blocked on E-2 for the *cutover only*

**Scope.** D-14. The 4-line `enabled()` gates, the frozen exemption, the migration and rollback runbook.
**Files owned:** `app/stages/video/keyframes.py`, `app/stages/video/captions.py`, `app/processing/processors/video.py`, `HANDOFF.md` (one status row), `handoff/ws-video-clip.md` (this document, kept current), `tests/test_legacy_dialect.py`.
**Changes.** Add `enabled(self, settings) -> resolve_pipeline(get_vision_settings()) == "keyframe"` to both stages. Nothing else. `_weave_ocr` and the `VIDEO_OCR_RECORDS` branch stay untouched in the legacy path and are simply not reached in clip mode.
**Exit criteria.**
- `pipeline_version` in legacy mode is the literal `"vidproc-vlm-v0"` / `"vidproc-mock-v0"` — **byte-for-byte, asserted**.
- All 11 existing tests in `tests/test_video_pipeline.py` stay green **unmodified** under `VIDEO_PIPELINE=keyframe`.
- An unknown `VIDEO_PIPELINE` value resolves to `keyframe` and the graph resolves — asserted.
- The runbook states, in these words: *rollback restores behaviour, not the corpus*; *drain-and-replace, never rolling*; *forward-only cutover at a UTC day boundary on a fresh `user_id` until E-2 lands*.
**Blocked on escalation:** the **cutover** is blocked on E-2 (storage retraction) for any `user_id` with existing video records. Verified by the lead session, and the picture is *nearly* as clean as it needs to be: the **Phase-3 replay** corpus carries **zero** `vidproc-*` records (all one audio dialect, per the report cited in E-2), but the **dev store** `storage/app/dev.db` does hold **86 `vidproc-mock-v0` caption records** (+39 `asr-fw-v1` transcripts, 125 total) left by earlier dev/E2E runs. Those are mock-dialect records under dev users, not pilot corpus — so the practical rule stands: **cut over on a fresh `user_id`, and there is no real video corpus to retract yet.** This is the last moment that is true; once the pilot runs, E-2 becomes a hard prerequisite for any re-cutover.

### WS-H — Eval harness & the quality gates · **START IMMEDIATELY** · blocked on nothing

**Scope.** The offline A/B, the ground-truth corpora, the Gemini oracle, the ungrounded-quote scorer.
**Files owned:** `scripts/capture_chunkset.py`, `scripts/prompt_ab.py`, `scripts/oracle_gemini.py`, `tests/fixtures/chunksets/**`, `handoff/ws-video-clip-eval.md`.
**Design.** `prompt_ab.py` imports `resolve()` + `run_graph()` + `pipeline.build_c2` directly — **never FastAPI, never `StorageClient`** — so it is structurally incapable of writing to `/context` (`ingest_core.py:178` is the only writer and it lives *above* the processor). Arms are selected by `git worktree`, because a pack is only reproducibly defined by a git state. `DP_OFFLINE_EVAL=1` unlocks pack overrides **and** `app/main.py` refuses to boot when it is set — the flag that enables experiments is the flag that prevents serving.
**Scorers, all mechanical:** the two `pipeline_version` strings and resulting `record_id`s printed side by side (proof the fork is real); records/chunk; chars/record; **projected chars per day-log block against `EXCERPT_CHARS`, rendered through continuum's own `build_daylog`** (a pure function of records); parse-fallback and truncation rates; `app != "unknown"` rate; change-verb rate; **`ungrounded_quote_rate`** — the fraction of double-quoted spans in the caption absent from that chunk's OCR text; measured prompt/completion tokens from `usage`.
**Exit criteria.** A 200-chunk run costs ≈$0.02 and ≈40 s wall, so it is cheap enough to be a pre-push hook — which is the actual requirement, since an eval expensive enough to skip will be skipped. O-4's blind judge run and the $70 Gemini oracle are deliverables of this workstream.
**Blocked on escalation:** no. The oracle needs a Vertex key; if unavailable, the mechanical scorers stand alone.

---

### Parallelism summary

| WS | starts | blocked by | shared-core files |
|---|---|---|---|
| A probe | **now** | — | none |
| B frames | **now** | — | none |
| C ocr | **now** | frozen dataclasses only | none |
| D prompts+primary | **now** (mock) | E-3(a) for the real-backend test only | none |
| E law-as-test | **now** | — | none |
| F observability | **now** | — | `main.py`, `ingest_core.py` (**exclusive**) |
| G legacy+runbook | **now** | E-2 for the cutover only | none |
| H eval | **now** | — | none |
| E2 registration raise | **last** | all of the above | `stagegraph/stage.py` (~8 lines) |

**All eight workstreams can start on day one.** Nothing in the build is blocked on an escalation. What *is* blocked: the production `VIDEO_OCR_BACKEND=ppocr` default (on O-2, WS-C's own deliverable), the real-backend integration test (on E-3(a), which WS-A makes precise on day one), the flip to `VIDEO_PIPELINE=clip` on any user with existing video records (on E-2), and the 5.8× cost figure (on E-1). The 2.4× figure, the 6× record reduction, the determinism fixes, the prompt registry and the whole OCR channel ship without asking anyone.

---

## 12. Appendix — lead-session verification

The design above was produced by a fan-out. These items were re-verified **independently by the
service lead** against the installed toolchain and the live model hub, because three decisions
rest on facts that are not knowable from this repo alone. Two sharpen a decision; one corrects a
figure; one adds candidates.

### 12.1 The `video_url` rejection (D-02) is right, and the reason is sharper than "decord/av"
Read directly from the installed vLLM 0.24.0 in the `vllm-cu13` env (the env `serve_vllm.sh`
defaults to):

- `{"type":"video_url","video_url":{"url": …}}` **is** a supported content part —
  `entrypoints/chat_utils.py:179-189` (`VideoURL` TypedDict), `:1447` (parser), `:1539`.
- base64 video data-URIs **are** supported — `multimodal/utils.py:89-111`
  (`encode_video_base64`, `f"data:{mimetype};base64,{video_b64}"`), `fetch_video` at `:330`.
- The server-side loader is **OpenCV by default, PyAV alternatively** —
  `multimodal/video.py:488, 541, 563, 590, 1285` (`backend: Literal["opencv","pyav"] = "opencv"`).

So the wire would have worked, and that is exactly the trap: frame decode **and** frame selection
would become a function of the *serving box's* loader backend and processor kwargs, not of DP's
pinned ffmpeg. This is the same failure the video-pipeline lead already fixed once by deleting the
OpenCV fallback (`app/vision/frames.py:8-16`: *"a second decoder's scene metric differs from
ffmpeg's, so a heterogeneous fleet would select different keyframes for identical bytes under the
SAME pipeline_version — a silent non-idempotent /context upsert"*). **D-02 stands, and the
determinism argument — not the API-surface argument — is the load-bearing one.**

Multi-image in one message is likewise supported and bounded by `--limit-mm-per-prompt`
(`chat_utils.py:_validate_add` → `validate_num_items`), which is precisely what E-3(a) asks for
and what WS-A's probe measures.

### 12.2 Keyframe arithmetic (§1.4) reproduced independently
Ran `_uniform_times` / `_select_times` with the shipped defaults:

| chunk | uniform grid | selected, no scene cuts | selected, busy screen |
|---|---|---|---|
| 10.0 s | 4 → `[0.0, 2.5, 5.0, 7.5]` | **4** | **8** (cap binds) |
| 18.4 s (observed seg-0 warm-up) | 7 | 7 | 8 |
| 30 s | 8 | 8 | 8 |
| 60 s | 8 → `[0, 7.5, …, 52.5]` | 8 | 8 |

§1.4's figure of 4 is confirmed for real screen content (where the scene pass contributes 0). The
**8** column is the busy-screen upper bound and is what the per-hour range 1,440–2,880 comes from.
One consequence worth stating plainly next to D-03: `VIDEO_MAX_KEYFRAMES=8` is **duration-blind**,
so the legacy path *degrades* as spans grow — at 60 s it samples **0.13 fps**. Against the 5 fps
floor / 10–15 fps balanced point in `readings/Choosing Frame Rate and Resolution…`, today's path
runs **12–37× under-sampled**, and a naive "just send longer chunks to the old pipeline" would make
that worse, not better. This is an independent argument for D-01 + D-03 being a *package*.

### 12.3 The OCR sidecar's dependency argument (D-06) is confirmed
`DP .venv numpy == 2.5.1` (measured), against `paddleocr → paddlex[ocr-core] → numpy<2.4`. The
conflict is real and the HTTP-seam quarantine is the right call.

### 12.4 OCR candidates, verified live on the Hugging Face hub
D-06 names PP-OCRv6 det+rec as the v1 pick and PaddleOCR-VL / dots.ocr / GLM-OCR as
one-config-away alternatives. Confirmed available, with sizes and licences:

| model | params | licence | shape | note |
|---|---|---|---|---|
| `PaddlePaddle/PP-OCRv5_mobile_det` (+ v6 medium det) | tiny | Apache-2.0 | **det+rec pair** | bbox-native, CPU-viable — the D-07 event-driven design needs this shape |
| `dots-studio/dots.ocr` | 3.04 B | MIT | VLM, **layout + bbox JSON** | best structured-geometry fallback |
| `deepseek-ai/DeepSeek-OCR-2` | 3.39 B | Apache-2.0 | VLM, full-page | 11.8 M downloads |
| `baidu/Unlimited-OCR` | 3.34 B | MIT | VLM | top-trending, Jun 2026 |
| `zai-org/GLM-OCR` | — | MIT | VLM | 3.7 M downloads |

The structural point behind D-06/D-07: a **det+rec pair** supports region-level incrementality —
detect boxes, track them, re-recognise only the boxes that changed. A 3 B full-page VLM re-reads
the entire frame on every call and cannot do that at any price. The `readings/OCR processing -
thoughts.md` pipeline is therefore only implementable on the det+rec shape; the VLMs are the
`VIDEO_OCR_BACKEND=vlm` A/B arm and the quality ceiling, exactly as D-06 has them.

### 12.5 Corrections applied to this document
- **E-2's Phase-3 figures** were restated to match the source exactly
  (`continuum/handoff/phase-3-report.md:132-135`): 13,020 emitted, **12,221 captions + 621
  transcripts** in-window. The earlier 12,391/629 split was not in the source.
- **WS-G's "zero `vidproc-*` records"** was scoped correctly: true of the Phase-3 replay corpus,
  but `storage/app/dev.db` holds **86 `vidproc-mock-v0` caption records** (125 total) from earlier
  dev runs. Mock dialect, dev users — the fresh-`user_id` cutover rule is unaffected, but the
  sentence now says what is actually on disk.

---

## Build log — WS-A (wire probe & serving prerequisites)

**Branch `svc/vc-ws-a`. Delivered:** `scripts/vlm_probe.py`, `scripts/ocr_probe.py`,
`handoff/ws-video-clip-probe.md` (the full capability report). No `app/` files touched. DP suite
**173 passed** (`ASR_BACKEND=mock`), unchanged — the deliverables are scripts + a handoff doc,
imported by nothing in the suite.

**How verified.** No VL endpoint is served on this box by default (`:8000` → connection refused), so
the four curls were **not run live** — stated plainly, not fabricated. Instead every assumption was
read from the installed serving stack on this node (`vllm-cu13` = **vLLM 0.24.0**, transformers
5.13.0, the Qwen3-VL-32B `config.json` / `preprocessor_config.json` in the HF cache) — the §12.1
method — and the two load-bearing claims were **adversarially re-checked** by skeptic agents. The
probe scripts speak the exact `app/vision/vlm.py` wire and are the ~60 s live confirmation for when
E-3(b) stands up the captioner endpoint. Each claim in the report carries installed-source `file:line`.

**Findings (all four probes PASS on source evidence; zero *mandatory* launch-flag changes):**

1. **`--limit-mm-per-prompt` — DESIGN ASSUMPTION CORRECTED.** The premise "default is commonly
   `image=1`, so the multi-image call 400s unless raised" is **false for vLLM 0.24.0**: the image cap
   **defaults to 999** (`config/multimodal.py:80,84,320-322`) and Qwen3-VL's model-side supported
   limit is `None`/unlimited (`qwen2_vl.py:868-869`, inherited by `qwen3_vl.py`). **K≤12 images in one
   message already validate on the current, unmodified `serve_vllm.sh`.** ⇒ **WS-D ships
   `screen-clip-v1` (multi-image) as the default; `screen-clip-single-v1` (K=1) is the documented
   degraded/interactive profile, NOT a forced fallback.** The D-02/§11-WS-A fallback branch does not
   activate. *(Adversarial verdict: "flag IS required" → REFUTED, high confidence.)*
2. **Guided decoding — available, ON by default.** `response_format:{"type":"json_schema",
   "json_schema":{"name","schema":{…}}}` is accepted, backend `auto` (xgrammar-first), no flag needed
   (`engine/protocol.py:123-164`, `config/structured_outputs.py:21-25`; xgrammar 0.2.3 / llguidance /
   outlines_core all installed). Recommend pinning `backend=xgrammar` for reproducibility (the `auto`
   default is documented as changing across releases).
3. **768×480 → exactly 360 tokens (factor 32), no clamp.** patch 16 × merge 2 = 32
   (`config.json`; smart_resize `factor=32` overrides the legacy 28, `image_processing_qwen2_vl.py:174`
   / `qwen3_vl.py:929`). `preprocessor_config` `size={65536,16777216}` are AREA min/max_pixels; a
   768×480 frame (368,640 px) sits ~45× under the cap ⇒ not downscaled. The factor-28 (≈470) and
   "materially lower/clamping" branches do **not** fire. *(Adversarial verdict: UPHELD, high
   confidence — survives even the video-path cap.)* `1280×800` = **1000 tok** (2.78× of 768 — A-16's
   cost-blowup, quantified).
4. **`video_url` data-URI — supported & first-class** (`chat_utils.py:179-190`, `media/video.py`);
   0.24.0 is post the Qwen3-VL timestamp-AssertionError fix (`qwen3_vl.py:1451-1454`; 0.24.0 > 0.19.1).
   Informational (O-4). DP still chooses K-stills because `video_url` cedes frame selection to the
   server's OpenCV decode — non-deterministic/non-auditable, the exact hazard D-02/§12.1 rejects.

**Exact flags for E-3(a) (precise, updated ask).** *Strictly required for the image path on vLLM
0.24.0: none.* Recommended as determinism / version-drift guards on the multimodal launch:
```
--limit-mm-per-prompt '{"image":16}'                                    # JSON string only; image=16 is rejected. Pins ≥K=12 (tightens 999→16, safe).
--mm-processor-kwargs '{"size":{"shortest_edge":65536,"longest_edge":16777216}}'   # pins the pixel cap at today's default; image path also accepts {"max_pixels":…,"min_pixels":…}
--structured-outputs-config '{"backend":"xgrammar"}'                    # pin guided-decoding backend (confirm exact CLI spelling on the box)
```
The genuine remaining serving ask is **E-3(b)** — a captioner endpoint distinct from `:8000` — which
this report leaves fully intact (the GPU-contention argument is unchanged).

**OCR probe (for WS-C) — honest SKIP.** No OCR runtime exists on this box (no `paddleocr`/`rapidocr`
in any conda env), and `sidecars/ocr/` is WS-C's to build. `ocr_probe.py` SKIPs cleanly and states the
`/health` model-pin + `/ocr` contract it will check and the §7.1 "0.6 s/1728×1080 frame" assumption it
will time — via a **separate** interpreter (never importing paddle into the numpy-2.5.1 DP venv, §12.3).
Gated on O-2 (WS-C's own deliverable); WS-A ships the instrument, not the verdict.

**Exit criteria met:** the report exists, is committed, names the exact flags, and feeds E-3(a) without
being blocked by it — the probe's job was to make the ask precise, and it did: the ask is now *lighter*
(the multi-image call needs no serving change to be admitted) and *sharper* (E-3(b) is the real one).
