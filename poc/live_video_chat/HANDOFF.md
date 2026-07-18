# HANDOFF — agent working canvas (global)

> **What this is.** The single source of truth for any agent (or human) building this POC.
> **Read this whole file first, then your one `handoff/wsN-*.md`.** Those two together are
> enough to start your workstream independently — you should not need to ask anyone for context.
>
> **Why it exists.** V0 is built by ~5 agents in parallel + 1 integrator. They can only work
> independently if the **contracts between them are pinned up front**. That's what this file does:
> shared infra, the data-flow, the exact APIs each piece exposes/consumes, and the locked decisions.
> For the stable human-facing overview see [`README.md`](README.md). This file is the *working* record.

**Last updated:** 2026-07-18 · maintained collaboratively, one section per workstream session.

> **Addendum (2026-07-18) — post-V0 state.** Two rounds happened after the 2026-06-30 V0/V0.1
> validation and were not written up here until now:
> **(1) 2026-07-01 long-clip round** — `backend/config.py` gained `MODEL_PRESETS`
> (`dense_1800` default / `sharp_768` / `sweet_768`) + `POST /api/model/reconfigure` &
> `GET /api/model/state` (vLLM relaunch per preset), a `PRERECORDED_CLIPS` registry
> (30-min ~128K-token IShowSpeed clip), and per-turn `POST /api/feedback` (thumbs → JSONL,
> intended as future fine-tuning signal); `serve.sh` reworked for long pre-recorded clips.
> **(2) 2026-07-02** — the nucleus-580 cluster upgrade this POC motivated **was executed**
> (node-7 on driver 580.159.03; NFS envs `~/vllm-vlm-580` = vLLM 0.24.0/torch 2.11 cu130 and
> `~/moe-580` = faster-whisper 1.2.1; `server/serve-580.sh` added). The node recreate **wiped
> `/mnt/localssd`** (turns/, the pre-recorded clip, the feedback JSONL, `.hf-home`); the model
> was re-cached to `~/.cache/huggingface`. The stack has been **down since** and the recorded
> tunnel URL is dead.
> **Outstanding:** RUNBOOK Phase 5 re-validation on vLLM 0.24 (incl. whether ffmpeg
> clip-normalization is still needed), regenerate the wiped `/mnt/localssd` artifacts, fix
> `serve-580.sh` LOG/PIDFILE paths (they point at the wiped `/mnt/localssd/.hf-home/`, no
> `mkdir`), and re-check `nvidia-fabricmanager` on node-7. Coordinate node-7 use with the
> product serve loop via [`product/HANDOFF.md`](../../product/HANDOFF.md).

---

## What we're building (one paragraph)

A phone-accessible web app where you point your camera at something and ask a question by voice or
text; a short recorded **video clip + text** is sent to a **self-hosted Qwen3-VL-32B-Instruct (dense)**
model, and the answer **streams** back into an output box. It mimics the "ChatGPT live-video voice
chat" experience, but with our own open-weight VLM. **V0 is deliberately minimal:** single-turn
(length-1, context resets every turn), **clip-based** (a fixed-max-length recording, not a live
stream yet), **video-only into the model** (audio is used only for browser speech-to-text), phone-reachable
over HTTPS.

---

## Workstream index

