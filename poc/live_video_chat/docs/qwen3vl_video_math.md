# Qwen3-VL Video Token Math — what's fixed, what we can tune, and the clip-length budget

> Reference for the `live_video_chat` POC (and any Qwen3-VL video serving). It explains
> exactly how a recorded clip becomes model tokens, which numbers are **fixed by the
> architecture** vs **knobs we can turn**, what **Qwen recommends**, what the model was
> **trained on**, and the **formulas** for "how long a clip fits." All constants are from
> primary sources (model `config.json`, `qwen-vl-utils/vision_process.py`, the HF
> `transformers` Qwen3-VL processor, the Qwen3-VL tech report arXiv:2511.21631, Qwen docs,
> vLLM docs). Last updated 2026-06-30.

---

## 0. TL;DR

- A clip is sampled to frames, **every 2 frames merge into 1 "temporal patch"** (≈ 1 patch/sec at the default **fps = 2**), and each patch costs `(H/32)·(W/32)` **vision tokens** + **~7 text tokens** for its `<t seconds>` timestamp.
- **Tokens per patch are clamped to [128, 768]**; Qwen's recommended operating point is **256 tokens/patch** (a ~512×512 frame).
- **Master formula:**
  ```
  video_tokens ≈ N_patches × (tokens_per_patch + ~7)
  N_patches    = sampled_frames / 2 = clamp(duration_s × fps, 4, 768) / 2
  tokens_per_patch = (H/32)·(W/32),  clamped to [128, 768]
  max_clip_s   ≈ 2 × budget_tokens / (fps × (tokens_per_patch + 7))
  ```
- The **binding limit in our deployment is vLLM's `--max-num-batched-tokens` (currently 8192)** — the per-request *encoder-cache* budget — **not** the model's 256K context. The model could hold ~100K+ video tokens; the serving knob is what we cap at.
- Two hard ceilings exist independent of that: **`FPS_MAX_FRAMES = 768`** (≈ **6.4 min** at fps 2 before frames thin out) and **`max_position_embeddings = 262144`** (256K context).

---

## 1. The pipeline: clip → frames → tokens

```
 recorded clip ──(sample at fps)──► N raw frames
                                     │  every 2 frames → 1 temporal patch  (temporal_patch_size=2)
                                     ▼
                          N/2 temporal patches
                                     │  each frame resized so (H,W) are multiples of 32
                                     │  patchify at 16px, then 2×2 spatial-merge  → net 32× per axis
                                     ▼
   per patch:  "<t seconds>"  +  <|vision_start|>  +  (H/32)·(W/32) video tokens  +  <|vision_end|>
                  ~7 text toks                          128 … 768 (clamped)            2 toks
```

So **one "frame" in the token stream = one temporal patch = 2 sampled raw frames**, and at fps 2 that's ≈ **one patch per second of clip**.

---

## 2. Master parameter table — FIXED vs TUNABLE vs DEFAULT vs QWEN-REC vs TRAINED-ON

### 2a. Architectural constants — **FIXED** (cannot change without breaking the weights)

| Parameter | Value | Note |
|---|---|---|
| vision `patch_size` | **16** | (Qwen2.5-VL was 14) |
| `spatial_merge_size` | **2** | 2×2 spatial merge after patchify |
| `temporal_patch_size` | **2** | 2 frames → 1 temporal patch |
| **effective spatial factor F** | **32** (= 16 × 2) | → `tokens = H·W / 1024` |
| `deepstack_visual_indexes` | **[8,16,24]** | multi-level ViT features injected into early LLM layers (the "deepstack" — note the vLLM accuracy bug, §7) |
| `mrope_interleaved` | **true**, `mrope_section [24,20,20]` | "Interleaved-MRoPE" over (t,h,w) |
| `video_token_id` / vision start,end | 151656 / 151652,151653 | |
| text layers / heads / kv-heads | 64 / 64 / 8 (GQA) | divides by 8 → TP=8 ✓ |
| `max_position_embeddings` | **262144 (256K)** | native context; YaRN-expandable to 1M |

### 2b. Video sampling defaults & bounds — **DEFAULT** (set by `vision_process.py`, can be overridden)

| Parameter | Value | Meaning |
|---|---|---|
| `FPS` | **2.0** | default sampling rate |
| `FRAME_FACTOR` | **2** (FIXED) | sampled frame count is always a multiple of 2 |
| `FPS_MIN_FRAMES` / `FPS_MAX_FRAMES` | **4 / 768** | clamp on sampled frame count → **768 frames = hard cap** |
| `VIDEO_MIN_TOKEN_NUM` / `VIDEO_MAX_TOKEN_NUM` | **128 / 768** | clamp on tokens **per patch** |
| `MODEL_SEQ_LEN` | **128000** (env-overridable) | client-side pixel-budget basis — note it's *under* the true 256K (§7) |

### 2c. What **Qwen recommends** for video (Qwen docs / "pixel control")

