# ws-video-clip build logs — WS-A … WS-H

> The build records for the screen-video clip path, rolled out of
> [ws-video-clip.md](ws-video-clip.md) on 2026-07-28 under [STYLE.md](../../../STYLE.md)
> §Growing a worklog: the design and the fifteen build logs had grown into one 2,513-line file,
> and the design was the half people needed.
>
> **Nothing here was edited when it moved.** These are the workstreams' own records — exit-criteria
> checklists, adversarial rounds, corrections and hand-offs to the lead — kept verbatim.
> The decisions they implement are [ws-video-clip.md](ws-video-clip.md) §3; the escalations are its
> §10.

## Build log — WS-D (primary)

Tab **D2** (the `CLIPCAP` primary, multi-image payload, parse ladder, emit). Branch `svc/vc-ws-d`,
shared worktree with tab D1 (Foundation: `config.py` clip fields, `version.py`, `budget.py`,
`prompts/`). D1's foundation landed on disk first; this tab imported it. **Suite: 294 passed**
(173 legacy baseline unchanged + D1's tests + *67 D2 tests*: 39 `test_parse.py`, 28
`test_clipcap.py`). All clip stages dormant by default (`VIDEO_PIPELINE=keyframe`), so the legacy
fixtures stay green.

**Landed (D2-owned, all under `app/vision/` — no shared-core edits):**
- `app/vision/parse.py` — the tolerant parse ladder (§5.3), a pure function of the reply. Rungs
  `clean|stripped|repair|keys|partial|line|whole`; only `clean` is not a fallback; empty `RAISES`
  `EmptyReplyError` (a `ValueError`, matches `vlm.py:119-121`). Counting is the caller's job (the
  ladder returns `ParseOutcome.step`), so it stays pure. Handles fenced / prose / truncated-mid-JSON
  / wrong-keys(alias) / refusal / prompt-echo(placeholder-stripped) / empty. String-aware brace
  scanner; one syntactic repair (smart quotes, trailing commas, raw newlines-in-strings).
- `app/vision/clipcap/{__init__,mock,vlm,vertex}.py` — the backend seam (`select` on `VIDEO_BACKEND`,
  unknown→mock). `mock` (`vidclip-mock-v1`, `prompt_tag=""`, text from `(n_frames,span,chunk_id)`
  only — never pixels/OCR/prompt). `vlm` (`vidclip-vlm-v1`): the D-02 ONE multi-image call — K
  `image_url` parts with `Frame k (+t.s):` labels interleaved, task+JSON contract last, D-09 OCR
  injected as `[[ocr_block]]`, `temperature 0`, async loop-native `httpx.AsyncClient` (0 threadpool
  tokens, §7.4), pack-variant selection (single/idle/scenario), parse-ladder integration with
  `dp_video_parse_fallback_total{pack,step}` + `dp_video_truncated_total{pass}` (null-guarded).
  `vertex` (`vidclip-vertex-v1`): documented stub, raises (D-15 oracle, not a serving path).
- `app/vision/emit.py` — `render_caption` (D-10 one paragraph, D-11 `caption_cap` sentence-boundary
  truncation, D-12 single-line) + `caption_unit` (D-05: exactly one `kind='caption'`,
  `discriminator=""`, `t_start=None`/`t_end=None` → `build_c2` carries the C1 span byte-for-byte,
  `abs_time` never called). The D-05 ">1 ocr record" discriminator rule is written beside it.
- Composed dialects verified: mock `vidclip-mock-v1#<cfg8>` (no `@` prompt tag → a prompt edit does
  not re-key the headless corpus); vlm `vidclip-vlm-v1@screen-clip-v1.p1.<digest8>#<cfg8>`.

**Exit criteria met headless (WS-D §11):** parse ladder over malformed replies (each fallback
classified, empty raises) ✓; exactly one `caption` unit, `discriminator=""`, `t_start=None`,
`c2["t_start"]==c1["t_start"]` byte-for-byte ✓; `content.text` no `\n`, respects `caption_cap` ✓;
mock text from `(n,span,chunk_id)` only, `prompt_tag==""` ✓; K `image_url` parts with interleaved
frame labels + task last ✓; version composition (`PIPELINE_VERSION+prompt_tag+cfg_tag`) ✓.

---

## Build log — WS-D (pack)

Tab **D1** (the prompt pack, config, and version plumbing — the modules D2 imports). Branch
`svc/vc-ws-d`, shared worktree with tab D2. Landed the module files + public signatures first so D2
could build against them; D2's section above confirms the seam end to end (its verified dialects
`vidclip-vlm-v1@screen-clip-v1.p1.<digest8>#<cfg8>` and mock `vidclip-mock-v1#<cfg8>` are exactly this
tab's `prompts.version_tag(vs)` + `version.cfg_tag(vs)`). **Suite: 300 passed** (173 legacy baseline
unchanged + D1's *60 tests* — 17 `test_budget.py`, 43 `test_prompt_pack.py`, + D2's). All clip stages
dormant by default (`VIDEO_PIPELINE=keyframe`), so legacy fixtures stay green.

