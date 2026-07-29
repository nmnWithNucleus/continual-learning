# ws-video-clip-probe.md — WS-A capability report (serving prerequisites)

**Status:** probes written + source-verified; **live curl NOT run** (no VL endpoint served on this
box by default — see §1). · *Owner:* WS-A. · *Date:* 2026-07-24.
*Feeds:* WS-D (prompt pack / clip primary), WS-C (OCR sidecar), and escalation *E-3(a)*.
*Instruments:* [`scripts/vlm_probe.py`](../scripts/vlm_probe.py), [`scripts/ocr_probe.py`](../scripts/ocr_probe.py).

WS-A's job is to verify every external serving assumption the clip design rests on **before any app
code is written**, and to make the E-3 ask precise. This report states, per probe: the answer, how it
was obtained, the **exact launch flag** (if any), and what still needs a 60-second live curl. Where a
probe could not be run live, it says so plainly — it does not fabricate a result.

---

## 0. Headline — the one thing that changes a design decision

> **Probe (1) inverts its own premise.** The clip design and E-3(a) assume the served model's
> `--limit-mm-per-prompt` image cap "is commonly `image=1`", so the multi-image call would `400` on
> the first chunk unless the flag is raised — and if it could not be raised, **WS-D's default pack
> would fall back to `screen-clip-single-v1` (K=1)**. **That fallback is NOT triggered.** In the
> deployed stack (vLLM **0.24.0**), the per-modality image limit **defaults to 999**, and Qwen3-VL's
> model-side supported limit is `None` (unlimited). **The design's K≤12 multi-image call already
> validates on the *current, unmodified* `serve_vllm.sh`** — no serving change is strictly required to
> admit the payload. **WS-D ships `screen-clip-v1` (multi-image) as the default; the K=1 pack stays as
> the documented degraded profile, not the default.**

Every launch flag E-3(a) names is now **recommended-for-determinism, not required-for-correctness**,
on this vLLM version. Details and the residual live-curl confirmation are below.

---

## 1. What was and was NOT run — honesty first

**No VL endpoint is served on this box by default.** `curl http://127.0.0.1:8000/v1/models` →
`Connection refused`; nothing is listening on 8000/8001/9000. So **the four probes were NOT run against
a live endpoint.** The `scripts/vlm_probe.py` unreachable path prints exactly this and the flags to
bring one up, and exits `2` — it never invents a token count or a limit.

This box *is* an 8×H100 node and *does* carry the `vllm-cu13` (vLLM 0.24.0) and `vllm-vlm` (0.19.1)
conda envs plus the Qwen3-VL-32B weights in the HF cache — i.e. the exact stack `serve_vllm.sh`
launches. WS-A deliberately **did not launch a TP=8 server**: serving is inference/platform's to own
(E-3), it needs `HF_TOKEN`, it is a multi-minute outward-facing operation, and the house rules keep WS
work headless/offline. Instead, every assumption was **verified against the installed vLLM 0.24.0
source + the transformers 5.13.0 Qwen3-VL processor + the model's own `config.json` /
`preprocessor_config.json`** — the same method the lead used for §12.1. This yields the server's
*documented* behaviour with high confidence; the probe scripts remain the *final live confirmation*,
runnable in ~60 s once E-3(b)'s endpoint exists.

**Method tag on every claim below:** `[SRC]` = read from installed source/config on this box (high
confidence); `[LIVE-TODO]` = only a live curl can settle it (the script is ready).

---

## 2. Results table

| # | Probe | Answer | Flag needed? | Method |
|---|---|---|---|---|
| 1 | N `image_url` parts in one message; is `--limit-mm-per-prompt` raised? | **Default is 999** (not 1); Qwen3-VL model limit `None`. K≤12 validates unmodified. | **No** (optional pin) | `[SRC]` |
| 2 | `response_format: {"type":"json_schema"}` — guided decoding available? | **Yes, ON by default**, backend `auto` (xgrammar-first). | **No** (pin backend for determinism) | `[SRC]` |
| 3 | `usage.prompt_tokens` for one 768×480 frame — 360 / 470 / lower? | **360** (factor 32); default `max_pixels` 16.78 Mpx ⇒ *no clamp*. | **No** (optional determinism pin) | `[SRC]` + local `smart_resize` |
| 4 | `video_url` data-URI (informational, O-4) | **Supported** & first-class; 0.24.0 is post the timestamp fix. | **No** | `[SRC]` |

