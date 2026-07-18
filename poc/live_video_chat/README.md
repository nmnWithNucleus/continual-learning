# Live Video Chat — a mini POC

> A side POC under the `continual_learning/poc/` umbrella, sibling to **`live_stream_stability`**
> and **`recursive_finetuning_stability`**. This one is a small, self-contained prototype — not a
> training run.

**What I'm building.** A phone-accessible web app that reproduces, with our **own open-weight VLM**,
the experience I had with ChatGPT's live-video voice chat: I open the app on my iPhone, point the
camera at something, ask a question (by voice or text), and the model answers — with a **live video
channel feeding the inference**. The model is **self-hosted Qwen3-VL-32B-Instruct (dense)** on our
H100s; the answer **streams** back into an output box as it's generated.

> **Note on the model.** Across this POC (and going forward in `live_stream_stability`) the base VLM
> is **Qwen3-VL-32B (dense)**, replacing Qwen2.5-VL-32B.

---

## V0 scope (deliberately minimal)

V0 is the smallest thing that proves the loop end-to-end on my phone:

- **Single-turn (length-1).** One question → one answer. The model context **resets every turn** —
  no chat history yet. (Multi-turn is a later version.)
- **Clip-based, not a live stream.** Each turn records a **fixed-max-length video clip** (~30s) which
  is sent whole to the model. (True live streaming is a later version.)
- **Video-only into the model.** The video clip is the visual modality. **Audio is *not* sent to the
  model** — it's used only inside the UI for **speech-to-text** so I can ask by voice.
- **Phone-reachable over HTTPS.** Open a link on the iPhone; it greets me with a hello message and
  I'm chatting.
- **Streaming answer.** A spinner shows while the model thinks; then tokens stream into an output box.

The open question I had to settle — *how long can the clip be?* — is answered below.

---

## Where this stands

| Workstream | What | Status |
|---|---|---|
| **Scaffolding** | Plan, README, global HANDOFF, per-agent handoffs | ✅ done (2026-06-30) |
| **WS1 — Inference server** | Host Qwen3-VL-32B on vLLM, OpenAI API, video input | ✅ done — vLLM 0.19.1, TP=8, up at `127.0.0.1:8000` |
| **WS2 — Backend / API** | FastAPI: receive clip+text → ASR → model → stream tokens | ✅ done — `:8080`, real vLLM turn streams |
| **WS3 — Frontend / UI** | Phone web app: camera, mic→ASR, streaming output box | ✅ done · phone-tested (UI/camera/ASR/record/upload) |
| **WS4 — ASR module** | faster-whisper: audio → text | ✅ done — wired into `/api/transcribe` |
| **WS5 — Tunnel / HTTPS** | cloudflared: expose to iPhone over HTTPS | ✅ done — public HTTPS, streaming verified |
| **WS6 — Integration** | Wire all, phone end-to-end test, finalize | ✅ wired + live-tested (local + tunnel) |

**Phone-tested (2026-06-30).** On a real iPhone over the HTTPS link: UI loads, camera live, ASR
transcription exact, a real MP4 clip records + uploads. The first turn surfaced a bug — a raw 640×480
phone clip decodes to ~9000 video tokens, over vLLM's ~8192 encoder cache, so the model 400'd and the
(then-stale) backend showed "(no response)". **Fixed** by normalizing every clip with ffmpeg before the
model call (downscale longest side to 512 px, drop audio) — the same iPhone clip now returns a grounded
streamed answer (~2–3 s, local and through the tunnel).

**V0.1 feature round (2026-06-30, live-validated).** Text-only send (video optional — type/speak with no
clip); **markdown rendering** of answers (vendored `marked`+`DOMPurify`); a **seamless ChatGPT-style
output** that flows down the page (no boxed scroller); Record/Hold-to-ask swapped; a **settings ⚙ modal**
(model, fps, max length, resolution); and a **per-turn usage modal** (system/video/text input tokens,
output tokens, ASR/TTFT/inference timings) fed by an `\x1e` metrics tail on `/api/turn`. The only leg
left to *you* is the final camera/mic-permission tap-through. Live working state, the API contracts, and
the decisions live in [`HANDOFF.md`](HANDOFF.md) + [`handoff/`](handoff/). This README is the stable overview.

---

## Runbook (bring up the stack on node `nucla3m-a3meganodeset-7`)