| Knob | Recommended | = tokens/patch |
|---|---|---|
| `fps` | **2.0** | — |
| per-frame `max_pixels` | **256·32·32 = 262,144** | **256 tokens/patch** |
| per-frame `min_pixels` | **4·32·32 = 4,096** | 4 tokens/patch (floor; real floor is 128) |
| all-frames `total_pixels` | **≤ 24576·32·32 ≈ 25.2M** | keep total video tokens ≤ ~24.5K |
| per-video token budget | **256 – 16384** | Qwen's stated usable range |

### 2d. What Qwen3-VL was **TRAINED ON** (arXiv:2511.21631)

| Aspect | Statement |
|---|---|
| Context | **256K native**, pretraining extended to 256K, inference-expandable to 1M |
| Timestamps | each temporal patch prefixed with a **text** timestamp `<3.0 seconds>` (in both seconds and HMS during training) — replaces Qwen2.5-VL's T-RoPE |
| Positional | **Interleaved-MRoPE** (full-frequency over t,h,w); native dynamic-resolution ViT (NaViT-style) |
| Temporal tasks | trained for second-level grounding / dense captioning |
| **fps / max-frames** | **NOT quantified in the report** — the operative `fps=2`, `FPS_MAX_FRAMES=768` are *inference-code defaults*, not stated training constants. Treat fps=2 as the well-supported operating point, not a proven training value. |

### 2e. vLLM serving knobs — **TUNABLE** (what we actually set)

| Flag | Controls | In our `serve.sh` |
|---|---|---|
| `--mm-processor-kwargs '{"fps":2,"max_pixels":…,"min_pixels":…}'` | sampling fps + per-frame pixel budget | set (but see §7: per-request/video `max_pixels` is **not honored** on 0.19.1 → we ffmpeg-normalize instead) |
| `--media-io-kwargs '{"video":{"num_frames":N}}'` | decoder frame cap | `num_frames=60` |
| `--limit-mm-per-prompt '{"video":1,"image":0}'` | max media items/request | video=1 |
| `--max-model-len` | context window (≤ 262144) | **16384** |
| **`--max-num-batched-tokens`** | **per-iteration / encoder-cache budget — THE cap a single video must fit** | **8192 (default)** ← the binding limit today |
| `--max-num-seqs` | concurrency | small (single user) |

---

## 3. The formulas

**Tokens per patch** (frame resized to H×W, multiples of 32):
```
tokens_per_patch = (H/32) × (W/32) = H·W / 1024        clamped to [128, 768]
```
At Qwen's recommended 262,144 px → 512×512 → 16×16 = **256**.

**Sampled frame count** (`smart_nframes`):
```
nframes   = clamp(duration_s × fps, FPS_MIN_FRAMES=4, FPS_MAX_FRAMES=768), floored to even
N_patches = nframes / 2
```

**Total video tokens:**
```
video_tokens ≈ N_patches × (tokens_per_patch + ~7 timestamp/delim tokens)
```

**Max clip length** (whichever ceiling binds first):
```
max_clip_s ≈ min(
    FPS_MAX_FRAMES / fps,                                  # frame cap: 768/fps
    2 × budget_tokens / (fps × (tokens_per_patch + 7))     # token cap
)
```
where `budget_tokens` = whatever bounds a single video — **in our serving that is `--max-num-batched-tokens` (8192)**, not 256K.

---

## 4. Worked numbers for OUR setup (512px-normalized, fps 2)

We ffmpeg-normalize every clip so the **longest side ≤ 512px** (and drop audio). Worst case (square 512×512) = **256 tokens/patch**; real phone aspect ratios are lighter (16:9 512×288 = 144, 4:3 512×384 = 192).

**Max clip length at 256 tok/patch (square worst case), vs the `--max-num-batched-tokens` budget:**

| `--max-num-batched-tokens` | fps 1 | **fps 2** | fps 3 | fps 4 | comment |
|---|---|---|---|---|---|
| **8192 (now)** | ~62 s | **~31 s** | ~21 s | ~16 s | also gated by `num_frames=60` → 30 s |
| 16384 | ~125 s | ~62 s | ~42 s | ~31 s | needs `num_frames` raised too |
| 32768 | ~250 s | ~125 s | ~83 s | ~62 s | approaching the 768-frame cap |

> At fps 2, real phone clips (144–192 tok/patch) fit **~40–55 s** in 8192 — but `num_frames=60` currently pins the effective max to **30 s**. To genuinely go longer you must raise **both** `--max-num-batched-tokens` **and** `num_frames` (= `round(max_clip_s × fps)`).

**Key intuition:** tokens depend on the **product `duration × fps`** (= total frames). So *30 s @ 2 fps ≡ 60 s @ 1 fps ≡ 15 s @ 4 fps* — same cost. Since little changes second-to-second in a live chat, **lower fps is the cheapest way to record longer**; drop fps before dropping resolution.