**Landed (D1-owned, all under `app/vision/` — no shared-core edits):**
- `app/vision/config.py` — `VisionSettings` now carries **all 47 knobs** (legacy keyframe path + the
  `clip_*`/`ocr_*`/`scenario`/`pipeline` clip knobs per D-03/D-11/D-13), lenient env parse (a numeric
  typo WARNs → default, never 500s a fleet), `clip_frame_width` clamped ≤1024 with a cost `WARN`
  (A-16), and `prompt_dir_fingerprint` computed *once at import* (D-13 TOCTOU, never re-stat'd).
  Other workstreams' TEMP `os.getenv` shims can now read real fields.
- `app/vision/version.py` — `cfg_tag(vs)` = `#`+sha8 over the explicit `OUTPUT_AFFECTING` allowlist
  (31), and `OPERATIONAL_ONLY` (16). `assert_fields_classified()` enforces
  `set(OUTPUT_AFFECTING)|set(OPERATIONAL_ONLY)==VisionSettings fields` — a new knob cannot be added
  without being classified. `vlm_url`/`ocr_url` and the 8 legacy keyframe knobs are deliberately
  OPERATIONAL_ONLY (an endpoint move / a frozen-`""`-dialect legacy knob cannot change a clip
  record's bytes; folding them would be a self-inflicted double-count).
- `app/vision/prompts/**` — the git-tree pack registry (D-13). `PACK_DIGEST` = sha8 over the
  Normalised specs (id+role+decode params+**schema**+system+user, line-endings/trailing-ws
  canonicalised) + `routes.json`, loaded once at import. `select`/`version_tag` both go through one
  `_resolve_pack_id` (unknown / non-clip `VIDEO_CLIP_PROMPT` → pinned clip default; never fork, never
  collide, never render the wrong family). Six `.prompt.md` packs authored per §5
  (`screen-clip-v1`, `screen-clip-idle-v1`, `screen-clip-single-v1`, `screen-ocr-v1`, `camera-clip-v1`,
  `per-frame-v0`), `routes.json`, `schemas.json`, `LOCK.json`, `archive/p1.json`, and
  `python -m app.vision.prompts {show|relock|status}` (show prints exact wire text; relock bumps the
  human token + archives full text *+ routes* so a historical dialect reconstructs from one file).
- `app/vision/budget.py` — `caption_cap`/`ocr_cap` (`round(rate×span)`, span-invariant dose, D-11),
  `caption_word_bounds`, deterministic `truncate_sentence`/`truncate_word` (D2's `emit.py` uses these).

**Exit criteria met headless (§11 WS-D, pack half):** PACK_DIGEST stable across whitespace/CRLF/
trailing-newline, forks on text/decode-param/**schema**/route edits ✓; unknown `VIDEO_CLIP_PROMPT` →
pinned default in both `select()` and `version_tag()` ✓; mock corpus not re-keyed by a prompt edit
(`cfg_tag` never folds `PACK_DIGEST`; `prompt_dir_fingerprint==""` packaged) ✓;
`OUTPUT_AFFECTING|OPERATIONAL_ONLY==fields`, and the check fails on an unclassified field ✓; one byte
of a `.prompt.md` → `pipeline_version` → `record_id` end to end ✓; `show` prints exact wire text,
`relock` bumps+archives ✓; packs load once per process, never re-stat'd ✓.

**Adversarial verification (17-agent fan-out review, 5 lenses × refute-by-default):** 1 confirmed
defect fixed — `family_default()` could return the hardcoded default even when a `VIDEO_PROMPT_DIR`
override omitted it, so `version_tag()` (`ACCEPT`) and `select()` (RUN) could diverge and crash a
worker; now `load_registry` **validates that `routes.json` references only loaded packs and fails
loud at import** (honouring the module's own "a bad drop-in fails loud" promise) and `family_default`
provably returns a loaded `role=='clip'` pack. Hardening from refuted-but-valid observations: typed
`PromptPackError` on non-numeric decode params; `relock` archive now carries `routes.json`; `show
--scenario` renders the deployment's actual label.

**Flag for D3 / lead (design tension, NOT reclassified):** `structured_mode` (guided-decoding
toggle) sits in OPERATIONAL_ONLY per the ratified allowlist (D-13 line 450), yet §5.3 calls guided
decoding "the primary discipline lever" that can move a weak model's output at the tail — an
O-7-class question. The parse ladder is designed to make guided/unguided converge to the same
`ClipDesc`, and the schema itself already forks via `PACK_DIGEST`, so it is kept per spec; surfaced
here rather than reclassified unilaterally.

**Adversarial hardening (two verify fan-outs, findings executed against the venv):**
- parse ladder — found + fixed a real `RecursionError` crash: a brace-balanced but deeply-nested
  reply (`'{"a":'*20000…`) makes CPython's recursive `json.loads` raise `RecursionError` (a
  `RuntimeError`, not `ValueError`), which escaped `parse_clip` uncaught — a non-empty reply `MUST`
  never raise. `_loads` now catches broadly and falls through to `whole`. Regression test added.
- vlm payload — found + fixed a real split bug: the head/tail split used `find` on the rendered user
  text, so an OCR string containing "Reply with ONE JSON" (a screenshot of the instructions) could
  steal the split; now `rfind` (the real task is always last). Regression test added.
- clipcap review (payload / lifecycle / emit-record / determinism) — **0 confirmed defects**.

**`HELD` — `app/stages/video/clipcap.py` (the thin primary stage):** blocked on WS-B `clipprep` + WS-C
`screentext` being *registered*, because stagegraph `_discover` (`stage.py:282-291`) raises if a
declared `need` names an unregistered stage, landing it early turns the whole suite red. Draft is ready
(scratchpad `clipcap_stage_DRAFT.py`); a watcher lands it when both deps register. The stage is a thin
wrapper (seam call + `emit.caption_unit`); every record-contract exit criterion is already proven via
`emit.caption_unit`+`build_c2` without it. `CONFIRMED` wiring as specified: primary/required, order 20,
`needs=("clipprep","screentext")`, `provides=("clip",)`, `mutable_slots=("enrichments",)`,
`enabled()==resolve_pipeline()=="clip"`.

**Integration risk for the consolidation tab (NOT WS-D's to fix):** the design's `clipprep` order 0
and `screentext` order 10 collide with legacy `keyframes`(0)/`captions`(10) under the per-modality
order-uniqueness check (`stage.py:260-266`) — landing them at 0/10 while legacy holds 0/10 breaks
discovery for everyone. WS-G's gate or an order re-plan must resolve it; `clipcap`(20) is unique and
unaffected. Also: WS-G's `enabled()` gate on `keyframes`/`captions` is not yet on disk, so in `clip`
mode both legacy primaries are still enabled — a full clip-graph resolve will see two primaries until
that gate lands (why D2's tests drive the pieces directly, not the resolver).

---

## Build log — WS-D (consolidation · D3)

Tab **D3** — closing WS-D end to end after D1 (pack/config/version) and D2 (primary/parse/emit).
Branch `svc/vc-ws-d`. **Suite: 304 passed** (173 legacy baseline unchanged + 127 D1/D2 tests + *4
D3 consolidation tests* in `tests/test_clip_consolidation.py`), `ASR_BACKEND=mock ./.venv/bin/python
-m pytest -q`, all clip stages dormant by default (`VIDEO_PIPELINE=keyframe`). No shared-core edits;
D3 touched only WS-D-owned files (`app/vision/config.py`, `app/vision/version.py`, and a new
`tests/test_clip_consolidation.py`).

### §11 WS-D exit-criteria checklist — PASS/FAIL

| # | Exit criterion (§11 WS-D) | Verdict | Evidence |
|---|---|---|---|
| 1 | `PACK_DIGEST` stable across a whitespace-only edit, changes on any semantic edit | `PASS` | `test_prompt_pack.py` (digest tests) + `test_clip_consolidation.py::test_prompt_byte_edit_forks_record_id_end_to_end` (`d_ws==d_base`, `d_sem!=d_base` over real `load_registry`/`compute_digest`) |
| 2 | Unknown `VIDEO_CLIP_PROMPT` → pinned default in **both** `select()` and `version_tag()` | `PASS` | `test_prompt_pack.py::test_unknown_clip_prompt_resolves_to_default_in_both`; `_resolve_pack_id` coerces unknown/non-clip → `family_default("clip")` in both |
| 3 | `mock` backend `prompt_tag` returns `""` — a prompt edit does not re-key the headless corpus | `PASS` | `test_clipcap.py::test_mock_prompt_tag_is_empty` + `test_clip_consolidation.py::test_prompt_edit_does_not_rekey_the_mock_corpus` (record_id level: `PACK_DIGEST not in` mock pv) |
| 4 | Mock text derives from `(n_frames, span_seconds, chunk_id)` only — never pixel bytes | `PASS` | `test_clipcap.py::test_mock_is_deterministic_and_ignores_pixels_and_ocr` (pixels + OCR text both varied, description invariant; `chunk_id` varied, description changes) |
| 5 | `set(OUTPUT_AFFECTING) \| set(OPERATIONAL_ONLY) == set(VisionSettings.__dataclass_fields__)` — fails until a new field is classified | `PASS` | `test_prompt_pack.py` (partition + `assert_fields_classified()` + forget-a-field negative). After D3 folded in `ocr_model`/`ocr_api_key`: **49 fields = 32 OUTPUT_AFFECTING + 17 OPERATIONAL_ONLY**, clean partition |
| 6 | One byte of a `.prompt.md` edit changes `pipeline_version` → `record_id`, end to end | `PASS` | `test_clip_consolidation.py::test_prompt_byte_edit_forks_record_id_end_to_end` — semantic edit forks both `pipeline_version` and `record_id` (and builds a C2-valid differing record); whitespace-only edit inert all the way down |
| 7 | Exactly one `kind='caption'`, `discriminator=""`, `t_start=None`; `c2["t_start"]==c1["t_start"]` byte-for-byte | `PASS` | `test_clip_consolidation.py::test_mock_clipcap_seam_emits_one_frozen_caption_with_byte_identical_span` (record SET len 1, trailing `Z` survives, `abs_time` never called) + `test_clipcap.py::test_c2_carries_c1_span_byte_for_byte` |
| 8 | `content.text` has no `\n` and respects `caption_cap(span, vs)` | `PASS` | same consolidation test (asserts `"\n" not in text`, `len(text) <= caption_cap`) + `test_clipcap.py::test_render_is_single_line_and_within_budget` |
| 9 | Parse ladder over malformed replies (fenced / prose-prefixed / truncated mid-JSON / wrong-keys / refusal / prompt-echo / empty); every fallback counted; empty `RAISES` | `PASS` | `test_parse.py` (39 tests, all seven categories present) + `test_clipcap.py` fallback-counting + `test_vlm_empty_reply_raises` |
| 10 | `python -m app.vision.prompts show` prints exact wire text; `relock` bumps + archives | `PASS` | Ran firsthand: `show --pack screen-clip-v1` printed system+user + tag `@screen-clip-v1.p1.565066a0`; `relock <tmpcopy>` bumped `p1→p2`, wrote `archive/p2.json` + `LOCK.json` (committed tree untouched) |
| 11 | Request carries K `image_url` parts with `Frame k (+t s):` labels interleaved, task text last | `PASS` | `test_clipcap.py::test_vlm_payload_interleaves_frames_and_puts_task_last` (+ the `rfind` split-steal regression test) |
| — | Real-backend (vLLM) integration test | **Deferred (not `FAIL`)** | Gated on **E-3(a) / WS-A's `vlm_probe.py`** per §11; every criterion above runs headless. The production wire (D-02 multi-image payload, `response_format`, parse ladder) is covered against an `httpx.MockTransport` fake VL server, so only the live-endpoint assertion waits on the probe |

**All 11 headless exit criteria `PASS`.** The single deferred item is the E-3(a)-gated live-VLM test,
which is explicitly out of WS-D's headless scope.

### D1↔D2 seam reconciliation

- **D2 reads only real `VisionSettings` fields** — no TEMP `os.getenv` shim anywhere in
  `app/vision/clipcap/**`, `parse.py`, `emit.py` (verified: the only `vs.*` reads are `backend`,
  `vlm_model`, `vlm_url`, `vlm_api_key`, `vlm_timeout`, `scenario`, `structured_mode`, all real).
- **Folded in two orphan WS-C OCR-arm knobs** (their shim `app/vision/ocr/config.py` marks every
  field `# TEMP -> VisionSettings (WS-D)`; these two had no home in D1's 47-field set):
  - `ocr_model` ← `VIDEO_OCR_MODEL` (vlm-OCR-arm served-model name) → **OUTPUT_AFFECTING**: a served
    OCR-model swap changes the `kind='ocr'` record's bytes and, injected under D-09, the caption —
    exactly the class `vlm_model` is OUTPUT_AFFECTING for. `''` under `mock`/`ppocr`, so it forks
    nothing until the vlm OCR arm is used.
  - `ocr_api_key` ← `VIDEO_OCR_API_KEY` (vlm-OCR-arm bearer) → **OPERATIONAL_ONLY**: a credential,
    like `vlm_api_key`; cannot change a record's bytes.
  `VisionSettings` is now *49 fields*; `assert_fields_classified()` stays green. After this every
  `VIDEO_OCR_*` knob WS-C's shim reads has a real field, so its migration is a pure lift-and-shift.
- **WS-B's shim knobs already all had homes** (`clip.py` `_env_*`: `VIDEO_CLIP_FRAME_WIDTH`,
  `_SECONDS_PER_FRAME`, `_MAX_FRAMES`, `_MIN_FRAMES`, `VIDEO_OCR_FRAME_WIDTH`, `VIDEO_ANALYSIS_PERIOD_S`,
  `VIDEO_OCR_IDLE_PEAK`, `_LAYOUT_PEAK`, `_MAX_EVENTS`, `_FLOOR_S`) — all map to existing
  `clip_*`/`ocr_*`/analysis fields. Nothing to add.

**One reconciliation note for the lead (a knob-name change, not a missing field):** WS-C's shim reads
`VIDEO_OCR_CHARS_PER_SECOND` (its `ocr_chars_per_second`, default 6). D1's design instead derives the
OCR char rate as `chars_per_second − caption_chars_share` via the frozen `budget.ocr_cap(span, vs)`
(`22 − 16 = 6` — the defaults match, so **no behaviour change**). At merge WS-C should drop its
`ocr_chars_per_second` knob and call `ocr_cap(span, vs)`; there is deliberately *no*
`VIDEO_OCR_CHARS_PER_SECOND` field (a second dial for the same budget would let caption+ocr shares
sum to ≠ total). Flagged rather than silently added.

**Migration direction at integration (for the tab that merges WS-B/WS-C):** WS-B and WS-C branched
off the pre-D1-config base, so they still carry local shims (`app/vision/config.py` legacy view,
`app/vision/ocr/config.py`, `clip.py` `_env_*`, `mode.py`). At merge, D1's `app/vision/config.py` +
`app/vision/mode.py` win; the shims are deleted and their readers switch to `get_vision_settings()`.
Every field they need now exists and is classified — this reconciliation made that true.

### Carried forward for the integration tab / lead (NOT WS-D-owned to land)

- **`app/stages/video/clipcap.py` stays `HELD`.** Its `needs=("clipprep","screentext")` cannot resolve
  until WS-B/WS-C register those stages — stagegraph `_discover` (`stage.py:283-291`) imports all
  stage modules then `RAISES` `StageRegistrationError` on a need naming an unregistered stage, which
  fires in the existing suite. Confirmed by D3. The ready draft is in the D2 scratchpad
  (`clipcap_stage_DRAFT.py`); land it only once both deps register. Every record-contract exit
  criterion is already proven through `emit.caption_unit` + `build_c2` exactly as
  `ClipcapStage.assemble` will run it.
- **Order collision (D2's flag, re-confirmed):** clip `clipprep`(0)/`screentext`(10) collide with
  legacy `keyframes`(0)/`captions`(10) under the per-modality order-uniqueness check; needs WS-G's
  `enabled()` gate or an order re-plan. `clipcap`(20) is unique.
- **Real-backend integration test** blocked on *E-3(a) / WS-A*, as above.

Clean commit on `svc/vc-ws-d`. Did not touch `HANDOFF.md` / `CHARTER.md`.
## Build log — WS-A (wire probe & serving prerequisites)

**Branch `svc/vc-ws-a`. Delivered:** `scripts/vlm_probe.py`, `scripts/ocr_probe.py`,
`handoff/ws-video-clip-probe.md` (the full capability report). No `app/` files touched. DP suite
**173 passed** (`ASR_BACKEND=mock`), unchanged — the deliverables are scripts + a handoff doc,
imported by nothing in the suite.

**How verified.** No VL endpoint is served on this box by default (`:8000` → connection refused), so
the four curls were **not run live** — stated plainly, not fabricated. Instead every assumption was
read from the installed serving stack on this node (`vllm-cu13` = *vLLM 0.24.0*, transformers 5.13.0,
the Qwen3-VL-32B `config.json` / `preprocessor_config.json` in the HF cache) — the §12.1 method, and
the two load-bearing claims were *adversarially re-checked* by skeptic agents. The probe scripts speak
the exact `app/vision/vlm.py` wire and are the ~60 s live confirmation for when E-3(b) stands up the
captioner endpoint. Each claim in the report carries installed-source `file:line`.

**Findings (all four probes `PASS` on source evidence; zero *mandatory* launch-flag changes):**

1. **`--limit-mm-per-prompt` — Design assumption corrected.** The premise "default is commonly
   `image=1`, so the multi-image call 400s unless raised" is *false for vLLM 0.24.0*: the image cap
   *defaults to 999* (`config/multimodal.py:80,84,320-322`) and Qwen3-VL's model-side supported
   limit is `None`/unlimited (`qwen2_vl.py:868-869`, inherited by `qwen3_vl.py`). *K≤12 images in one
   message already validate on the current, unmodified `serve_vllm.sh`.* ⇒ *WS-D ships
   `screen-clip-v1` (multi-image) as the default; `screen-clip-single-v1` (K=1) is the documented
   degraded/interactive profile, NOT a forced fallback.* The D-02/§11-WS-A fallback branch does not
   activate. *(Adversarial verdict: "flag IS required" → `REFUTED`, high confidence.)*
2. **Guided decoding — available, ON by default.** `response_format:{"type":"json_schema",
   "json_schema":{"name","schema":{…}}}` is accepted, backend `auto` (xgrammar-first), no flag needed
   (`engine/protocol.py:123-164`, `config/structured_outputs.py:21-25`; xgrammar 0.2.3 / llguidance /
   outlines_core all installed). Recommend pinning `backend=xgrammar` for reproducibility (the `auto`
   default is documented as changing across releases).
3. **768×480 → exactly 360 tokens (factor 32), no clamp.** patch 16 × merge 2 = 32 (`config.json`;
   smart_resize `factor=32` overrides the legacy 28, `image_processing_qwen2_vl.py:174` /
   `qwen3_vl.py:929`). `preprocessor_config` `size={65536,16777216}` are area min/max_pixels; a 768×480
   frame (368,640 px) sits ~45× under the cap ⇒ not downscaled. The factor-28 (≈470) and "materially
   lower/clamping" branches do *not* fire. *(Adversarial verdict: `UPHELD`, high confidence — survives
   even the video-path cap.)* `1280×800` = *1000 tok* (2.78× of 768, A-16's cost-blowup, quantified).
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
The genuine remaining serving ask is **E-3(b)** — a captioner endpoint distinct from `:8000`, which
this report leaves fully intact (the GPU-contention argument is unchanged).

**OCR probe (for WS-C) — honest `SKIP`.** No OCR runtime exists on this box (no `paddleocr`/`rapidocr`
in any conda env), and `sidecars/ocr/` is WS-C's to build. `ocr_probe.py` SKIPs cleanly and states the
`/health` model-pin + `/ocr` contract it will check and the §7.1 "0.6 s/1728×1080 frame" assumption it
will time — via a **separate** interpreter (never importing paddle into the numpy-2.5.1 DP venv, §12.3).
Gated on O-2 (WS-C's own deliverable); WS-A ships the instrument, not the verdict.

**Exit criteria met:** the report exists, is committed, names the exact flags, and feeds E-3(a) without
being blocked by it — the probe's job was to make the ask precise, and it did: the ask is now *lighter*
(the multi-image call needs no serving change to be admitted) and *sharper* (E-3(b) is the real one).
## Runbook — legacy freeze, migration & rollback (WS-G)

*Operator-facing. This is the WS-G block; edit only here. Implements D-14. The whole cutover
is one environment variable, `VIDEO_PIPELINE`, resolved by the single `resolve_pipeline()`
(`app/vision/mode.py`): `keyframe` (the shipped default, unknown/mistyped → here) | `clip`.*

### 0. The one thing to internalise first

The one law: **rollback restores behaviour, not the corpus.** Flipping `VIDEO_PIPELINE` back to `keyframe`
makes the *next* chunk process under the legacy dialect again — instantly and safely, but every
`clip`-dialect record already written to `/context` **stays written**. `record_id` forks by design
(`pipeline.py:12-14`), old records persist across a receipt change (`journal.py:296-298`), and `/context`
has no delete. There is *no operation in the system that removes a `vidclip-*` record until storage ships
E-2* (§10 E-2 · A-3). Plan the cutover as a one-way door on the corpus even though it is a two-way door on
behaviour.

### 1. What the gate guarantees (so you can flip it without fear)

- **Legacy is byte-frozen.** In `keyframe` mode the video graph resolves to the literal
  `pipeline_version` `vidproc-vlm-v0` / `vidproc-mock-v0`, byte-for-byte (asserted in
  `tests/test_legacy_dialect.py`). `keyframes` is the single frozen R1 exemption — a sidecar with
  a non-empty `provides` but an *empty `version_fragment`*, so it contributes nothing to the
  dialect and the pre-migration `record_id`s reproduce exactly. Re-running the legacy path over an
  already-processed chunk is therefore an idempotent upsert, never a second disjoint record set.
- **A typo cannot 500 you.** An unknown / mistyped `VIDEO_PIPELINE` (`clipp`, `keyframes`, ``,
  whitespace, wrong case that doesn't normalise) resolves to `keyframe`, so the graph always has
  exactly one enabled primary. It never lands in the "zero enabled primaries →
  `GraphResolutionError` → 500 on every video ingest" hole (§8 finding #1).
- **The two legacy stages flip as a pair.** `keyframes` and `captions` gate on the *same*
  resolver, so a mistype can never disable exactly one and orphan the other.

### 2. Prerequisites before `VIDEO_PIPELINE=clip` may be flipped on

The gate itself ships **now** (this workstream). Turning it *on* in production is blocked until
all of the following are true — none of them is WS-G's to land, they are listed so the operator
does not flip early:

1. **The clip stages are in the image** — `clipprep` (WS-B), `screentext` (WS-C), `clipcap`
   (WS-D). On a branch without them, `VIDEO_PIPELINE=clip` disables the legacy pair and finds
   *zero* primaries → `GraphResolutionError`. That is correct and intended (it is how
   `tests/test_legacy_dialect.py::test_clip_mode_disables_the_legacy_pair` proves the gate is
   real), but it means *clip mode must never be flipped on until the clip primary exists*.
2. **WS-E2 has landed** — the registration-time raise that binds `enabled()`↔`version_fragment()`
   for a sidecar with non-empty `provides` (`app/stagegraph/stage.py`). Architecture A's R1
   correctness for the *caption* depends on it (Decision addendum edit #6), so it ships *before*
   the flip, not after.
3. **Serving is ready (real backend)** — E-3(a): `--limit-mm-per-prompt '{"image":16}'` + an
   explicit `max_pixels`, or the multi-image call 400s on the first chunk. Until then the mock
   backend runs headless.
4. **OCR default is settled** — production `VIDEO_OCR_BACKEND=ppocr` is gated on O-2; ship
   `VIDEO_OCR_BACKEND=mock` until it passes. `off` is the honest degrade, not a `best_effort`
   flip (A-10).
5. **The target is a fresh `user_id`** — see §4.

### 3. Deploy discipline — **drain-and-replace, never rolling**

Do **drain-and-replace, never rolling.** `daylog.py` filters on neither `kind` nor
`pipeline_version` (`db.py:344-351`; `context_reader.py:7`: filters exist only *"when they're
ratified"*). During a rolling restart two replicas resolve two dialects simultaneously, and a
single 2-min day-log block then contains records from **both** `vidproc-*-v0` and `vidclip-*` —
double-counting the same span at consolidation. So:

1. Stop admitting new work (let recording's queue absorb it; `INGEST_QUEUE_MAX=4096` is the buffer).
2. **Drain** in-flight chunks to completion (`INGEST_DRAIN_TIMEOUT=120`).
3. Replace the *entire* replica set with the new `VIDEO_PIPELINE` value — no replica of the old
   dialect may be serving alongside a replica of the new one.
4. Resume admission.

The tripwire that catches a violation: the `dp_pipeline_dialect{modality,pipeline_version}=1` gauge
(WS-F), with **alert on `count by (modality) (dp_pipeline_dialect) > 1`**. If that alert fires
during a deploy, a rolling restart leaked two dialects — roll the deploy, do not proceed.

### 4. Forward cutover procedure (`keyframe` → `clip`)

The rule is a **forward-only cutover at a UTC day boundary on a fresh `user_id` until E-2 lands**.
Never backfill; never re-cut an existing `user_id`'s history.

Why each clause:
- **forward-only / never backfill** — old chunks stay under the old dialect; re-processing them
  under `clip` mints a *second*, disjoint record set for spans that already have one (`record_id`
  forks by design), and nothing can retract the loser until E-2.
- **at a UTC day boundary** — consolidation buckets a day at a time; splitting a single day across
  two dialects double-counts that day's blocks. A UTC-midnight cut keeps every rendered day
  single-dialect. (DP emits *relative* offsets and `daylog.py` anchors blocks in `win.tz`
  (A-6/E-4a); the boundary that matters for *record placement* and re-consolidation is the UTC day,
  which is what `t_start` sorts on.)
- **on a fresh `user_id`** — the clean guarantee that no span this user owns already carries a
  legacy record, so there is nothing to double-count and nothing to retract. This is affordable
  *right now*: the Phase-3 replay corpus carries *zero* `vidproc-*` records, and the only
  `vidproc-mock-v0` records on disk are *86* dev-store rows (`storage/app/dev.db`, 125 total)
  under dev users — mock dialect, not pilot corpus. *This is the last moment that is true*; once
  the pilot writes real video records, E-2 becomes a hard prerequisite for any re-cutover.

Steps:
1. Confirm §2 prerequisites.
2. Provision (or select) a **fresh `user_id`** with no prior video records.
3. Wait for a **UTC day boundary**.
4. Set `DP_DIALECT_FREEZE=1` for the flip window (WS-F): `_current_pv` returns `None`, so a
   redelivered chunk is served from its stale-dialect receipt rather than mass-reprocessed under
   the new dialect during the transition — a DP-local mitigation for the reprocess-on-redelivery
   hazard.
5. **Drain-and-replace** (§3) the replica set with `VIDEO_PIPELINE=clip` (+ the settled
   `VIDEO_OCR_BACKEND`, and the real captioner backend/flags per §2).
6. **Verify** (§6). Once the dialect gauge shows a single `vidclip-*` value and the first blocks
   render clean, clear `DP_DIALECT_FREEZE`.

### 5. Rollback procedure (`clip` → `keyframe`)

Behaviourally instant and safe; corpus-wise a one-way door.

1. **Drain-and-replace** (§3) back to `VIDEO_PIPELINE=keyframe`. Do *not* rolling-restart the
   rollback either — the same two-dialects-in-one-block hazard applies in reverse.
2. The next chunk processes under `vidproc-*-v0` again, byte-for-byte identical to pre-migration
   (§1) — reprocessing a legacy chunk idempotently upserts its original `record_id`.
3. **What rollback does NOT undo:** every `vidclip-*` record written during the clip window
   remains in `/context`. Any day that was consolidated (or gets re-consolidated) across the clip
   window still renders those records. Because the cutover was *forward-only on a fresh
   `user_id`*, that blast radius is confined to the one pilot user's clip-window days — which is
   the entire reason for those clauses. Full corpus cleanup waits on *E-2*
   (`DELETE /context/records?user_id=&from=&to=&pipeline_version=&kind=`, kind-aware — §10 E-2).
4. If you rolled back because clip mode was *misconfigured* rather than *wrong*, do not re-cut the
   same `user_id`; move forward on another fresh `user_id` once fixed.

### 6. Verification checklist

- `GET /health` → `video_pipeline_version` is the single expected dialect for the whole fleet.
- `dp_pipeline_dialect` gauge shows **exactly one** value per modality
  (`count by (modality) (dp_pipeline_dialect) == 1`). More than one ⇒ a rolling deploy leaked
  dialects (§3) — remediate before trusting any block from the window.
- No spike in `dp_graph_stage_failures_total` / dead-letters (a zero-primary resolve or a serving
  400 shows up here).
- First rendered day-log blocks for the cutover user are single-dialect and not double-counted.
- Tests: `ASR_BACKEND=mock VIDEO_PIPELINE=keyframe ./.venv/bin/python -m pytest -q` stays green,
  and `tests/test_legacy_dialect.py` pins the frozen dialect + the unknown-value safety.

### 7. Environment reference (WS-G-relevant knobs only)

| var | values | default | effect |
|---|---|---|---|
| `VIDEO_PIPELINE` | `keyframe` \| `clip` | `keyframe` | the graph selector; unknown → `keyframe` (safe) |
| `VIDEO_BACKEND` | `mock` \| `vlm` | `mock` | legacy captioner → `vidproc-mock-v0` / `vidproc-vlm-v0` |
| `DP_DIALECT_FREEZE` | `0` \| `1` | `0` | `1` → `_current_pv` returns `None`; serve stale-dialect receipts during the flip window (WS-F) |

Read fresh from the environment per call — a flip takes effect at the next graph build, no
re-import (`app/vision/mode.py`, `app/config.py`).

---

## Build log — WS-G

**Scope delivered (D-14 · §11 WS-G):** froze the legacy keyframe path behind the `VIDEO_PIPELINE`
gate and wrote the migration/rollback runbook above.

**Changes**
- `app/stages/video/keyframes.py` — added `enabled(self, settings) -> resolve_pipeline() ==
  "keyframe"` (imports `resolve_pipeline` from `app/vision/mode.py`, the committed Foundation
  file). Documented the frozen R1 exemption in place: `keyframes` keeps **no** `version_fragment`
  (inherits `""`) so the legacy dialect reproduces byte-for-byte. Nothing else in the body.
- `app/stages/video/captions.py` — added the same `enabled()` gate to the primary. `version_fragment`
  is **unchanged** (`select_captioner(vs).PIPELINE_VERSION`), so the gate touches enabledness only,
  never the dialect. `_weave_ocr` and the `VIDEO_OCR_RECORDS` branch are untouched — simply not
  reached in clip mode.
- `app/processing/processors/video.py` — reviewed; the compat shim needs no change (the gate lives
  in the stage files). Left as-is.
- `tests/test_legacy_dialect.py` — new, 36 tests: literal `vidproc-{mock,vlm}-v0` dialect
  (byte-for-byte); `keyframes` is the single empty-fragment exemption and the composed dialect is
  the primary's base alone; unknown/mistyped values resolve to `keyframe` (parametrised) and the
  graph resolves (never zero-primary → 500); case/whitespace normalisation; the two legacy stages
  flip together; `clip` disables the pair (zero-primary raise on this branch, by design); and two
  E2E `/ingest` checks (unknown mode still 200 under the frozen dialect; explicit `keyframe` ==
  default, byte-identical record ids). Headless + offline; the E2E rides the mock synthetic-keyframe
  fallback, so no decoder is required.

**Exit criteria — met**
- `pipeline_version` in legacy mode is the literal `"vidproc-vlm-v0"` / `"vidproc-mock-v0"` —
  byte-for-byte, asserted. ✅
- All existing video tests in `tests/test_video_pipeline.py` stay green **unmodified** under
  `VIDEO_PIPELINE=keyframe` (15 tests: `ASR_BACKEND=mock VIDEO_PIPELINE=keyframe pytest
  tests/test_video_pipeline.py` → 15 passed). ✅
- An unknown `VIDEO_PIPELINE` resolves to `keyframe` and the graph resolves — asserted at unit and
  E2E level. ✅
- Runbook states, verbatim, each on one line: *rollback restores behaviour, not the corpus*
  (§0/§5); *drain-and-replace, never rolling* (§3); and
  *forward-only cutover at a UTC day boundary on a fresh `user_id` until E-2 lands* (§4). ✅

**Suite:** `ASR_BACKEND=mock ./.venv/bin/python -m pytest -q` → **209 passed** (173 baseline + 36
new); identical under `VIDEO_PIPELINE=keyframe`.

**Not done here (by design):** the flip to `VIDEO_PIPELINE=clip` is gated on WS-B/C/D (the clip
stages must exist), WS-E2 (the registration raise), E-3(a) (serving flags), O-2 (the `ppocr`
default), and — for any `user_id` with existing video records, E-2 (storage retraction). Per the
house rules, `HANDOFF.md` and `CHARTER.md` are untouched; this block is the only edit to this file.
## Build log — WS-F

**Branch** `svc/vc-ws-f`. **Scope delivered** (§8 + §11 WS-F): the observability metric families,
the failure semantics, dialect visibility, `DP_DIALECT_FREEZE`, the blob-sha256 move, and the VLM
circuit-breaker module. *Files touched — WS-F's four owned files, plus one forced test edit:*
`app/main.py`, `app/ingest_core.py`, `app/vision/circuit.py` (new), `tests/test_metrics_video.py`
(new), and `tests/test_ingest_mock.py` (one assertion, see below). No edit to `config.py`,
`pipeline.py`, `processing/base.py`, `stagegraph/**`, or `vision/config.py`.

**What shipped**

- **Parent-side per-unit families** (`ingest_core.py`, in the write loop ~205-216, where `metrics`
  is in scope and null-guarded so they survive `INGEST_ISOLATION=subprocess` — finding #15):
  `dp_units_total{modality,kind}`, `dp_content_chars{modality,kind}` (histogram, bucket edges
  short-caption→full-budget-OCR), `dp_empty_output_total{modality,kind}` (the `modality=="audio"`
  guard is *not* reproduced — it fires for any modality's empty `content.text`; the legacy
  audio-only `dp_vad_empty_total` is left untouched, a distinct signal). They count *durably-written*
  units: a unit whose `/context` POST failed never reaches the accounting. `dp_partial_write_total{modality}`
  fires when a sibling was already durable and a later unit's write blips (caveat A-4).
- **Stage-side families declared at the single site** (`main.py:_setup_metrics`) so WS-B/C/D emit
  against frozen names/labels from day one: `dp_video_parse_fallback_total{pack,step}`,
  `dp_video_truncated_total{pass}`, `dp_video_delta_peak` (hist, edges pinned to the D-04/D-07 class
  thresholds 2/8/40), `dp_video_ocr_events` (hist), `dp_caption_ungrounded_quote_total`,
  `dp_ocr_redactions_total`, `dp_video_scenario_mismatch_total{expected,seen}`, `dp_ocr_frame_errors_total`.
- **Dialect visibility:** `video_pipeline_version` + `dialect_frozen` in `/health`; the pull-time
  gauge `dp_pipeline_dialect{modality,pipeline_version}=1` via `add_gauge_source`; alert
  `count by (modality) (dp_pipeline_dialect) > 1` (the replica-robust form
  `count(count by (modality,pipeline_version) (...)) by (modality) > 1` is what the test evaluates).
- **`DP_DIALECT_FREEZE`:** `_dialect_frozen()` reads env fresh per call (arm/disarm on a live process,
  no redeploy); when set, `_current_pv` returns `None`, so the journal backstop *serves* the stale
  receipt (`journal.processed_record_ids` skips the dialect compare on a `None` current) instead of
  reprocessing under a just-flipped dialect during the drain-and-replace window (D-14).
- **sha256 off the event loop:** `hashlib.sha256(blob)` → `run_in_threadpool(_sha256_hex, blob)`;
  value + terminal-mismatch (502, non-transient) semantics byte-identical (proven by
  `test_blob_integrity` + the corrupt-blob test).
- **VLM circuit breaker** (`app/vision/circuit.py`, new): a shared-per-endpoint `CLOSED`→`OPEN`→HALF_OPEN
  breaker with an injectable *monotonic* clock, tripping on connect-refused only (a 200-with-garbage is
  the parse ladder's problem, never an outage). Inert until a clip stage wires `allow()` before the
  ffmpeg passes (WS-B `clipprep`) and records the HTTP outcome (WS-C/WS-D). Fast-fails at ~0 CPU per
  chunk during an endpoint outage and stops burning the retry budget. HALF_OPEN admits exactly one probe,
  *with a stale-probe self-heal*: a probe whose outcome is never recorded (a consumer that forgot its
  `try/finally`, or died between `allow()` and `record_*`) would otherwise wedge the breaker HALF_OPEN
  forever — a permanent fast-fail *worse* than the outage, so an admitted probe older than one cooldown
  is re-admitted. Bounds the worst-case wedge to one cooldown regardless of consumer bugs.

**One judgement call this build.** The exit line reads *"all new counters visible on `/metrics` at
zero before any traffic"*. A declared-but-never-incremented counter renders **nothing** in this
registry (`metrics.py:180`; `test_metrics.test_empty_histogram_and_counter_emit_nothing`), so
"visible at zero" = "seeded". I seed all four parent-side series per registered modality *and* the
three *unlabelled* stage-side counters (`dp_caption_ungrounded_quote_total`, `dp_ocr_redactions_total`,
`dp_ocr_frame_errors_total`) — a single series each, no label values to guess. The *labelled*
stage-side families and the histograms genuinely cannot be pre-seeded and correctly surface on first
emit. Caveat: under `INGEST_ISOLATION=subprocess` the stage-side increments land in the child and are
blind to the parent, so a seeded stage-side counter reads a permanent parent-side `0` in that mode —
documented at the seed site; on the default in-process path it increments correctly.

**One forced cross-file edit.** Adding `video_pipeline_version`/`dialect_frozen` to `/health` breaks
the exact-`==` dict assertion in `tests/test_ingest_mock.py::test_health_reports_mock_backend`. There
is no additive way around an exact-dict check; the assertion was updated to the new shape. No other
workstream edits that file, so this creates no merge surface. `/health` is a liveness probe, not a
frozen contract.

**WS-H coordination (open).** `dp_caption_ungrounded_quote_total` is declared here with the name from
§8. The Decision addendum widens the *scorer* from double-quoted spans to all named ≥4-char strings;
WS-H owns that scorer and the counter's increment site. If WS-H forks the counter **name** for the
widened metric, change it in one place (`main.py:_setup_metrics`) and here — coordinate before the
pilot so a rename doesn't split the series.

**WS-C coordination (open) — `dp_video_ocr_events` type.** §8 annotates only `dp_video_delta_peak` as
`(histogram)`; `dp_video_ocr_events` is listed bare, so its type is a WS-F choice. I declared it a
**histogram** (events-per-chunk distribution, parallel to `delta_peak`). This registry is
first-declaration-wins, and `_setup_metrics` runs at construction, so WS-C's emit `MUST` use
`metrics.observe(...)`, not `metrics.inc(...)` — an `inc()` against a histogram family writes an unread
slot and is silently swallowed. If WS-C actually wants a running counter, change the declaration here to
a counter and emit with `inc()`. Flagged; no in-tree emitter exists yet, so nothing is firing today.

**Adversarial review.** A four-lens fan-out (byte-identical / metrics-shape / failure-semantics /
spec-fidelity), each finding independently verified, returned **0 confirmed defects**. Two low findings
were refuted as latent-not-firing (no in-tree emitter / no consumer), but one — a HALF_OPEN wedge if a
consumer never records its probe, was worth hardening against regardless: hence the stale-probe
self-heal above (`test_circuit_half_open_stale_probe_self_heals`). The `dp_video_ocr_events` type note
above is the other.

**Exit criteria — each proven in `tests/test_metrics_video.py`:**
- all new counters visible at zero before traffic → `test_new_counters_visible_at_zero_before_any_traffic`
  (parent-side + the three unlabelled stage-side; labelled/histograms asserted absent-until-emit);
- `/health` reports the dialect → `test_health_reports_video_dialect_and_freeze_off_by_default`,
  `test_health_reflects_dialect_freeze_flag`;
- a two-dialect fixture trips the alert → `test_two_dialect_fleet_trips_the_alert_expression`
  (+ `test_single_replica_never_fires_the_alert`);
- a graph run with `resources=None` (the isolation shape) does not raise →
  `test_video_graph_run_with_resources_none_does_not_raise` + `..._process_is_the_child_entry...`;
- plus: parent-side per-unit accounting, partial-write vs full-failure, the freeze serve-vs-reprocess
  behaviour end-to-end (a redelivery over a shared journal after the video `pipeline_version` is bumped:
  freeze serves the stale receipt with no new writes, no-freeze reprocesses and forks the ids),
  the offloaded-sha256 rejection, and the full circuit-breaker state machine (incl. the self-heal).

**Additive-only proof.** The default (audio) record path is byte-identical — metrics are a side
channel and the sha256 move is value-preserving. **DP suite: 193 passed** (was 173; +20 in
`test_metrics_video.py`, +6/-2 lines in the `test_ingest_mock.py` `/health` assertion), run as
`ASR_BACKEND=mock ./.venv/bin/python -m pytest -q`.
## Build log — WS-B (frame prep & the delta gate)

**Scope delivered:** D-03 sampling operating point, D-04 two ffmpeg passes + true-PTS + the
binarized 32×32 change map + the anchor accumulator, D-07's selection half (floor grid ∪
change events, rank-free cap, chunk-local), the deterministic caption frame count, and the
`clipprep` stage. **Suite: 219 passed** (173 baseline untouched + 46 new).

**Files created (only these):** `app/vision/clip.py`, `app/vision/delta.py`,
`app/stages/video/clipprep.py`, `scripts/calibrate_delta.py`, `tests/conftest_video.py`,
`tests/test_clipprep.py`, `tests/test_delta.py`. Imports `Frame`/`DeltaCell`/`Delta`/
`ClipFrames` from `clip_types.py` and `resolve_pipeline` from `mode.py`; never touched
`result.py`, `config.py`, or any shared core (per the lead correction — the clip shapes live
in the committed `clip_types.py`, not a WS-B-owned `result.py`).

**Exit criteria — status (all met):**
- Floor **exactly 2** on flat black/white/gray — asserted. Verified empirically that the `2`
  is an artefact of `scale=32:32:flags=area` (identical under lossless ffv1, so scaler not
  codec) and stable across the ~1.15–2.3 Mpx band (1 below 1280×800, 3 above 1920×1200);
  fixtures pinned to *1440×900*, solidly mid-band. The floor assertion's failure message
  names the ffmpeg-build cause (the design pins ffmpeg, §8 A-2) since clipprep never
  post-processes the value.
- The six §D-04 calibration vectors reproduce as content classes (idle / typing / layout /
  switch) with the two exact anchors — floor 2 and app-switch 255/1024. Exact real-footage
  magnitudes (typing 11–19 etc.) are O-1 measurements on real capture, not reproducible from
  synthetic lavfi; each fixture's own measured signature is recorded beside its builder, and a
  dedicated test proves the D-04 mechanism claim (a whole-frame mean is blind to typing —
  measured 0, while binarize-then-max recovers it at 18–32).
- Two runs over one fixture → byte-identical `ClipFrames` + `Delta` — asserted (hashes both
  JPEG renditions + the full `Delta`; holds even across a fresh libx264 rebuild).
- Requesting a frame the stream lacks `RAISES` (`FrameCountError`) — asserted at the Pass-B
  guard AND at the stage level (never masked by the synthetic fallback).
- `ffprobe` and `scene` appear nowhere in the clip source — grep-asserted (production files +
  the fixture builders).
- ffmpeg-absent under a non-mock backend raises; under mock → synthetic fallback — asserted.
- `calibrate_delta.py` prints the peak/spread histograms + events/chunk per candidate
  `VIDEO_OCR_IDLE_PEAK` — asserted by a smoke test.
- Fixtures generated at test time via `ffmpeg lavfi`; NO binaries committed.

**Frame selection is by exact presentation timestamp, not a rate-derived index.** An initial
`eq(n, round(t·fps))` model (fps from `duration_time`) was replaced after an adversarial review
(below) confirmed it is a **CFR poison-pill**: on variable-frame-rate capture (macOS
ScreenCaptureKit emits frames only on change) mp4 pins a constant timebase, so `duration_time`
reports the tick — not the real inter-frame gap, and `round(t·fps)` lands far past the last real
frame, dead-lettering a benign chunk on every backend (reproduced end to end). Pass A now
recovers each analysis frame's integer `pts`; Pass B re-selects those exact frames by
`eq(pts,P)` (+ `eq(n,0)` for the opening), so *every requested frame provably exists* — VFR and
short-media degrade to fewer frames instead of dead-lettering, and the guard fires only on a
genuine decoder anomaly. No rate is reconstructed, so 29.97/23.976 drift is gone too. A VFR
regression fixture pins this. The caption still takes a span-driven count `K =
clamp(ceil(span/2.5),2,12)` — now K frames evenly spaced across the frames the delta pass
decoded, rather than at exact grid times.

**Deviation — `clipprep` order (for lead/WS-G at integration):** §2.1 assigns `order 0`, but
legacy `keyframes` still holds `order 0` and `register_stage` enforces per-modality order
uniqueness unconditionally (`stage.py:260-266`), so `order 0` fails discovery and reddens the
suite on this pre-integration branch. `order` is behaviourally inert for a unit-less sidecar
(execution is needs-driven; order only sequences emitted-unit assembly), so it is registered at
**order 5** (any value ∉ {0,10}). WS-C's `screentext` (design order 10) hits the identical
collision with `captions` (10) on its own branch — integration should renumber the legacy pair
(freeing 0/10/20 for the clip cohort), OR relax the order check to per-enabled-cohort (WS-E2's
file), at which point `clipprep` returns to 0.

**Integration boundary:** clip-mode END-TO-END needs WS-G (the legacy `enabled()` gate — the
current `keyframes`/`captions` have none, so in clip mode they stay enabled and `keyframes`
double-provides `vision_settings` → resolve() raises a slot-owner error) and WS-D (the `clipcap`
primary). On the isolated WS-B branch clip-mode graph resolution therefore cannot run by design;
tests drive `clipprep` and the delta functions directly (as instructed). The default keyframe
graph is byte-unchanged (`vidproc-mock-v0`).

**Config shim (no WS-D dependency):** every `VIDEO_CLIP_*` / `VIDEO_OCR_*` / `VIDEO_ANALYSIS_*`
knob is read via a local `os.getenv` helper in `clip.py`, each marked
`# TEMP -> VisionSettings (WS-D owns config.py)`. The `vision_settings` slot is a
`ClipVisionSettings` bundling the shipped base `VisionSettings` (read, not edited) with the clip
knobs; attribute reads resolve base fields then clip knobs, so both read at the TOP level
(`vs.backend`, `vs.seconds_per_frame`) — exactly the shape WS-D's folded `VisionSettings` will
expose. Selector defaults pinned to §D-07/§8: `IDLE_PEAK=8`, `LAYOUT_PEAK=40`, `MAX_EVENTS=3`,
`FLOOR_S=120`; binarize threshold 24, spread cell threshold 13.

**Adversarial review (17-agent, 5 dimensions × adversarial verify):** 14 findings, 11 confirmed,
all addressed — the CFR poison-pill (fixed by PTS selection), fractional-fps drift (same fix, no
rate), the `ClipVisionSettings` top-level seam (now delegates), the caret vector's mechanism
(relabelled honestly, it aliases against the sample period), and added tests (VFR regression,
stage-level FrameCountError-not-masked, `calibrate_delta` smoke). Remaining accepted edges: the
anchor floor-guard (`ANCHOR_FLOOR_GUARD=2`, tied to the 1728-capture floor; wider native blobs
want +1 — O-1); `dp_video_delta_peak` / `dp_video_ocr_events` (§8) are populated from the
provided `Delta`, with WS-F owning the metric emission.

---

## Build log — WS-C (sidecar)

Tab **C1** (the OCR sidecar service). Files added: **`sidecars/ocr/**` only** — the new deployable
(`app.py`, `run.sh`, `requirements.txt`, `README.md`, its own venv), the O-2 bake-off harness
(`bakeoff/`), and the sidecar's own tests. *Zero shared-core edits; zero DP-venv changes.* C2's
`app/vision/ocr/**` + `screentext.py` + `tests/fixtures/ocr_truth/**` are untouched (they were created
in parallel in this shared worktree and are C2's to commit). DP suite: *173 passed* (its separate
venv is unchanged — confirmed nothing broke).

### Frozen wire contract (C2 codes against `sidecars/ocr/README.md`, not the code)
- `POST /ocr {image:<base64-jpeg>}` → `{regions:[{text,bbox:[x0,y0,x1,y1],conf}], engine, model_sha_det, model_sha_rec}`
- `GET /health` → `{ok, model_sha_det, model_sha_rec, ort_version, ep}` (+ additive `engine`)
- Default endpoint `http://127.0.0.1:8091` (`OCR_PORT=8091`). The sidecar returns **raw, unfiltered**
  regions — no conf gate, no min-length, no dedup, no redaction, no role, because all of D-07's
  post-processing (role assignment, redaction, dedup, budget) is *DP-side (C2)*.

### §11 WS-C exit criteria — sidecar half (PASS/FAIL)
- `mock` backend needs **no network / no GPU / no new DP dependency** — *`PASS`.* The HTTP layer is
  Python-stdlib only; proven (subprocess audit) that the mock path imports zero third-party modules;
  `run.sh` serves mock on a bare system `python3`.
- `/health` returns **both model-file sha256 + ORT version + EP** — `PASS` (real 64-hex det/rec shas,
  `ort 1.27.0`, `CPUExecutionProvider`). *DP asserting them against config at graph resolution is C2's
  half; the sidecar supplies the values.*
- `VIDEO_OCR_BACKEND` unknown → `off` in both resolvers — **C2's half** (DP-side `select()`/
  `version_tag()`). The sidecar's own `OCR_MODE` fails loud on an unknown value.
- redaction 6 cases / rendered text no `\n` / exactly one `kind='ocr'` unit / per-frame error absorbed,
  >50 % raises — **C2's half** (the DP seam + `assemble.py`); the sidecar is the dumb specialist below it.
- **O-2 bake-off report committed** — `PASS`: `sidecars/ocr/bakeoff/REPORT.md`.

Sidecar tests: `sidecars/ocr/test_app.py` (8 passed, mock wire + errors + zero-dep proof; ppocr engine
verified in its venv) and `bakeoff/test_score.py` (8 passed, lenient scorer).

### O-2 verdict (this gates the pilot)
Measured on a **204-frame synthetic-proxy** corpus (a headless build cannot capture 200 real macOS
frames; the corpus is deterministic macOS-UI mocks at 13 pt-equivalent through a **real x264 CRF-28**
encode, exact ground truth). Scored on ≥5-char key-string recall with *lenient substring* matching
(+ CER on the focused region).

- **ppocr@1728: key-string recall 0.988 (micro/key-pooled substring — the gate metric) / 1.000 fuzzy /
  CER 0.070 → clears the ≥0.85/≤0.10 gate on the proxy.**
- ppocr@1152: recall 0.869 (micro) / CER 0.074 — passes but weaker (sheet recall 0.769, terminal CER
  0.277). Note per-archetype at 1728, browser (CER 0.115) and slack (0.113) breach the CER half while
  the aggregate (0.070) passes.
- **CRF-28 codec cost ≈ 0** at 1728 px (raw vs CRF-28 substring recall 0.981→0.983): resolution, not the
  encoder, is the lever — direct support for `VIDEO_OCR_FRAME_WIDTH=1728` (no resample). Measured OCR
  cost ~0.93 s/frame (above the design's 0.6 s §7.1 assumption).
- VLM arms (Qwen3-VL-32B / Qwen2.5-VL-32B) **not run** — need the GPU endpoint (E-3(a)).

**The O-2 gate is defined over real frames and is NOT satisfied by a synthetic proxy** (which is likely
optimistic: Liberation fonts ≠ CoreText/SF Pro, no real screen noise). **Verdict: ship
`VIDEO_OCR_BACKEND=mock` as the production default** (matches O-2's own recommendation). The `ppocr`
backend is built, wired and validated; flipping it to the production default is gated on running this
same harness — which accepts real frames with *zero code change*, over 200 hand-labelled real macOS
frames and clearing ≥0.85 recall / ≤0.10 CER. Engine shipped is *PP-OCRv4* (rapidocr-onnxruntime ships
v4; the design names v6) — file-swappable via `OCR_DET_MODEL`/`OCR_REC_MODEL`, `/health` re-hashes.

---

## Build log — WS-C (seam)

Tab **C2** (the DP-side OCR seam). Files added (owned): `app/vision/ocr/**` (`__init__.py`,
`config.py`, `redact.py`, `assemble.py`, `mock.py`, `ppocr.py`, `vlm.py`), `app/stages/video/screentext.py`,
`tests/test_ocr_assemble.py`, `tests/test_screentext.py`, `tests/fixtures/ocr_truth/**`, and this build-log
section. **Zero shared-core edits** (`main.py`/`ingest_core.py`/`pipeline.py`/`processing/base.py`/`stagegraph/**`/
`vision/config.py` untouched). *No new DP dependency* (httpx is already a base dep). *DP suite: 220 passed*
(was 173 — the OCR seam is dormant on this branch, see the registration note).

### Exit criteria (§11 → WS-C, C2 half) — PASS

| # | criterion | evidence |
|---|---|---|
| 1 | `mock` backend green with `VIDEO_OCR_BACKEND=mock`, no new DP dep | default backend is `mock`; 220 passed; `requirements.txt` unchanged |
| 2 | DP asserts sidecar `/health` shas vs config, fails loud on mismatch | `ocr.assert_health` → `ppocr.assert_health` (per-process cached), called at the top of `screentext.run_sync` before any read/record; `test_ppocr_assert_health_*`, `test_ppocr_health_mismatch_fails_the_stage` |
| 3 | 6 redaction cases → `[redacted:secret]` + counter | `redact.py`; `test_redaction_each_secret_case` (AWS/`sk-`/`ghp_`/base64/PEM/Luhn); `dp_ocr_redactions_total` via `test_redaction_counter_incremented` |
| 4 | rendered text contains no `\n` | `assemble._normalize_text` collapses all whitespace; asserted in `test_render_is_single_line_*` + every `_assert_single_ocr_unit` |
| 5 | exactly one `kind='ocr'` unit always, `discriminator="ocr"`, `t_start=None`, empty when nothing legible | `screentext.run_sync` emits one unit unconditionally; `test_*` for mock/off/no-frames/unknown (off & no-frames → `""`) |
| 6 | per-frame error absorbed + counted, `>50%` raises | `test_minority_frame_errors_absorbed_and_counted` (`dp_ocr_frame_errors_total`), `test_majority_frame_errors_raises` |
| 7 | unknown backend → `off` in both `select()` and `version_tag()` | `test_unknown_backend_is_off_in_both_resolvers` |
| 8 | stage disabled-by-default (clip mode) | `enabled() = resolve_pipeline()=="clip"`, default `keyframe`; `test_enabled_only_in_clip_mode`; video discovery stays `[keyframes, captions]` |

### Decisions worth flagging

- **`off` carries a NON-Empty fragment (`+ocr-off-v1`).** Deliberate divergence from the `diarize`
  precedent (a mutate, `off`→`""`). `screentext` is a *fragment-bearing sidecar that feeds the caption*
  (`provides=("ocr_text",)`, always enabled in clip mode), so §4.3 *R1* requires a non-empty fragment in
  Every enabled config — `off`/unknown included. `off` is an honest dialect ("OCR configured off for this
  chunk", A-10), not stage-disablement. *WS-E's law-as-test must accept this as correct, not flag it.*
- **Registration is sibling-gated.** The frozen `needs=("clipprep",)` would fail the stage-discovery
  existence check on a WS-C-only branch (clipprep is WS-B's, absent here) and take the whole suite red.
  `screentext.py` auto-registers only when `importlib.util.find_spec(".clipprep")` resolves — so it stays
  dormant/unregistered here (unit tests drive the class directly) and auto-wires the instant clipprep lands.
  The frozen wiring (name/kind/policy/order/needs/provides) is declared exactly; only registration is gated.
  Edits only `screentext.py` — no sibling file, no merge surface.
- **Health assertion timing.** `executor.resolve()` is WS-E2's shared-core file (not editable here), so
  the `/health` sha assertion runs at the *start* of `screentext.run_sync` — before any read and before
  assembly. A mismatch fails the chunk loud with nothing written, the corpus-safety guarantee D-06 asks for.
- **Config shim.** `app/vision/ocr/config.py` holds the `VIDEO_OCR_*` knobs (each marked
  `# TEMP -> VisionSettings (WS-D)`); `ocr_cap`/`truncate_word` are stubbed locally in `assemble.py`
  (D-11's 6 chars/s OCR share). `select()`/`version_tag()` key off `cfg.ocr_backend`, name-compatible with
  the future `VisionSettings.ocr_backend`, so the migration is a lift-and-shift.
- **Wire-contract reconciliation vs `sidecars/ocr/README.md` (C1).** Matched on `POST /ocr`, `GET /health`,
  bbox-in-pixels (normalized DP-side via a stdlib JPEG-dims parse + 16:10 fallback), and per-frame `500`
  semantics. *Fixed one drift:* DP default `VIDEO_OCR_URL` was `:8089` → aligned to C1's frozen
  `OCR_PORT=8091`.

### Adversarial review

Ran a 7-dimension multi-agent review (exit-criteria · record-law · determinism · redaction · wire-contract ·
integration-safety · quality) with per-finding adversarial verification. 10 raw findings → **2 confirmed,
both fixed:** (a) the `vlm` arm mapped an off-vocab/missing model role to `""` with a zero bbox, which
`assign_role` labelled `titlebar` (cy=0 band) instead of `main` → added a degenerate-bbox guard in
`assign_role`; (b) the masked-field rule `[•●·*]{3,}` over-redacted markdown (`***bold***`, `/*****/`) and
double-counted → split into `[•●]{3,}` + a standalone `\*{6,}`. A follow-on `vlm` test then caught a
`.format()` crash on the literal-JSON-brace prompt → switched to `.replace`. All three locked with tests.

### For C3 / the lead

- Full clip-mode E2E (`select → POST /ocr → assemble → emit`) is C3's; once WS-B (`clipprep`) + WS-D
  (`clipcap`) land, `screentext` auto-registers and the graph resolves (the fragment composes — the dialect
  carries `+ocr-mock-v1`).
- **O-2** (the `ppocr` production-default gate) is C1's deliverable; DP ships `VIDEO_OCR_BACKEND=mock` by
  default. The `ppocr` client is built and validated against the frozen contract; flipping the default is
  gated on O-2, not on this seam.
- **WS-D** should migrate the config shim + local budget stubs into `VisionSettings`/`budget.py`, and add the
  OCR knobs (`ocr_backend`, `ocr_ep`, `ocr_model_sha_det`/`_rec`, …) to `OUTPUT_AFFECTING` so `cfg_tag` forks
  on the precise model shas (this seam's coarse `+ocr-ppv4-cpu-v1` fragment is the human token only).

---

## Build log — WS-C (consolidation)

Tab **C3** — closes WS-C end to end after C1 (sidecar) and C2 (seam) in this same worktree. One file
added, owned by nobody else: `tests/test_screentext_integration.py` — the real-sidecar end-to-end
integration test the C3 prompt / §11 requires. **Zero shared-core edits, zero edits to C1's
`sidecars/ocr/**` or C2's `app/vision/ocr/**` / `app/stages/video/screentext.py`** (no drift needed
fixing — see reconciliation below; the source is left exactly as the two build workers committed it).

- **DP suite: 225 passed** (`ASR_BACKEND=mock ./.venv/bin/python -m pytest -q`) — 220 after C2 + 5 new
  integration tests. The *≥173 gate holds with wide margin*; the OCR seam is present-but-dormant on
  this branch (clip-mode default is `keyframe`; `screentext` auto-registers only once WS-B `clipprep`
  lands), so the legacy graph is unaffected.
- **Sidecar tests** (`sidecars/ocr/`, C1's separate venv): *17 passed, 1 skipped* — the skip is the
  `ppocr`-Engine test (no ONNX model venv in this headless box; O-2 ran the real engine elsewhere and
  committed `bakeoff/REPORT.md`). The mock + wire + scorer tests all pass on a bare `python3`.

### §11 WS-C exit criteria — PASS / FAIL (the full list)

| # | criterion | verdict | evidence |
|---|---|---|---|
| 1 | `mock` needs no network / no GPU / no new DP dep; full DP suite green with `VIDEO_OCR_BACKEND=mock` | `PASS` | default backend `mock` (`ocr/config.py:85`); `ocr/mock.py` `make_client`→`None` (reaches no socket) and keys off `(chunk_id, frame.index)` only; `requirements.txt` unchanged (httpx already a base dep); suite → **225 passed** |
| 2 | `/health` returns both model shas + ORT version + EP; DP asserts them vs config **at graph resolution**, fails loud on mismatch | **`PASS` in substance — ⚠ placement flagged (L‑1)** | sidecar `/health` → `{ok, model_sha_det, model_sha_rec, ort_version, ep, engine}` (real 64‑hex shas + `ort 1.27.0` + `CPUExecutionProvider` in ppocr; sentinel `"mock"` / `null` in mock) — verified over the real wire (`test_sidecar_health_shape_over_the_wire`) and by C1 `test_app.py`. DP `ocr.assert_health → ppocr.assert_health` **`RAISES` on mismatch** (cached per‑process); proven over the real socket by `test_pinned_sha_mismatch_fails_loud_over_the_real_wire`, and via MockTransport by `test_ppocr_health_mismatch_fails_the_stage` / `test_ppocr_assert_health_raises_on_sha_mismatch`. *The assertion runs at `screentext.run_sync`'s first line — before any read / assemble / emit — not literally inside `executor.resolve()`; see flag L‑1.* |
| 3 | Redaction: 6 cases (AWS key, `sk-`, `ghp_`, base64 blob, PEM header, Luhn card) → `[redacted:secret]`, counter incremented | `PASS` | `ocr/redact.py`; `test_redaction_each_secret_case` (all 6, exactly-once) + `test_render_redacts_and_counts_through_the_pipeline`; through-stage counter `dp_ocr_redactions_total` via `test_redaction_counter_incremented` |
| 4 | Rendered text contains **no `\n`** — asserted | `PASS` | `assemble._normalize_text` collapses all whitespace incl. `\n`/`\r`, re-applied belt-and-braces in `render`; `test_render_is_single_line_even_with_embedded_newlines` + every `_assert_single_ocr_unit` (C2) / `_assert_frozen_ocr_shape` (C3) |
| 5 | Exactly one `kind='ocr'` unit always — `discriminator="ocr"`, `t_start=None`; `content.text==""` when nothing legible | `PASS` | `screentext.run_sync` emits one unit unconditionally; C2: `test_mock_backend_emits_one_ocr_unit_with_text`, `test_off_backend_still_emits_one_empty_ocr_unit`, `test_no_ocr_frames_still_emits_one_empty_unit`, `test_unknown_backend_behaves_like_off`; C3 over the real wire: `test_ppocr_over_real_sidecar_emits_one_ocr_record`, `…_through_the_executor` |
| 6 | Per-frame OCR error absorbed + counted; **>50% erroring `RAISES`** | `PASS` | `test_minority_frame_errors_absorbed_and_counted` (`dp_ocr_frame_errors_total`), `test_majority_frame_errors_raises` |
| 7 | `VIDEO_OCR_BACKEND` unknown → `off` in **both** `select()` and `version_tag()` — asserted | `PASS` | single `_resolve` drives both; `test_unknown_backend_is_off_in_both_resolvers` (select→`None`, version_tag→`+ocr-off-v1`, non-empty per R1), `test_version_fragment_tracks_backend` |
| 8 | **O-2 bake-off report committed** (WS-C is the workstream that produces it) | `PASS` | `sidecars/ocr/bakeoff/REPORT.md` + harness (`run_bakeoff.py`, `score.py`, `gen_corpus.py`, `ablate_crf.py`, `ground_truth.json`, `results.json`) + `bakeoff/test_score.py` (scorer green). Verdict recorded below |

**All 8 exit criteria `PASS`.** Criterion 2 passes in substance (fail-loud-before-corpus, proven over
the real socket) with a placement caveat the lead must ratify — flag **L‑1** below.

### The end-to-end integration test (the C3 deliverable)

`tests/test_screentext_integration.py` boots the **real** co-located sidecar (`sidecars/ocr/app.py`)
as a subprocess in `OCR_MODE=mock` on a free loopback port, with a **cleaned environment** (only
`OCR_*` + `PATH`) so the wire cannot lean on DP's venv, then drives a fixture chunk through the full
`ppocr` path — *`select → POST /ocr` (real HTTP) `→ assemble → emit`*, twice: once directly
(`run_sync`) and once through the real executor (`resolve` + `run_graph`) alongside a fake primary.
It asserts exactly one `kind='ocr'` record with the *frozen shape* (`discriminator="ocr"`,
`t_start=None`, `t_end=None`, no `\n`), that the provided `ocr_text` slot equals the record text (one
witness, two channels — §4 R2 Corollary 2), and that real OCR text actually crossed the wire
(non-empty, self-anchored `+0s …`). Headless: the sidecar mock is stdlib-only; JPEG frames are
synthesized in-process (valid SOI+SOF0 header so the DP-side `_jpeg_dims` parser reads real dims; the
mock engine ignores pixels). *No binary committed.* 5 tests, ~0.6 s wall.

### Wire-contract reconciliation (C1 `sidecars/ocr/README.md` ↔ C2 `ppocr.py` client)

Traced field-by-field; **the contract and the client agree — no drift remains to fix**:
- `POST /ocr` request `{image:<base64-jpeg>}` — client sends exactly this (`ppocr.read`).
- `POST /ocr` response `{regions:[{text,bbox,conf}], engine, model_sha_det, model_sha_rec}` — client
  reads `regions[].{text,bbox,conf}`; the echo `engine`/`model_sha_*` are deliberately **not** re-checked
  per-response (the sha guard lives at `/health`), which is correct, not a gap.
- `bbox` is pixels of the submitted image → client normalizes to `[0,1]` against parsed JPEG dims (16:10
  fallback). `conf∈[0,1]` → confidence gate. Consistent.
- `GET /health` `{ok, model_sha_det, model_sha_rec, ort_version, ep, engine}` — client asserts `ok` +
  the two shas; `ort_version`/`ep` are consumed by WS-D's `cfg_tag`, not the seam (by design).
- Per-frame `500` → client `raise_for_status()` → stage absorbs + counts, `>50%` raises. Matches the
  README's stated DP behaviour.
- **Endpoint:** default `http://127.0.0.1:8091` on both sides. The one historical drift (DP's original
  `:8089`) was already fixed by C2; confirmed reconciled — `grep 8089` over `app/`, `tests/`, `sidecars/`
  is empty.

### Confirmation — `/health` sha assertion timing (criterion 2 detail)

The sha gate **is** wired and **does** fail loud (proven over the real socket). Where it runs is the
subtlety: it fires at the *first line of `screentext.run_sync`*, before any `read`, `assemble` or `emit`,
cached per-process. It is *not* literally inside `executor.resolve()` because (a) `resolve()` is WS‑E2's
shared-core file WS‑C may not edit, and (b) — the substantive reason, *`resolve()` must stay a pure,
network-free function*: it (and the `version_fragment()`/`enabled()` it calls) run at `ACCEPT` for every
ingest, on the dedup fast-path and on redrive (`main.py:358`, `:204`, `:279`). An HTTP round-trip there
would add latency to every accept, make the record dialect depend on the sidecar being reachable, and 500
every video accept during a sidecar blip instead of failing only the chunk in flight. The run-entry
placement delivers exactly D‑06's teeth — *"fails loudly … not silently in the corpus"*, because a
mismatch raises before a single record byte exists. *Verdict: the exit guarantee is met; the literal
phrase "at graph resolution" needs the lead's ratification — flag L‑1.*

### O-2 verdict — does the `ppocr` production default hold? **No — DP ships `VIDEO_OCR_BACKEND=mock`.**

Per `sidecars/ocr/bakeoff/REPORT.md`: the PP‑OCR det+rec ONNX the sidecar serves clears the gate **on a
204‑frame synthetic macOS-UI proxy through a real x264 CRF‑28 encode** — key-string recall **0.988**
(micro, lenient substring, the gate metric), CER *0.070* at 1728 px (`@1152` is weaker: 0.869 / 0.074,
and two of six archetypes — browser, slack, breach the CER half per-archetype even at 1728). *But the
O‑2 gate is defined over ~200 hand-labelled real macOS frames*, which a headless build cannot capture,
and the proxy is likely optimistic (Liberation fonts ≠ SF Pro/CoreText, no real screen noise). *The
gate is therefore NOT satisfied.*

> **Decision (matches O‑2's own recommendation and the WS‑C exit criteria): ship
> `VIDEO_OCR_BACKEND=mock` as the production default.** The `ppocr` backend is built, wired and
> validated against the frozen contract end to end; flipping the default to `ppocr@1728` is gated on
> re-running the *same* harness (it accepts real frames with **zero code change** — drop labelled PNGs +
> a truth spreadsheet) over the real-frame corpus and clearing **≥0.85 micro key-string recall and
> ≤0.10 focus CER**. Codec cost at 1728 px is ≈0 (raw vs CRF‑28 recall 0.981→0.983): resolution, not
> the encoder, is the lever — direct support for `VIDEO_OCR_FRAME_WIDTH=1728` (no resample). The engine
> shipped is **PP‑OCRv4** (what `rapidocr-onnxruntime` bundles; the design names v6) — file-swappable via
> `OCR_DET_MODEL`/`OCR_REC_MODEL`, `/health` re-hashes. VLM A/B arms were not run (need the GPU endpoint,
> E‑3(a)).

### Flags for the lead (do NOT self-reconcile — HANDOFF.md / CHARTER.md untouched)

- **L‑1 — "at graph resolution" wording vs. placement.** The `/health` sha assertion is implemented at
  `screentext.run_sync` entry (before any read/assemble/emit, cached per-process), not inside
  `executor.resolve()`. This delivers D‑06's corpus-safety guarantee but not the literal phrase. Two
  clean resolutions, lead's call: *(a, recommended)* ratify run-entry as satisfying D‑06 and soften the
  D‑06 / §11 wording to *"at stage entry, before any read — cached per process"* (resolve() must stay
  pure/network-free, so this is the architecturally correct home); or *(b)* have *WS‑E2* (already
  sequenced to touch `stage.py`/the resolver last) add an *optional, lazy, cached* resolution-time health
  hook, explicitly NOT a per-accept network call.
- **L‑2 — production default is `mock`, by O‑2 (not a defect).** `VIDEO_OCR_BACKEND=mock` ships as the
  pilot default; `ppocr@1728` is validated but gated on the real-frame O‑2 run. The lead should schedule
  the ~200-frame real-macOS capture + re-run as the single remaining step before the OCR channel carries
  real text in the pilot. Until then, per §4.3 R3(e): *continuum must never infer "no on-screen text"
  from an absent/empty OCR record* — the record is always present; its emptiness under `mock`/`off` is a
  dialect fact, not a claim about the user's screen.
- **L‑3 — engine is PP‑OCRv4, design names PP‑OCRv6.** `rapidocr-onnxruntime 1.4.4` bundles v4; the seam
  is model-agnostic (file swap + `/health` re-hash + the coarse `+ocr-ppv6-cpu-v1` human token). The lead
  should decide whether to source a real v5/v6 det+rec ONNX pair for the O‑2 real-frame run or accept v4
  as the pilot engine and correct the `-ppv6-` token. Not blocking; a naming/provenance reconciliation.
- **L‑4 — cross-workstream hand-offs already noted by C2 stand:** WS‑D folds the OCR knobs
  (`ocr_backend`, `ocr_ep`, `ocr_model_sha_det`/`_rec`, …) into `OUTPUT_AFFECTING` so `cfg_tag` forks on
  the precise shas (the seam's `+ocr-ppv6-cpu-v1` fragment is the coarse human token only), and migrates
  the `ocr/config.py` shim + local `ocr_cap`/`truncate_word` budget stubs into
  `VisionSettings`/`app/vision/budget.py`. WS‑E2's registration-time R1 raise must *accept* `screentext`'s
  non-empty `off` fragment (`+ocr-off-v1`) as correct (a fragment-bearing sidecar that feeds the caption),
  not flag it. Both are downstream-workstream tasks, not WS‑C defects.

---

## Build log — WS-C (seam) — post-review fixes (lead review, 2026-07-24)

The lead's review found a **masked wiring bug** the consolidation missed (its E2E test hand-lists the
stages, bypassing the registry). Four fixes, all in `app/stages/video/screentext.py` + tests (plus the
provenance rename in `app/vision/ocr/__init__.py`):

1. **`screentext` order `10 → 15`.** Order 10 *collided* with the retained legacy `captions` stage
   (also order 10). The locked clip band is `clipprep=5`, `screentext=15`, `clipcap=20`.
2. **Standard unconditional `@register_stage`** — deleted the `_sibling_present("clipprep")` guard and
   helper. The guard *masked* the order-10 collision: screentext never registered on a clipprep-absent
   branch, so the collision never fired. Unconditional registration makes the stage statically
   discoverable and surfaces any order/needs error loudly.
3. **Real-registry discovery test** (`test_screentext_registered_and_wired_in_real_registry`) — asserts,
   over `_discover()` + `stages_for("video")` (NOT a hand-built list), that `screentext` is registered at
   order 15 with no duplicate orders, and (when the clip graph is coherent) that clip-mode `resolve()`
   wires it under one primary. Gated to skip when `clipprep` is absent.
4. **`version_tag` ppocr `+ocr-ppv6-cpu-v1 → +ocr-ppv4-cpu-v1`** — provenance honesty: the sidecar ships
   PP-OCRv4 (rapidocr default), not the design's aspirational v6. This is the rename the consolidation
   section above already flagged as needed. Updated the three asserting tests, incl. the consolidation's
   `test_screentext_integration.py` (one line). The design-doc §2.1/§2 tokens (v6 = target) are the lead's
   design-of-record and are left untouched; a real v6 file-swap re-keys via the precise shas in `cfg_tag`.

**Suite state (honest):**
- **Integration base** (clipprep + clipcap present — verified this session with throwaway WS-B/WS-D stubs,
  and the clip-mode resolve additionally verified with WS-G's `captions`/`keyframes` gate simulated):
  *226 passed, 0 failed*; discovery lists `keyframes(0), clipprep(5), captions(10), screentext(15),
  clipcap(20)` — screentext at 15, no collision; clip-mode resolve → one primary (`clipcap`) + screentext
  wired, dialect carries `+ocr-mock-v1`.
- **This isolated branch** (clipprep absent): unconditional registration makes `_discover()` raise
  `needs unknown stage 'clipprep'`, so *3 discovery-triggering tests fail* (subprocess-isolation +
  first-ingest) — *expected per the lead's note, and correct against the integration base.* Every WS-C
  unit test passes (the real-discovery test skips; 47 seam tests green). This trades the previous
  green-on-isolated-branch (which the sibling-gate bought by *hiding* the stage) for a statically-correct,
  collision-free registration on the integration base — the lead's explicit call.

---

## Build log — WS-D (integration · resync onto svc/video-clip)

Tab **D3** again, after the lead put WS-A/B/C/F/G on `svc/video-clip`. Merged that into
`svc/vc-ws-d`, reconciled the config seam, landed the `HELD` `clipcap` primary, and lit clip mode
up end to end. **Suite: 465 passed** (was 304 WS-D-only; +155 from WS-A/B/C/F/G, +6 new clip-E2E).
Clean code merge — WS-D touches none of clipprep/screentext/captions/keyframes/main; only
`handoff/ws-video-clip.md` conflicted and was resolved by keeping ALL build-log blocks (WS-A…G).

**1 — Merge.** `git merge svc/video-clip`. D1's `config.py`/`mode.py`/`clip_types.py` survive
unchanged (that branch never touched them), so the real `VisionSettings` wins and WS-B/WS-C's local
shims arrive on their own files, reconciled next.

**2 — Config seam (I own `config.py`; one 1-line fix in WS-C's `ocr/config.py`).** Grepped every
`VIDEO_*` read in `clipprep`/`screentext`/`clip.py`/`ocr/`. All output-affecting knobs already map to
classified `VisionSettings` fields — including `ocr_model` (OUTPUT_AFFECTING) / `ocr_api_key`
(OPERATIONAL_ONLY), folded in during the first D3 pass, which is exactly the OCR-vlm-arm gap. **One
real hole found and closed:** WS-C's OCR assembler budgeted on its OWN `VIDEO_OCR_CHARS_PER_SECOND`
(on `OcrConfig`, invisible to `cfg_tag`) — an output-affecting knob that could rewrite the `ocr`
record's bytes under an unchanged `record_id` (the silent `/context` overwrite D-13 exists to close).
Per D-11 the OCR rate is derived (`total − caption_share`), not an independent dial, so `get_ocr_config`
now reads the canonical `VIDEO_CHARS_PER_SECOND` / `VIDEO_CAPTION_CHARS_SHARE` (both OUTPUT_AFFECTING,
so `cfg_tag` forks on them); defaults `22−16=6` preserve behaviour and match `budget._ocr_rate`. The
`OUTPUT_AFFECTING | OPERATIONAL_ONLY == fields` completeness test still passes (49 fields). *Follow-up
for the WS-C owner: collapse the local `assemble.ocr_cap` stub to `import app.vision.budget.ocr_cap`
once `screentext` threads `vs` — the stub's own TODO; deferred to avoid re-plumbing a stage signature.*

**3 — `clipcap` primary landed** (`app/stages/video/clipcap.py`, the locked declaration): `primary` /
`required` / `order 20`, `needs=("clipprep","screentext")`, `provides=("clip",)`,
`mutable_slots=("enrichments",)`, `enabled()==resolve_pipeline()=="clip"`,
`version_fragment = backend.PIPELINE_VERSION + backend.prompt_tag(vs) + cfg_tag(vs)`, `run_async` (one
loop-native VLM call), `assemble()` → exactly ONE `kind='caption'`, `discriminator=""`, `t_start=None`
via `emit.caption_unit`. **Needs-closure now resolves** (clipprep + screentext are registered
siblings). Reads config fresh via `get_vision_settings()` — NOT `clipprep`'s `vision_settings` slot,
which in clip mode holds WS-B's `ClipVisionSettings` bundle, not D1's flat `VisionSettings` (the
captioner/emit/cfg_tag path speaks the flat one). Both are pure functions of the same per-chunk env,
so the accept-time dialect and the run-time read agree.

**4 — Full clip resolution verified** (`tests/test_clip_pipeline_e2e.py`, 6 tests, real discovery +
executor, not a hand-built stage list):
- `stages_for("video")` lists all five at the locked band — `keyframes 0`, `clipprep 5`, `captions 10`,
  `screentext 15`, `clipcap 20`, **no duplicate order**.
- `VIDEO_PIPELINE=clip` → `resolve()` yields **exactly one primary (`clipcap`)** + `clipprep` +
  `screentext`; legacy `captions`/`keyframes` gated off.
- one fixture chunk through `run_graph` → **exactly TWO records**: `kind='caption'` (disc `""`) and
  `kind='ocr'` (disc `"ocr"`), *both carrying the C1 span byte-for-byte* (`t_start`/`t_end` verbatim,
  `abs_time` never called), both C2-schema-valid, distinct `record_id`s.
- `pipeline_version` composes to `vidclip-mock-v1#<cfg>+cp-v1+ocr-mock-v1` (mock) and
  `vidclip-vlm-v1@screen-clip-v1.p<N>.<digest>#<cfg>+cp-v1+ocr-ppv4-cpu-v1` (vlm) — base primary
  fragment + sorted sidecar fragments, exactly the design's composed dialect.
- determinism: two runs → identical record set + ids. Legacy `keyframe` mode still resolves
  `captions`/`vidproc-mock-v0` byte-for-byte; an unknown `VIDEO_PIPELINE` still coerces to `keyframe`.

  Updated `tests/test_legacy_dialect.py::test_clip_mode_disables_the_legacy_pair`: its
  pre-integration premise ("no clip primary registered → zero-primary raise") is now obsolete — clip
  mode resolves to `clipcap`. The gate proof (both legacy stages disabled) is retained; the raise
  became a resolves-to-`clipcap` assertion.

**5 — D3 exit checklist re-run against the integrated base: all `PASS`.** Every item from the earlier
`## Build log — WS-D (consolidation · D3)` table holds unchanged (same green tests), now plus the live
clip graph: the mock `clipcap` seam's one-frozen-caption criterion is proven a second way — through the
Real executor beside the real `screentext` OCR record, and criterion 6 (a `.prompt.md` byte forks
`record_id`) now forks the *composed* clip dialect, whose `@<pack>.p<N>.<digest>` half is asserted in
the E2E. Default keyframe suite green; clip-mode E2E green.

Clean commit on `svc/vc-ws-d` (merge commit + this integration commit). Did not touch
`HANDOFF.md` / `CHARTER.md`.

---

## Build log — WS-E

**Branch:** `svc/vc-ws-e`, cut from the integrated `svc/video-clip` (WS-A/B/C/D/F/G; suite 465
green). Two commits: WS-E (the law test + the CHARTER extract), then WS-E2 (the one sanctioned
shared-core edit). **Suite: 724 passed, 21 skipped** — 465 baseline, unchanged and untouched, plus
259 from this workstream. `HANDOFF.md` / `CHARTER.md` not touched.

**Files owned and delivered:** `tests/test_emission_law.py` (new), `docs/record-emission-law.md`
(new — the CHARTER extract for the lead to fold in), `app/stagegraph/stage.py` (WS-E2 only).

### 1 — The finding first: the law holds, and it holds for a reason worth stating

The design's claim was that §4 is compliant by construction on the current registry. It is: **zero
real violations** across every configuration tested. The one thing worth flagging is not a violation
but the shape of the exemption — `video/keyframes` really is a provides-bearing sidecar with an
empty fragment, and the law test now runs *with the exemption set emptied* and asserts that pair is
the **only** violation in any configuration. So the exemption is a named debt with a tripwire, not a
category anyone else can slip into.

### 2 — Why the test is a MATRIX, not a single default-env pass

Every rider is conditioned on *enabledness*, and enabledness is read fresh from the environment
(`resolve_pipeline()`, `get_ocr_config()`, `get_audio_config()`). A law asserted only under the
default env would be silent about exactly the configuration a cutover flips to. `_MATRIX` covers 10
rows: legacy default · legacy+vlm · legacy with every audio sidecar on · legacy with mistyped values
· clip × {`off`, `mock`, `ppocr`, `vlm`, typo} · clip with every audio sidecar on. Every check below
runs over all 10.

Asserted over the **live registry** (`stages_for`, the real integrated stages — never a fixture):

- **R1 fork rider** — an enabled `sidecar` with non-empty `provides` returns a non-empty
  `version_fragment`; single frozen exemption `keyframes`. Plus the exemption-emptied run above.
- **R3(b)/(a)** — no `best_effort` stage carries a fragment; `best_effort` is sidecar-only; no
  `required` stage sits downstream of a `best_effort` one.
- **mutate rules** — no mutate overrides `enabled()`; every enabled mutate's `writes ⊆` the
  *enabled* primary's `mutable_slots` (video ships two primaries, so "the primary" is
  configuration-dependent and the check resolves it per row).
- **exactly one enabled primary per modality**, with a non-empty base dialect, in all 10 rows.
- **R4 determinism** at the resolver level (`is_enabled` / `version_fragment` are pure), and
  `resolve()` produces the same `pipeline_version` twice.
- **a disabled stage's fragment never reaches the dialect.** This is the one place the checks are
  deliberately *one-directional*. `screentext.version_fragment` is a pure function of the OCR config,
  so it returns `+ocr-mock-v1` even in keyframe mode where the stage is disabled. That is harmless
  only because `resolve()` composes from the enabled set alone — so the test asserts *that*, plus the
  observable form (keyframe mode's video dialect is the bare `vidproc-*-v0` literal), rather than
  forcing every resolver to gate itself on enabledness. An earlier draft asserted the bidirectional
  binding `enabled() ⇔ fragment != ''` and reddened on `screentext`; that draft was wrong, not the
  stage — R1 is one-directional by design and the addendum's `off`-carries-a-fragment argument
  depends on it.

### 3 — The 18-row worked table

Rows **1, 2, 3, 9, 11, 13, 14/15, 4/17, 18** are encoded as assertions (clip primary = one
`discriminator=""` caption unit with `t_start=None`, driven through the real `emit.caption_unit`;
per-keyframe records retired = the clip-mode enabled set is exactly `{clipprep, screentext,
clipcap}`; OCR = exactly one `kind='ocr'` unit **always**, driven through the real
`ScreentextStage.run_sync` with nothing legible to read, proving R3(e)'s presence-is-the-signal and
R4's outcome-independence; row 13 generalised to *no mutate may be permanently unrunnable*, checked
by quantifying over the whole matrix). The remaining rows carry the *reason* they are not
mechanically checkable at this layer (mostly: "an absence cannot be asserted over a registry"), and
`TABLE_ROWS` — the coverage map itself, is asserted complete, so a 19th row cannot be added without
deciding which it is.

Row 15's latent hole is pinned rather than fixed: `acoustic.provides == ()` is asserted, so the day a
real acoustic backend starts providing a slot, R1 applies and CI goes red. Audio owner's call, as §4.4
says.

### 4 — Exit criterion: the law FAILS on a violator

The checkers (`r1_violations`, `r3b_violations`, `mutate_violations`, `writes_violations`) take a
stage **list**, not the registry — so the code that vets the live registry is the code the negative
tests point at a bad stage. A hypothetical violator is caught both as a bare instance and once
**registered** into a throwaway modality and read back through `stages_for`. Verified by mutation
beyond that: breaking `screentext.version_fragment` to return `''` reddens *24* tests across the
matrix (reverted).

### 5 — WS-E2 landed. **It is a clip-cutover gate.**

`register_stage` bound `enabled()` ↔ `version_fragment()` only for `mutate` (`stage.py:226-230`). A
**sidecar** with non-empty `provides` — which `screentext` is under the ratified Architecture A, had
two independent resolvers, re-opening the diarize silent-overwrite class **for the caption record**.
Now: a provides-bearing sidecar that never declares `version_fragment()` fails at *import*, with
`R1_EXEMPT_SIDECARS = {("video","keyframes")}` as the single frozen exemption — named in `stage.py`
and in the law test, and asserted equal so the two cannot drift.

- Scope is exactly `provides`: additive sidecars (`translate`, `acoustic`, `injected_caption`) declare
  neither and stay legal.
- All **5 video + 5 audio** shipped stages register unchanged; verified additionally by dropping a
  synthetic bad stage file into `app/stages/video/` and confirming it raises during *real
  discovery*, not merely via a direct decorator call.
- **Honest limit, asserted rather than glossed:** settings do not exist at import, so the raise is the
  *structural* half only. A stage that declares a resolver returning `''` for the configuration it is
  enabled in still registers — that case is caught by the matrix in `tests/test_emission_law.py` and
  nowhere else. The three layers (registration raise · CI matrix · graph resolution) are documented in
  the CHARTER extract, with the gap between them made explicit.

**Consequence for the fan-out:** the §11 addendum item 6 prerequisite is **satisfied** —
`VIDEO_PIPELINE=clip` may now be turned on for a real user as far as the dual-resolver hole is
concerned (WS-G's E-2 / fresh-`user_id` rule is a separate, still-standing gate). `app/stagegraph/stage.py`
is the only shared-core file WS-E touched, as chartered.

### 6 — For the lead at reconciliation

`docs/record-emission-law.md` is the CHARTER extract: the invariant, T1–T5, R1–R5 (with R2 Corollary
2 and R4's `stage outcome` clause folded in), the three-enforcement-layer table, the frozen
exemption, and row 15's named latent hole. It is written to CHARTER standard and is meant to be
folded into `CHARTER.md` and then deleted or left as the working copy — WS-E did not edit
`CHARTER.md` or `HANDOFF.md`.
## Build log — WS-H (eval harness & the quality gates)

Full detail, run instructions, results and caveats live in `handoff/ws-video-clip-eval.md`.
This section is the summary and the two things the lead must reconcile.

**Delivered.** `scripts/capture_chunkset.py` (chunkset builder: `synth` / `headless` / `slice` /
`wrap`, plus `ocr-truth`, the O-2 bridge), `scripts/prompt_ab.py` (the A/B driver, the arm worker,
every scorer, the O-8 gate, the pre-push `--check` gates), `scripts/oracle_gemini.py` (blind pairwise
judge + frontier oracle), the two experimental packs, `tests/fixtures/chunksets/smoke-v1/` (12 chunks,
JSON only), `tests/test_eval_scorers.py` (41 tests), and the one-line `DP_OFFLINE_EVAL` boot guard in
`app/main.py`. **Suite: 506 passed** (baseline 465 + 41), `ASR_BACKEND=mock`.

**Structurally cannot write to `/context`**, three ways: the harness enters below the only writer
(`resolve`/`run_graph`/`build_c2`, never FastAPI, never `StorageClient`); the arm worker *poisons*
`StorageClient.__init__` and `ingest_core.process_chunk` before importing a stage, so the property is
enforced rather than asserted; and `DP_OFFLINE_EVAL=1` — which the harness requires, makes
`create_app()` raise. The flag that enables experiments is the flag that prevents serving.

**Arms cannot collide.** Each arm gets its own complete registry in a temp dir (packaged packs + its
experimental pack + a rewritten `routes.json` + an `arm.json`) and its own subprocess with
`VIDEO_PROMPT_DIR` set. `prompt_dir_fingerprint` is OUTPUT_AFFECTING, so the dir's contents fork
`cfg_tag` under *every* backend including mock; `arm.json` is what makes two arms with byte-identical
pack text (`injected` vs `injected-corrupt`) fork anyway. Measured: 4 distinct `pipeline_version` over
4 arms; `--check` fails the run on any collision.

### Two items for the lead to reconcile in §11

1. **The experimental packs live in `app/vision/prompts/experimental/`, not the flat pack dir.**
   §11 → WS-H names them at `app/vision/prompts/<id>.prompt.md` on the reasoning that "new paths ⇒ no
   conflict with D's production packs". That reasoning does not survive contact with WS-D's registry:
   `PACK_DIGEST` is a digest over *every loaded pack* and `load_registry` globs the flat dir, so two
   extra files there fork `record_id` for *every production caption* (for an experiment that never
   ran) and redden `tests/test_prompt_pack.py` (a WS-D file). Both globs are non-recursive, so the
   subdirectory is completely inert to the packaged registry and to every WS-D test while staying a
   committed git path. Guarded by a regression test that states what moving them costs. *No WS-D file
   was edited.*
2. **§11's "≈$0.02 per 200-chunk run" is ~7× low.** §7.3's own arithmetic — 200 × (1,517 prefill + 60
   output) at 12k/2k tok/s and $16/node-hour, i.e. `$0.250/screen-hour ÷ 360 × 200` — gives *$0.139*.
   The ~40 s wall target is met (chunks run concurrently, `--concurrency 8`). The conclusion is
   unchanged (14 cents is pre-push cheap); the number should read ~$0.14, or the pre-push corpus should
   be ~30 chunks. The harness prints the reconciliation on every run.

### Gates: built, and what actually ran

* **The widened grounding scorer (addendum edit #2) is implemented and tested**: all named ≥4-char
  strings (quoted spans, digit+letter tokens, internal caps, path/dot/@ shapes, capitalised
  non-stopwords), maximal adjacent runs merged, runs broken at punctuation, lenient substring
  grounding. Measured side-effect worth recording: across every run performed here the corpora
  contained *zero double-quoted spans* and 5–24 named strings per arm — a quote-only counter had
  nothing to measure. `dp_caption_ungrounded_quote_total` (declared by WS-F, unwired, with a note
  deferring the definition to WS-H) should be fed by
  `prompt_ab.grounding(caption, ocr_text)["named_ungrounded"]`; the wiring is a WS-D/WS-F edit.
* **O-8 is built, pre-registered, and unrun against a real model.** The rule (`ship A iff recall_lift
  > 0.25 AND propagation_rate < 0.10, else ship D`) is asserted at all four corners of its decision
  table, is strict at the boundary, and returns `UNDECIDED` — never a verdict, under a mock captioner
  or an unlabelled corpus. Validated end to end against a local stub endpoint whose reply is a
  function of its rendered prompt: both halves of the rule bit, in opposite directions, on one run
  (`recall injected 0.236 / blind 0.000`, `propagation 0.600` → ship D). *Those numbers are a harness
  validation and say nothing about Qwen3-VL.* Running it for real needs one served captioner (E-3(a))
  plus `capture_chunkset.py synth --count 200` (~10 min, no capture, no binaries).
* **O-4**: the mechanical half runs today via `--arms keyframe,injected` — the legacy per-frame graph
  vs the clip primary through the same executor, which is the A/B the design notes exists nowhere in
  the repo. The blind-judge half is built and unrun (no Vertex credential).
* **O-2**: unchanged — WS-C's synthetic-proxy verdict stands, gate unsatisfied, `mock` is the
  production default. `capture_chunkset.py ocr-truth` is the bridge that makes it *one labelling pass,
  two gates*: it exports a labelled chunkset as the bake-off's exact `ground_truth.json` + the
  high-resolution frames `screentext` would actually have read (`focus_bbox_2x` in the 3456-wide canvas
  `run_bakeoff.py` hard-codes). No real macOS capture exists in this build.
* **The ~$70 Gemini oracle**: built (blind pairwise judge with deterministic hash-based presentation
  order + a frontier-model upper bound), gated behind `--yes` and a printed cost projection, and
  *unrun* — no credential here. Wire shape, tolerant parse and blinding are unit-tested offline. The
  mechanical scorers stand alone; they are the gate, the oracle is corroboration.

### Also measured

The D-11 budget claim, through continuum's **own** `build_daylog` (imported, never reimplemented):
at `segment_seconds=60, block_segments=2` the projected block is 1,960 chars against
`EXCERPT_CHARS=6,000` — **67 % headroom, zero blocks over budget**, so the OCR line (rendered last,
truncated first) survives. `--check` fails the run if any block ever exceeds it.

Seven caveats (H-1 … H-7) are recorded in `handoff/ws-video-clip-eval.md` §9 — most importantly that a
`headless` chunkset leaves the OCR channel dark (`clipprep`'s fallback yields `ocr_times=()`), that
`synth` is not real capture, and that architecture D is realised at the prompt level because splitting
the injected block from the `kind='ocr'` record would mean editing a WS-C file.

Clean commit on `svc/vc-ws-h`. Did not touch `HANDOFF.md` / `CHARTER.md`, any WS-D prompt-registry
file, or any other workstream's owned file; the only shared-core edit is the sanctioned one-line
`DP_OFFLINE_EVAL` guard in `app/main.py`.