**Net:** every probe passes on **config/source evidence** with *zero mandatory launch-flag changes*
for the design's frame sizes on vLLM 0.24.0. Two flags are *recommended* as determinism guards (§7).

**Adversarially verified.** The two load-bearing claims were independently re-checked by skeptic agents
told to refute them: *"the `--limit-mm-per-prompt` flag IS required for K=12"* → **refuted** (default is
999), high confidence; *"768×480 → exactly 360 tokens, no clamp at defaults"* → *upheld* (survives
even the video-path cap check: a single frame's temporal-inclusive area 737,280 < the 786,432 video cap,
so 360 holds regardless of media type), high confidence. Each finding carries installed-source `file:line`
citations (verified against `vllm-cu13` = vLLM 0.24.0 + transformers 5.13.0 on this box).

---

## 3. Probe 1 — multi-image per prompt & `--limit-mm-per-prompt`  `[SRC]`

**Question.** vLLM caps multimodal items per prompt via `--limit-mm-per-prompt`. What is the default,
and does the design's one-message K-image call (D-02, `VIDEO_CLIP_MAX_FRAMES=12`) fit?

**Answer.** The image cap **defaults to 999**, so *12 images in one message validate with no flag*.
- `MultiModalConfig.limit_per_prompt` is `Field(default_factory=dict)` → `{}`
  (`config/multimodal.py:80`); `get_limit_per_prompt(modality)` returns **999** for any modality absent
  from that dict — *"Unspecified modality is set to 999 by default"* (`config/multimodal.py:320-322`,
  docstring `:84`). Independently re-grepped: `count: int = Field(999, ge=0)` (`:20`).
- Model-side cap: effective = `min(supported, allowed)`. Qwen3-VL inherits Qwen2-VL's
  `get_supported_mm_limits → {"image": None, "video": None}` (`models/qwen2_vl.py:868-869`; no override
  in `qwen3_vl.py`) = unlimited, so the net image ceiling with no flag = **999**.
- N `image_url` parts → N distinct image items, count-validated on each add
  (`entrypoints/chat_utils.py:600,616`); over-limit raises `VLLMValidationError("At most {N}
  image(s) may be provided in one prompt.")` → mapped to **HTTP 400** in the OpenAI server.
- Since 999 ≥ 12, this never fires for the design.
- Qwen3-VL is inherently multimodal, so the vision tower loads even under today's **text-only**
  `serve_vllm.sh`; media just isn't being sent yet. `--max-model-len 32768` is ample (12×360 = 4,320
  vision tokens ≪ 32,768). *⇒ the current `serve_vllm.sh`, unmodified, already admits the K=12 call.*

**Exact flag (optional).** `--limit-mm-per-prompt` is a JSON-dict-typed arg → the value must be a JSON
string (parsed by `json.loads`; `engine/arg_utils.py:371-378`). A bare `image=16` is **not** accepted.
Two valid forms:
```
--limit-mm-per-prompt '{"image":16}'
--limit-mm-per-prompt.image 16          # dotted equivalent
```
Note `image=16` **lowers** the ceiling from 999 to 16 (still ≥ 12, safe) — it is a *tighter* bound, not
"headroom above default". Recommended only as an explicit, self-documenting guard.

**Consequence for WS-D.** Ship `screen-clip-v1` (multi-image) as the **default** pack. Keep
`screen-clip-single-v1` (K=1) as the documented degraded/interactive profile (A-9), *not* as a
`--limit-mm-per-prompt`-forced fallback. The fallback branch in D-02/§11-WS-A does not activate on this
version. `[LIVE-TODO]` confirm with `scripts/vlm_probe.py --probe 1` (the ladder 1→16 prints the exact
cap the running server enforces — this catches a future version that changes the default).

---

## 4. Probe 2 — guided decoding (`response_format: json_schema`)  `[SRC]`

**Question.** Is guided/structured decoding available on the OpenAI endpoint? It is the primary
discipline lever for a Flash-class 32B (D-13/§5.3), not an optimisation.