**To go sharper instead of longer:** raise the normalization size (e.g. 768px → 576 tok/patch). Then a 30 s @ 2 fps clip = 30 patches × 576 ≈ 17.3K tokens → needs `--max-num-batched-tokens ≥ ~18K`. Sharper ⇒ fewer seconds per token budget.

---

## 5. What's fixed vs what we can play with (cheat sheet)

| Want to… | Lever | Cost |
|---|---|---|
| Record **longer** clips | ↑ `--max-num-batched-tokens` + ↑ `num_frames`; or ↓ fps | more GPU memory / more prefill latency; lower fps loses motion detail |
| **Sharper** video (read small text/signage) | ↑ normalize size (512→768) + ↑ `--max-num-batched-tokens` | quadratic token growth → much higher latency |
| **Snappier** answers | ↓ clip length / ↓ fps / ↓ resolution | less context |
| Change **fps** | `--mm-processor-kwargs fps` (launch) — **fixed per server on 0.19.1** | per-request fps unreliable until a newer vLLM (§7) |
| Use the **full 256K context** | ↑ `--max-model-len` (and budget) | only matters once we serve much longer/multi-clip |

Cannot change: the 32× spatial factor, 2-frame temporal merge, the 128/768 per-patch clamp, the 768-frame cap, 256K context — these are architectural.

---

## 6. Why 8192 and not 256K? (the thing that bit us)

The model's context is 256K, but vLLM pre-allocates an **encoder-cache / per-iteration budget** = `--max-num-batched-tokens` (default **8192**). A single video's vision tokens must fit there in one prefill, or the request **400s** with *"exceeds the pre-allocated encoder cache size."* That's the real cap on clip size today — and it's a one-line serving change to raise (memory permitting), **not** a model limit.

---

## 7. Gotchas & version-specific caveats

1. **`max_pixels` is NOT applied to *video* on our vLLM 0.19.1.** Per-request `mm_processor_kwargs` (incl. `max_pixels`) is ignored for video over the OpenAI API on this build, and vLLM's video path doesn't downsample to `max_pixels` like it does for images. **This is why we ffmpeg-normalize clips to 512px** to bound tokens deterministically. A newer vLLM *may* fix per-request video kwargs (see the cluster-upgrade doc) — re-test before dropping the ffmpeg step.
2. **`qwen-vl-utils` defaults `image_patch_size=14`** (a Qwen2.5-VL holdover) → factor 28, not 32 → token math off by (32/28)² ≈ **31%** if you preprocess client-side. The vLLM/transformers server path uses 16/32 correctly; only manual `qwen-vl-utils` use is affected. We don't preprocess with qwen-vl-utils, so we're fine — but worth knowing.
3. **Two frame-count paths** (`mm-processor-kwargs` fps/num_frames vs `media-io-kwargs` num_frames) can desync timestamps↔frames; **pass `fps` *with* `num_frames`** (we do) and prefer setting exactly one sampling control.
4. **`MODEL_SEQ_LEN` defaults to 128000** in `vision_process.py` (under the true 256K) — conservative client-side pixel budgeting; irrelevant to us since the server governs.
5. **Deepstack × torch.compile accuracy bug** (vLLM < 0.23, fixed PR #43617): on pre-0.23 builds the compiled graph can silently skip the deepstack multi-level vision features → modestly degraded vision accuracy. We're on 0.19.1 — flagged in the cluster-upgrade doc as a real reason to move to ≥ 0.23.

---

## 8. Our current settings + suggested operating points

**Current (live):** normalize → 512px longest side, fps 2 (launch), `num_frames 60`, `--max-num-batched-tokens 8192`, `--max-model-len 16384` → **30 s max clip, ~6–8K tokens, ~0.5 s TTFT.**

**If you want longer (≈60 s @ 512px/2fps):** `--max-num-batched-tokens 16384`, `num_frames 120`, `max_clip_seconds 60` (config), normalize stays 512. ~15K tokens, still ~1 s TTFT. (~4-min vLLM restart.)

**If you want sharper (≈768px, ~28 s @ 2fps):** normalize 768, `--max-num-batched-tokens ~20000`, `num_frames 56`. Better OCR of small text; ~2× the tokens/latency.

> Sweet spot for a live "show-and-ask": **fps 2, 512px, 30 s** as now. Reach for **lower fps (1)** to record longer cheaply; reach for **768px** only when reading fine text matters.

---

## Sources
- `qwen-vl-utils/vision_process.py` (raw): https://raw.githubusercontent.com/QwenLM/Qwen3-VL/main/qwen-vl-utils/src/qwen_vl_utils/vision_process.py
- transformers video processor / processor (timestamps): `.../models/qwen3_vl/video_processing_qwen3_vl.py`, `.../processing_qwen3_vl.py`
- `Qwen/Qwen3-VL-32B-Instruct` `config.json` + model card; Qwen3-VL README + "Pixel Control" doc
- Qwen3-VL Technical Report — arXiv:2511.21631
- vLLM multimodal + engine-args docs; encoder-cache behavior (issues #28375, #41485)