Everything runs on **one a3mega node** so `file://` clip paths and `127.0.0.1` resolve.

```bash
cd poc/live_video_chat

# 1. vLLM (WS1) — slow model load (~3-4 min); start it first, then poll until UP.
server/serve.sh --bg
server/serve.sh --status          # repeat until: UP — http://127.0.0.1:8000/v1

# 2 + 3. Backend (WS2/WS4) + cloudflared tunnel (WS5), both detached.
scripts/run_all.sh                # asserts vLLM up → starts backend → starts tunnel
                                  # → prints the phone https://… URL + on-device checklist

scripts/run_all.sh --status       # vLLM / backend / tunnel state + current URL
scripts/run_all.sh --stop         # stops backend + tunnel (leaves vLLM up)
```

The backend and tunnel are started **detached** (nohup + pidfile), so they survive the shell exiting —
the phone keeps working after you walk away. The quick-tunnel `https://…trycloudflare.com` URL **rotates
on each tunnel restart**; read the current one from `scripts/.tunnel_url` (or the `run_all.sh` banner).

**Measured (server-side, vLLM 0.19.1 / TP=8 / 8× H100):**

| Path | Clip | First token | Total | Grounded? |
|---|---|---|---|---|
| backend → vLLM (local) | 6s counter | **0.62 s** | 0.99 s | yes (read "1–6", blue bg) |
| backend → vLLM (local) | 30s @512px (~3.7 MB) | **1.81 s** | 3.99 s | yes (color bars, timer, counter) |
| **through HTTPS tunnel** | 30s @512px | **0.94 s** | 3.15 s | yes — streamed in 326 incremental reads |
| ASR `/api/transcribe` | iOS `audio/mp4` + `audio/webm` | — | sub-300 ms (warm) | exact transcript |
| **real iPhone clip** (post-fix) | 640×480 → normalized | **~1 s** | ~2–3 s | yes — "two monitors, curved screen w/ code…" |

All well inside the ~2.5–10 s "feels responsive" target. Note vLLM's **~8192-token encoder-cache
ceiling**: it does *not* downsample video to `max_pixels`, so the backend **normalizes every clip with
ffmpeg** (longest side → 512 px, audio dropped) before the model call, keeping any phone clip at ~6k
tokens. Without that, a raw 640×480 phone clip is ~9000 tokens and 400s (see HANDOFF → *Post-phone-test
fix*).

---

## How it works

```
 iPhone (Safari, HTTPS)                 one a3mega node
 ┌───────────────────┐    cloudflared   ┌──────────────────────────┐      ┌───────────────────────┐
 │  Web UI            │◄────HTTPS───────►│  Backend (FastAPI)        │      │  vLLM                 │
 │  camera · mic·ASR  │                  │  /api/transcribe (ASR)    │ file://clip + prompt        │
 │  text box · output │                  │  /api/turn (stream) ──────┼─────►│  Qwen3-VL-32B (TP=8)  │
 └───────────────────┘                  │  faster-whisper           │◄─────┤  video → token stream │
                                         └──────────────────────────┘      └───────────────────────┘
```

A turn: **🎤 speak → text box** (ASR) · **Record → 30s MP4 clip** (auto-stop) · **Send** →
backend builds the Qwen3-VL request (`file://` clip + question, `stream=true`) → **tokens stream back**
into the output box. Then context resets for the next turn.

---

## Key decisions (settled by research)