**Answer.** **Yes — available and ON by default; no launch flag required.**
- `response_format` accepts `text | json_object | json_schema` (`engine/protocol.py:156-164`). The
  `json_schema` payload is `{"name": str, "schema": {...}}` — the schema dict is on the wire key
  `schema` (pydantic alias; `engine/protocol.py:123-129`). A missing `json_schema` object → HTTP
  400 (`chat_completion/protocol.py:659-683`).
- It maps to vLLM's V1 structured-outputs path (`to_sampling_params`,
  `chat_completion/protocol.py:588-618,647`). `StructuredOutputsConfig.backend` **defaults to `auto`**
  (`config/structured_outputs.py:21-25`), resolved per-request (xgrammar first, then guidance/outlines
  fallback; `sampling_params.py:862-1009`). There is *no* enable/disable switch and no separate
  `--guided-decoding-backend` needed.
- Backing packages are actually installed in `vllm-cu13`: `xgrammar 0.2.3`, `llguidance 1.7.6`,
  `outlines_core 0.2.14` — the fallback chain won't fail a lazy import. TP=8 is not a factor (the
  grammar bitmask is TP-independent).

**Exact wire (for WS-D's parse ladder / §5.3).**
```json
{"type":"json_schema","json_schema":{"name":"clip","schema":{...JSON Schema...}}}
```
`vlm_probe.py`'s probe 2 sends exactly this shape (plus a `json_object` control).

**Recommended flag (determinism, not correctness).** `auto` **silently changes the backend per
request** based on schema features, and the config warns this behaviour is *"subject to change in each
release"*. For a pipeline whose whole identity story is reproducibility (record_id / dialect), *pin
the backend*:
```
--structured-outputs-config '{"backend":"xgrammar"}'    # confirm exact CLI spelling on the live box
```
`[LIVE-TODO]` confirm the endpoint honours `json_schema` and returns valid JSON: `vlm_probe.py --probe 2`.

---

## 5. Probe 3 — `usage.prompt_tokens` for one 768×480 frame  `[SRC]`

**Question.** Is it **360** (factor 32), *470* (factor 28), or *materially lower* (server-side
`max_pixels` clamping)?

**Answer.** **360 tokens, factor 32, no clamp at default settings** — provided frames are sent as
`image_url` (they are; D-02).
- **Factor = 32.** `factor = patch_size × spatial_merge_size = 16 × 2`. Model `vision_config`:
  `patch_size=16`, `spatial_merge_size=2` (`config.json`); `preprocessor_config.json`: `patch_size=16`,
  `merge_size=2`. HF/vLLM call `smart_resize(factor=patch×merge=32)`
  (`transformers …/image_processing_qwen2_vl.py:174`, `vllm …/qwen3_vl.py:929`), overriding the legacy
  `factor=28` default. *The factor-28 (470) branch does not apply to this model.*
- **768×480 → 360.** `smart_resize` rounds each edge to a multiple of 32: 768→768, 480→480
  (already exact); area 368,640 px is within `[min,max]` so it is *unchanged*.
- Patches `48×30 = 1,440`; merged tokens `1,440 // 2² = 360`. Reproduced locally by
  `vlm_probe._smart_resize(768,480,32) = 360`.
- **`size` is an *area* (min/max pixels), not an edge length.**
  `size={shortest_edge:65536, longest_edge:16777216}` in `preprocessor_config.json` means
  `min_pixels 65,536 (256²)` and `max_pixels 16,777,216 (4096² = 16 Mpx)`.
- It is passed straight into the area comparison (`image_processing_qwen2_vl.py:175-176`; vLLM
  mirror `qwen3_vl.py:911-913`).
- A 768×480 frame (368,640 px) is ~45× below the cap, so **not downscaled**, and ~5.6× above the
  floor, so not upscaled. A silent downscale would need > 16.78 Mpx.
- **The "materially lower / clamping" branch does not fire.**
- Width sweep (predicted, image path): **1024×640 → 640 tok**, *1280×800 → 1000 tok* — i.e. 1280 is
  *2.78×* the tokens of 768 (matches A-16's cost-blowup warning). `vlm_probe.py --sweep 768,1024,1280`
  measures these live.

**Exact flag (optional, determinism only).** Since 768×480 never clamps at default, no flag is required
for correctness. To make the pixel cap **immune to a future default change**, pin it (values = current
defaults, so behaviour-neutral). Qwen3-VL's image path honours `min_pixels`/`max_pixels`/`size`
(`qwen3_vl.py:908-913`):
```
--mm-processor-kwargs '{"size":{"shortest_edge":65536,"longest_edge":16777216}}'
# legacy-equivalent on the IMAGE path:
--mm-processor-kwargs '{"max_pixels":16777216,"min_pixels":65536}'
```
**Caveat for WS-D:** this holds for **image** content. If a future path ever sends frames as *video*
content, the video processor has a *much smaller* default cap and does *not* accept
`min_pixels`/`max_pixels` (only the `size` form routes) — but D-02 sends stills, so the image path is
what ships. `[LIVE-TODO]`: `vlm_probe.py --probe 3` reports the real `usage.prompt_tokens` (it isolates
the image's contribution via a text-only control, so the ±2 `<|vision_start|>`/`<|vision_end|>` tokens
and any per-image overhead are visible). Getting exactly 360±4 confirms both factor 32 and no clamp in
one call.

---

## 6. Probe 4 — `video_url` data-URI (informational, O-4)  `[SRC]`

**Answer.** **Supported and first-class in vLLM 0.24.0; no mandatory flag; 0.24.0 is post the fix.**
- `video_url` with a `data:video/mp4;base64,…` URL is a supported content part (`chat_utils.py:179-190`,
  parser `:1447,1539`, dispatch `:1703-1706`); the data-URI decodes via
  `VideoMediaIO.load_base64` (`media/video.py:74-140`) — base64 is required (`;base64`), else
  `NotImplementedError`.
- Loader for Qwen3-VL is auto-selected (`Qwen3VLVideoProcessor → "qwen3_vl"` backend); underlying codec
  defaults to **opencv** (`media/video.py:541`; env `VLLM_VIDEO_LOADER_BACKEND` default `opencv`,
  `envs.py:82`), pyav optional.
- **Timestamp AssertionError (serve_vllm.sh:52-55 / PR #36136):** timestamps are computed in-process and
  guarded by `assert len(timestamps)==grid_thw[0]` (`qwen3_vl.py:1451-1454`); the in-tree
  `do_sample_frames` branch matches the fixed behaviour, and 0.24.0 > 0.19.1 (which the script states is
  post-fix) ⇒ *0.24.0 inherits the fix.* `[LIVE-TODO]` (git-tag-level confirmation of the exact commit
  was not done statically).

**Why DP still chooses K-stills (D-02), stated crisply.** With `video_url` the **server** decodes the
mp4 and *decides which frames the model sees* (reads container fps/duration, uniform-samples
`num_frames`/`fps` indices via OpenCV seek+decode). That frame set depends on the serving box's
cv2/FFmpeg build and the container's keyframe layout — *not reproducible or auditable from the request
alone*. K pre-extracted stills in one call keep frame selection *client-side, deterministic, logged*.
This is the *same* determinism argument the video-pipeline lead already banked when deleting the OpenCV
fallback (§12.1). *The wire is usable for O-4 later; the reason not to use it now is determinism, not
capability.*

---

## 7. The exact launch flags — for E-3(a)

`serve_vllm.sh:52-55` omits `--limit-mm-per-prompt` / `--mm-processor-kwargs` / `--media-io-kwargs` and
calls them *"intentionally omitted"*. **Updated ask, precise:**

**Strictly required to serve the clip design on vLLM 0.24.0:** *(none for the multi-image image path)*.
The current text-only launch already admits the K≤12 `image_url` call, guided JSON is on by default, and
768×480 frames are not clamped. The one genuine serving ask that remains is **E-3(b)** — a captioner
endpoint *distinct from :8000* so DP's prefill bursts don't land in the chat tenant's continuous batch
(unchanged by this report; the GPU-contention argument stands entirely).

**Recommended (determinism / explicitness / version-drift insurance) — add to the multimodal launch:**
```bash
vllm serve Qwen/Qwen3-VL-32B-Instruct \
  --tensor-parallel-size 8 --host 127.0.0.1 --port 8000 \
  --limit-mm-per-prompt '{"image":16}' \
  --mm-processor-kwargs '{"size":{"shortest_edge":65536,"longest_edge":16777216}}' \
  --structured-outputs-config '{"backend":"xgrammar"}'
```
- `--limit-mm-per-prompt '{"image":16}'` — pins the image cap explicitly (16 ≥ K=12). **Not required**
  (default 999), but makes the contract legible and guards against a future default change. JSON-string
  form is mandatory; `image=16` is rejected.
- `--mm-processor-kwargs '{"size":{...}}'` — pins the pixel cap at the current default so a vLLM upgrade
  can never silently start clamping our frames. Behaviour-neutral today.
- `--structured-outputs-config '{"backend":"xgrammar"}'` — pins the guided-decoding backend so it does
  not switch per-request (the `auto` default is documented as changing across releases). *(Confirm the
  exact CLI spelling on the box; the config field is `backend`.)*

**Do NOT** add `--media-io-kwargs` unless/until O-4 enables the `video_url` path.

---

## 8. How to run the probes (once an endpoint exists — E-3(b))

```bash
# from product/services/data-processing, against a live VL endpoint:
VIDEO_VLM_URL=http://<node>:8000 ./.venv/bin/python scripts/vlm_probe.py \
    --frames 1,2,4,8,12,16 --sweep 768,1024,1280 --json /tmp/vlm_probe.json
```
`vlm_probe.py` speaks the exact wire `app/vision/vlm.py` speaks (same `/v1/chat/completions`, same
`image_url` data part, same `VIDEO_VLM_*` env config), needs only stdlib + `httpx`, synthesises its own
768×480 PNG (no PIL in the DP venv), and — reachable or not, prints `PASS`/`FAIL`/`SKIP` per probe and the
launch flags. Exit `0` = all requested probes ran & passed; `2` = endpoint unreachable; `1` = a live `FAIL`.
A green run here is the 60-second confirmation that turns every `[SRC]` above into `[VERIFIED-LIVE]`.

---

## 9. OCR serving probe (`scripts/ocr_probe.py`) — for WS-C  `[SKIP]`

**No OCR runtime exists on this box** (scanned every conda env: no `paddleocr`, no `rapidocr`), and the
`sidecars/ocr/` service is WS-C's to build, so the OCR serving assumptions are **unverified live**. The
probe SKIPs honestly (exit 2) and states the contract WS-C must expose and the assumption it must measure:
- `GET /health` → `{det_sha256, rec_sha256, ort_version, ep:"CPU", …}`, which DP asserts against config
  **at graph resolution** and fails loud on mismatch (D-06; WS-C exit criteria). The probe checks these
  keys are present.
- `POST /ocr {image: data-URI, …}` → `[{text, bbox, confidence}, …]`.
- **§7.1 assumption under test:** PP-OCRv6 det+rec CPU at *~0.6 s / 1728×1080 frame, 4 threads*.
- The probe times it either through the running sidecar or via a *separate* interpreter passed
  with `--python` (it imports *nothing* heavy into the DP venv — honouring the
  numpy-2.5.1-vs-`numpy<2.4` quarantine confirmed in §12.3).
- This is **gated on O-2** (WS-C's own bake-off: can PP-OCRv6 read 13 pt macOS UI text at 1728 px
  through CRF 28?), so the production default stays `VIDEO_OCR_BACKEND=mock` until O-2 passes. WS-A does
  not pre-empt that call; it ships the instrument.

Run once the sidecar exists:
```bash
VIDEO_OCR_URL=http://127.0.0.1:<port> ./.venv/bin/python scripts/ocr_probe.py --frames 50
# or local timing through the sidecar's own venv:
./.venv/bin/python scripts/ocr_probe.py --python sidecars/ocr/.venv/bin/python --frames 50
```

---

## 10. Residual — what only a live curl can settle  `[LIVE-TODO]`

All four VLM answers are source-derived with high confidence; a live curl of the shipped probe closes
the last gaps: (1) the exact reported `usage.prompt_tokens` for a real 768×480 frame incl. the
`<|vision_start|>`/`<|vision_end|>` overhead; (2) that guided `json_schema` returns valid JSON on *this*
served model, not just that the server accepts the field; (3) the real per-frame limit the running
server enforces (the 1→16 ladder), catching any deployment that overrides the 999 default; (4) that the
server's OpenCV/FFmpeg build decodes a given mp4 (probe 4). None of these block a build — the probe is
run when E-3(b) stands up the captioner endpoint. WS-A's exit criterion — *the report exists, names the
required flags, and feeds E-3(a) without being blocked by it*, is met.