| WS | What | Status | Working file | Owner/agent |
|---|---|---|---|---|
| 1 | **Inference server** — host Qwen3-VL-32B on vLLM, OpenAI-compatible, video input | ✅ done (vLLM 0.19.1, TP=8, up) | [handoff/ws1-inference-server.md](handoff/ws1-inference-server.md) | WS1 |
| 2 | **Backend / API** — FastAPI: receive clip+text, call ASR, call model, stream tokens | ✅ done (real vLLM turn streams) | [handoff/ws2-backend-api.md](handoff/ws2-backend-api.md) | WS2 |
| 3 | **Frontend / UI** — phone web app: camera capture, ASR mic, streaming output box | ✅ built (iOS device leg pending user) | [handoff/ws3-frontend-ui.md](handoff/ws3-frontend-ui.md) | WS3 |
| 4 | **ASR module** — faster-whisper: audio blob → text (lives in backend/) | ✅ done (wired into /api/transcribe) | [handoff/ws4-asr.md](handoff/ws4-asr.md) | WS4 |
| 5 | **Tunnel / HTTPS** — cloudflared: expose backend to the iPhone over HTTPS | ✅ done (HTTPS streaming verified) | [handoff/ws5-tunnel.md](handoff/ws5-tunnel.md) | WS5 |
| 6 | **Integration** — wire all, phone end-to-end test, finalize docs (the stitcher) | ✅ wired + validated server-side | [handoff/ws6-integration.md](handoff/ws6-integration.md) | WS6 |

> **WS1–WS5 run in parallel.** They depend only on the **contracts** below, not on each other's
> code. WS6 starts once the others have something runnable and owns the end-to-end wiring + test.
> Each agent keeps its own `wsN-*.md` current (Worklog section) and flips its row above when done.

---

## System architecture & data flow

```
                          ┌──────────────────────────── one a3mega node ────────────────────────────┐
 iPhone (Safari)          │                                                                          │
 ┌───────────────┐  HTTPS │  ┌──────────────────────────┐        OpenAI /v1/chat/completions         │
 │  Web UI (WS3) │◄──────►│  │   Backend / API (WS2)     │  file:// clip + prompt, stream=true        │
 │  - camera     │ (WS5   │  │   FastAPI                 │ ─────────────────────────────────────┐     │
 │  - mic→ASR    │  cloud-│  │  GET  /            (UI)   │                                       ▼     │
 │  - text box   │ flared)│  │  GET  /api/config         │   ┌───────────────────────────────────────┐│
 │  - output box │        │  │  POST /api/transcribe ───►│   │  vLLM server (WS1)                      ││
 └───────────────┘        │  │  POST /api/turn (stream)  │   │  Qwen3-VL-32B-Instruct, TP=8, video     ││
                          │  │        │  ▲                │◄──│  127.0.0.1:8000  (token stream back)    ││
                          │  │        ▼  │ text           │   └───────────────────────────────────────┘│
                          │  │   ASR (WS4) faster-whisper │                                              │
                          │  └──────────────────────────┘                                               │
                          └──────────────────────────────────────────────────────────────────────────-┘
```

**Turn lifecycle (V0, single-turn):**
1. Phone loads the UI over the cloudflared HTTPS URL → shows a static **hello** greeting.
2. User taps 🎤 and speaks a question → UI records a small **audio-only** blob → `POST /api/transcribe`
   → text appears in the (editable) text box. (Or the user just types.)
3. User taps **Record** → camera preview → records an **MP4/H.264** clip, **auto-stops at
   `MAX_CLIP_SECONDS`** (30s) or on manual stop.
4. User taps **Send** → `POST /api/turn` with the video clip + final text.
5. Backend saves the clip to a local path, builds the Qwen3-VL chat request (`file://` clip + prompt,
   `stream=true`), and **streams tokens** from vLLM straight back to the UI's output box.
6. UI shows a **spinner** in the output box from Send until the first token, then streams the answer.
7. **Context resets.** No history is kept; the next turn is independent.

---

## CONTRACTS (the spine — do not diverge without updating this file)

### Shared config constants (one source of truth)
Backend owns these and exposes them at `GET /api/config`; the UI fetches them on load so there is
**one** place to change them. Defaults for V0:

| Key | Value | Used by |
|---|---|---|
| `max_clip_seconds` | **30** | WS3 (auto-stop), WS2 (reject longer) |
| `target_fps` | **2.0** | WS1 (sampling), informational to WS2 |
| `video_mime` | `video/mp4` | WS3 (MediaRecorder), WS2 (accept) |
| `max_new_tokens` | **512** | WS2 (model call) |
| `greeting` | `"👋 Hey — show me something and ask."` | WS3 |