| Area | Decision |
|---|---|
| **Model** | `Qwen/Qwen3-VL-32B-Instruct` (dense, Apache-2.0), bf16. **Served on a full a3mega node — TP=8, 8× H100 — for lowest latency** (single user → the whole node works each request). FP8 is an optional *further*-speed lever. |
| **Serving** | **vLLM 0.19.1** (CUDA-12 build), OpenAI-compatible, video-enabled. *Note:* this node's driver is 570 / CUDA 12.8, and vLLM 0.20.0+ wheels link `libcudart.so.13` (CUDA 13, driver ≥580). 0.19.1 is the **highest CUDA-12 release** and is **post-fix** for the Qwen3-VL timestamp bug (PR #36136, merged 2026-03-11). Going to ~0.24.0 would need a driver upgrade. |
| **Local media** | vLLM is launched with **`--allowed-local-media-path /mnt/localssd`** — it blocks `file://` media by default. The backend therefore writes clips under `/mnt/localssd/poc/live_video_chat/turns/` (inside the allowed root). Clips elsewhere (NFS `/home`, `/tmp`) would 400. |
| **Clip / latency** | **30s max · fps 2.0 → ~0.6–1.8 s to first token (measured, TP=8)** — ~2–4 s record→stream total. Every clip is **ffmpeg-normalized to ≤512 px / audio-dropped** in the backend → ~6k video tokens for any phone resolution, under the **~8192-token encoder-cache ceiling** (vLLM doesn't downsample video to `max_pixels`, so this is required — a raw 640×480 clip is ~9000 tokens and 400s). Tunable snappy (12s) / thorough (60s, also needs `--max-num-batched-tokens` raised). |
| **Phone access** | **Cloudflare Tunnel** → HTTPS (required for the camera API on iOS). |
| **Speech-to-text** | **Server-side faster-whisper** on the recorded audio. |
| **iOS capture** | `MediaRecorder` → **MP4/H.264** (no WebM on iOS); single-Blob capture; `<video> playsinline muted`. |
| **Output streaming** | `fetch()` + `getReader()` (not EventSource); spinner until first token. |
| **UI** | Vanilla single-page, no build step, served by the backend. |

**The clip-length answer.** Qwen3-VL turns video into ~`(clip_s × fps / 2) × tokens_per_unit` vision
tokens (one merged unit ≈ 1s at fps 2). At 30s · fps 2 · ~256 tokens/unit that's **~8K input tokens** —
measured at **7964 prompt tokens** for a real 30s @512px clip. **Measured** TTFT on the full a3mega node
(TP=8, 8× H100): **0.56 s for an 8K-token clip** in isolation; ~0.6–1.8 s through the full backend path.
Comfortably inside the "feels responsive" budget. The real cap is the **encoder-cache ceiling (~8192
video tokens)**; since vLLM doesn't downsample video to `max_pixels`, the backend normalizes each clip to
≤512 px (→ ~6k tokens) so any phone resolution stays under it (raising `--max-num-batched-tokens` lifts
the cap further). Full derivation, the vLLM flags, and the per-input-size TTFT table are in
[`HANDOFF.md`](HANDOFF.md) → *Decisions* / *Clip-length math* / *Post-phone-test fix*.

---

## Repository layout

```
poc/live_video_chat/
├── README.md                    ← this file (human-facing overview)
├── HANDOFF.md                   ← global agent canvas: architecture, CONTRACTS, decisions, infra
├── handoff/                     ← one working file per workstream (agent picks up global + its file)
│   ├── ws1-inference-server.md
│   ├── ws2-backend-api.md
│   ├── ws3-frontend-ui.md
│   ├── ws4-asr.md
│   ├── ws5-tunnel.md
│   └── ws6-integration.md
├── server/                      ← WS1: vLLM launch + serving (serve.sh, health check, contract notes)
├── backend/                     ← WS2 + WS4: FastAPI app + faster-whisper ASR
├── frontend/                    ← WS3: index.html + app.js + styles (the phone UI)
└── scripts/                     ← WS5: cloudflared config + run helpers
```

---

## Build model (how the work is parallelized)

Five independent workstreams (**WS1–WS5**) build against pinned **contracts** in `HANDOFF.md`, so
agents don't block each other; **WS6** stitches them together and runs the phone end-to-end test.
Each agent is handed the **global `HANDOFF.md` + its one `handoff/wsN-*.md`** and can start cold.

---

## Roadmap (beyond V0)

- **V1 — multi-turn chat.** Keep conversation history; the model sees prior turns (text, maybe prior clips).
- **V2 — live streaming.** Replace fixed clips with a continuous video channel into inference.
- **V3 — audio modality into the model** (not just ASR), echoing the `live_stream_stability` audio track.
- **Polish.** Nicer UI, multiple model replicas for concurrency, latency tuning, on-device niceties.

---

## Relationship to the umbrella

Sibling to `live_stream_stability` (continual-pretraining a VLM on a 35-day livestream) — this POC
shares its **Qwen3-VL-32B** base-model choice and is a quick experiential-interaction prototype rather
than a training run. It currently lives as a **plain directory** under `poc/`; it can become its own
submodule (like the siblings) once there's a remote repo to push to.