### Contract A — WS2 (backend) ↔ WS1 (vLLM model server)
- **Endpoint:** `POST http://127.0.0.1:8000/v1/chat/completions` (OpenAI-compatible, same node).
- **Model id:** `Qwen/Qwen3-VL-32B-Instruct`.
- **Video is passed as a `video_url` content part with a `file://` URL** (backend and vLLM share the
  node filesystem, so no HTTP hop needed):
```jsonc
{
  "model": "Qwen/Qwen3-VL-32B-Instruct",
  "stream": true,
  "max_tokens": 512,
  "messages": [
    { "role": "system", "content": "You are a helpful assistant. Answer in plain English about what you see." },
    { "role": "user", "content": [
      { "type": "video_url", "video_url": { "url": "file:///abs/path/turn_<id>.mp4" } },
      { "type": "text", "text": "<the user's question>" }
    ]}
  ],
  "extra_body": { "mm_processor_kwargs": { "fps": 2.0, "max_pixels": 262144, "min_pixels": 131072 } }
}
```
- WS2 consumes the **SSE token stream** (`data: {...}\n\n`, `[DONE]`) and re-streams plain text to WS3.
- WS1 guarantees the server is up at this URL with video enabled and the launch flags in §Decisions.

### Contract B — WS3 (UI) ↔ WS2 (backend)
- `GET /` → serves the single-page UI (`frontend/index.html` + assets).
- `GET /api/config` → `{"max_clip_seconds":30,"video_mime":"video/mp4","greeting":"…"}`.
- `POST /api/transcribe` → body: raw audio blob (`audio/mp4` from iOS, `audio/webm` elsewhere) as
  `multipart/form-data` field `audio`. Response: `{"text": "transcribed question"}`.
- `POST /api/turn` → `multipart/form-data`: field `video` (the MP4 clip), field `text` (string, may be
  empty). Response: a **streamed** body of answer text — chunked `text/plain` (UI reads it with
  `fetch()` + `response.body.getReader()`; **not** EventSource, because this is an authenticated POST).
  Stream ends when the body closes. On error, send a final line prefixed `\n[error] <msg>`.
- **Statelessness:** every `/api/turn` is independent (length-1). No session/history in V0.

> If any agent needs to change a contract, edit THIS section first and ping WS6, so the others
> see it. Contracts are the only coupling between parallel workstreams.

### Contract B — V0.1 additions (2026-06-30, live-validated)
The V0 shapes above are extended (all backward-compatible / additive):
- `GET /api/config` now ALSO returns `model_id` and `video_longest_side` (px the clip is normalized to)
  — for the settings modal.
- `POST /api/transcribe` now returns `{"text": "...", "asr_ms": <int>}` (ASR wall-time).
- `POST /api/turn` — **`video` is now OPTIONAL.** Cases: no video+no text → `[error] nothing to send`;
  video(+text) → video turn; text only → a text-only chat request. After the answer streams, the body
  ends with **one metrics frame**: the byte `\x1e` (U+001E, RECORD SEPARATOR) + a compact JSON object, then
  EOF. Split the body on `\x1e`: before = answer, after = metrics. `[error]` turns omit the frame.
  Metrics JSON: `{"tokens":{"system","text","video","prompt_total","output"},
  "timing_ms":{"normalize","ttft","inference_total"},"model"}`. Token split via vLLM `/tokenize`
  (`video = prompt_total − non_video`); `prompt_total`/`output` via `stream_options.include_usage`.
- UI (V0.1): markdown rendering (vendored `marked`+`DOMPurify` in `frontend/vendor/`), seamless
  page-scrolling answer (no box), Record/Hold-to-ask swapped, a settings gear modal (#7), and a per-turn
  usage modal (#8). Audio is ASR-only → no "audio tokens" in the model input (shown as ASR latency).

---

## Decisions (locked for V0)

| Area | Decision | Source |
|---|---|---|
| **Model** | `Qwen/Qwen3-VL-32B-Instruct` (dense, Apache-2.0), bf16 weights = 66.7 GB. **Served across a FULL a3mega node — TP=8, 8× H100 — for lowest latency.** Single user / single replica → the whole node (full NVSwitch mesh) works each request, minimizing prefill + decode time. ~8.3 GB/GPU of weights leaves huge KV/vision headroom. `…-Instruct-FP8` (35.5 GB) is an optional *further*-speed lever (~2× prefill). | user + research |
| **Serving runtime** | **vLLM**, OpenAI-compatible. Use a **post-March-2026 build** (target latest stable, ~v0.24.0) — a video timestamp bug was live through 0.15.1 (fixed PR #36136). **Fallback:** native HF `transformers≥4.57` + `qwen-vl-utils==0.0.14` + a FastAPI `TextIteratorStreamer`. | research |
| **Video input** | `video_url` with `file://` to the clip on the shared node. **Always pass `fps` together with `num_frames`** or older vLLM raises `AssertionError: timestamps length … should equal video length`. | research |
| **Clip / latency** | **30s max clip, fps 2.0, ~512×512/frame (~256 tok/unit) → ~8K input tokens.** Model TTFT ~1.6–2.5s at modest TP; **TP=8 on the full node pushes it lower (~1–2s, benchmark to confirm)**. Comfortably inside the "balanced ~5–10s" target. Tunable: snappy=12s, thorough=60s@fps1. | research |
| **Phone access** | **Cloudflare Tunnel** (`cloudflared`) → free HTTPS URL. HTTPS is **mandatory**: iOS `getUserMedia` is secure-context-only (plain HTTP → `navigator.mediaDevices` is `undefined`). | user + research |
| **ASR** | **Server-side faster-whisper** on the recorded audio blob (already in the `moe` env). | user |
| **iOS capture** | `MediaRecorder` yields **MP4/H.264 only** on iOS Safari (no WebM). Record `start()`→`stop()`, take the single Blob (don't rely on timeslice chunks). `<video>` needs `playsinline muted autoplay`; capture must start from a user tap. | research |
| **Streaming out** | `fetch()` + `response.body.getReader()` (parse chunks manually; `for await` over streams only lands in Safari 26.4). Spinner until first token. | research |
| **UI stack** | Vanilla single-page (HTML/CSS/JS), served by the backend. No build step. | default |
| **V0 scope** | Single-turn / length-1 / context reset; clip-based (no live stream); video-only to model; ASR for input only. | user |

### Clip-length / token math (reference)
`video_tokens ≈ (clip_s × fps / 2) × (tokens_per_unit + ~5 timestamp tokens)`; one merged unit ≈ 1s
of video at fps 2; `tokens_per_unit` ∈ [128, 768], V0 uses ~256 (≈512×512 frame via
`max_pixels≈262144`). `num_frames = round(clip_s × fps)` = 60 for the 30s cap. `--max-model-len 16384`
is the real ceiling (≈8K input + answer headroom).

---

## Global context (single source of truth)

### Infra
- GCP `poetic-avenue-438401-a7`, zone `us-east4-b`. SLURM partition `a3mega` = 8× a3-mega nodes
  `nucla3m-a3meganodeset-[0-7]` (8× H100 80 GB each). `/home` is shared NFS; `/mnt/localssd` is
  per-node local; node-to-node SSH is passwordless.
- Conda env **`moe`** (torch 2.8/cu128, transformers, faster-whisper, ffmpeg 7.1). `HF_TOKEN` in
  `~/.bashrc`. **vLLM may need an upgrade/separate env** to hit the post-March-2026 build — WS1 owns this.
- **For V0, the simplest deployment is single-node:** vLLM (WS1, full node TP=8) + backend (WS2) on the same
  a3mega node so the `file://` video path and `127.0.0.1` endpoint just work. cloudflared (WS5) runs
  on that node too.

### Conventions
- **Commits / PRs: no attribution.** No `Co-Authored-By`, no "Claude/Anthropic/AI" mentions — clean,
  professional messages. (Pinned in `~/.claude/CLAUDE.md`.)
- **Contracts are the spine.** The only coupling between parallel workstreams is the CONTRACTS section.
- **Each workstream is self-contained** in its directory (`server/`, `backend/`, `frontend/`, `scripts/`).
- **Keep your `wsN-*.md` Worklog current** as you go; flip your status row at the top when done.
- This POC currently lives as a **plain directory** under the umbrella (not yet a submodule). Don't
  add nested git repos; we'll wire the submodule when there's a remote to push to.

---

## Current state (terse — detail lives in the wsN files)
- **Scaffolding:** ✅ done — skeleton + README + this file + 6 wsN handoffs written (2026-06-30).
- **WS1–WS6:** ✅ built + integrated (2026-06-30). The full stack runs on one a3mega node
  (`nucla3m-a3meganodeset-7`): vLLM :8000 → backend+ASR :8080 → cloudflared HTTPS tunnel.
- **V0.1 feature round:** ✅ built + live-validated (2026-06-30) — text-only send, markdown + seamless
  page-flow output, swapped buttons, settings gear modal, per-turn usage modal, `asr_ms`, `\x1e` metrics
  tail. See *Contract B — V0.1 additions*. Verified text-only + video turns + metrics through the tunnel.
- **Validated server-side:** a real clip → backend → vLLM turn returns a **grounded** answer that
  **streams** token-by-token; the same works **through the public HTTPS tunnel** (streamed incrementally,
  not buffered); ASR transcribes iOS `audio/mp4` + `audio/webm` exactly.
- **Phone-tested (2026-06-30):** real iPhone over the HTTPS link — UI loads, camera live, ASR
  transcription exact, a real MediaRecorder MP4 (640×480 + rotation metadata) records + uploads. **Bug
  found + fixed:** the raw phone clip decoded to ~9000 video tokens > the 8192 encoder cache → vLLM 400,
  which a stale backend swallowed as "(no response)". Fixed by **backend ffmpeg clip-normalization** (see
  *Post-phone-test fix* below); the same iPhone clip now returns a grounded streamed answer locally and
  through the tunnel (~2–3 s).
- **Pending:** the user's own final camera/mic-permission tap-through on the phone.
- **Bring-up:** `server/serve.sh --bg` (vLLM) then `scripts/run_all.sh` (backend + tunnel, prints URL).

## How WS6 wired it (integration deltas applied 2026-06-30)
- `backend/app.py`: real WS4 ASR imported (`from asr import transcribe`, `_ASR_AVAILABLE=True`);
  `transcribe()` now runs via `fastapi.concurrency.run_in_threadpool` (it's blocking) and `asr.warmup()`
  runs at startup via a FastAPI `lifespan` handler (off the event loop, best-effort).
- Backend env = conda **`moe`** (already has fastapi/uvicorn/httpx/python-multipart + faster-whisper/
  ctranslate2/numpy + ffmpeg 7.1 — one env runs WS2 and WS4). `backend/run.sh` sets `HF_HOME` + pins
  `ASR_DEVICE_INDEX=7` so ASR shares cleanly with vLLM's TP=8.
- Clip dir `/mnt/localssd/poc/live_video_chat/turns/` is **under** vLLM's `--allowed-local-media-path
  /mnt/localssd` → `file://` loads are allowed. (No change needed; verified.)
- `scripts/run_all.sh` brings the stack up in order (assert vLLM → start backend detached → start tunnel
  detached → print URL + on-device checklist); `--status` / `--stop` / `--restart` subcommands.

## Post-phone-test fix — clip normalization (2026-06-30)
First real-iPhone turn returned "(no response)". Root cause: **vLLM does not apply `max_pixels` to
video**, so a raw 640×480 phone clip at `num_frames=60` ≈ **9000 video tokens**, exceeding the ~8192
encoder cache → HTTP **400** (the synthetic 512×512 test clip fit at 7680, which masked it). The backend
also relayed the 400 as an empty body. Fix (backend-only, **no vLLM restart**, tunnel URL preserved):
- `backend/app.py` `_normalize_clip()` — every uploaded clip is re-encoded with ffmpeg before the model
  call: downscale longest side to **512 px**, **drop audio** (video-only model), force CFR + `+faststart`,
  apply rotation. Bounds video tokens to ~6k for **any** phone resolution/orientation (~0.3 s for a 10s
  clip). Falls back to the raw clip if ffmpeg fails. Knobs in `config.py` (`NORMALIZE_*`).
- `backend/model_client.py` — dropped the per-request `extra_body.mm_processor_kwargs` (vLLM ignores
  `extra_body` over raw HTTP and doesn't honor `max_pixels` for video); fps/num_frames come from the
  launch flags, resolution from normalization. Non-200 still surfaces as `[error] vLLM returned <code>: …`
  (the silent "(no response)" was a *stale* pre-fix backend; the fresh one surfaces errors).
- Verified: the exact iPhone clip → grounded streamed answer, ~2–3 s, local **and** through the tunnel.

## Open risks / must-verify in-house
1. ~~**TTFT is estimated.**~~ **RESOLVED — measured (WS1, TP=8):** 0.19 s @2K, **0.56 s @8K** (the 30s
   contract), 0.89 s @16K. Through the full backend path: 0.6–1.8 s first token. Table in ws1 worklog.
2. ~~**vLLM flag interaction.**~~ **RESOLVED:** on vLLM 0.19.1 the launch `--mm-processor-kwargs fps=2.0`
   + `--media-io-kwargs num_frames=60` govern sampling; passing `fps` with `num_frames` avoids the
   timestamp assertion. ⚠️ Per-request `extra_body.mm_processor_kwargs` is **ignored** over the raw HTTP
   API, and `max_pixels` is **not honored for video** — so clip resolution is bounded by backend ffmpeg
   normalization, not request kwargs. (vLLM 0.19.1 not ~0.24.0: 0.20.0+ need CUDA-13/driver≥580; this node
   is driver 570/CUDA 12.8. 0.19.1 is post-fix for PR #36136.)
3. **patch factor 16 vs 14.** Model config wins under vLLM; token counts matched the budget in practice
   (6s@512px = 1596 tok, 30s@512px = 7964 tok ≈ predicted). No action needed.
4. ~~**iOS clip size / `dataavailable` quirks.**~~ **PHONE-TESTED:** real iPhone MediaRecorder produced a
   single-Blob MP4/H.264 (640×480 + rotation metadata, 5.5 MB / 10 s) that records + uploads fine; backend
   normalization handles the rotation. Remaining: the user's final camera/mic-permission tap-through.
5. ~~**TP=8 sanity.**~~ **RESOLVED:** head counts divide by 8 (text 64/8, kv 8/8, vision 16/8); TP=8 runs,
   no TP=4 fallback. ~74 GB/GPU at `--gpu-memory-utilization 0.90`.
6. ~~**Encoder-cache ceiling (~8192 video tokens).**~~ **HIT IN THE WILD + RESOLVED:** real phone clips
   (640×480 @ num_frames=60 ≈ 9000 tokens) exceeded it → 400, because vLLM doesn't downsample video to
   `max_pixels`. Fixed by backend ffmpeg normalization (→512 px ≈ ~6k tokens for any resolution). To allow
   *longer/higher-res* clips beyond that, also raise `--max-num-batched-tokens` in `server/serve.sh`.
7. **Ephemeral tunnel URL rotates** each tunnel restart — read the current one from `scripts/.tunnel_url`
   or the `run_all.sh` banner. A named tunnel (Cloudflare account) would fix the hostname (not needed V0).
