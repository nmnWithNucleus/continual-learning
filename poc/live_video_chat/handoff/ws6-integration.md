# WS6 — Integration (the stitcher)

Status: **done (server-side validated; on-device iPhone leg pending the user)** · Owner/agent: WS6 integration agent · Last updated: 2026-06-30

> **Start here:** read the global [`../HANDOFF.md`](../HANDOFF.md) in full, then this file. You don't own
> a single component — you own the **seams**: that the contracts actually hold and the whole loop works
> on my phone. Start once WS1–WS5 have something runnable (you can begin wiring against stubs earlier).
> Keep the Worklog current; flip rows in the global index as pieces land.

## Goal
Bring WS1–WS5 together into one running system and prove the **end-to-end turn on a real iPhone**:
greeting → ask by voice/text → record clip → Send → streamed grounded answer → context resets. Then
tidy and update the docs to "working."

## Deliverables
- A top-level `run.sh` (in `scripts/` or repo root) that brings up the stack on one node in order:
  **vLLM (WS1) → backend+ASR (WS2/WS4) → cloudflared (WS5)**, and prints the phone URL.
- A short **end-to-end test checklist** (below) executed on a real iPhone, with results noted here.
- Final updates to [`../README.md`](../README.md) and [`../HANDOFF.md`](../HANDOFF.md): statuses → done,
  the real runbook, the measured latency, and any contract changes that happened during the build.

## Integration order & seams to verify
1. **Contract A (WS2↔WS1):** backend can POST a `file://` clip + prompt and receive a streamed answer.
   Confirm `fps`+`num_frames` consistency (no timestamp assertion) and that clip paths are visible to vLLM.
2. **Contract B (WS3↔WS2):** UI's `/api/config`, `/api/transcribe`, `/api/turn` all behave; streaming
   reaches the browser incrementally.
3. **Tunnel (WS5):** everything above works **through the HTTPS tunnel from the phone**, including the
   big video POST and incremental streaming (watch for tunnel/proxy buffering).
4. **ASR (WS4):** voice → text fills the box on the actual device (iOS `audio/mp4`).

## End-to-end test checklist (run on the iPhone)
- [ ] Open the HTTPS link → hello greeting shows.
- [ ] Tap 🎤, speak a question → text appears in the box (editable).
- [ ] Tap Record → camera preview, records, **auto-stops at 30s**.
- [ ] Tap Send → spinner appears, then the answer **streams** in token-by-token.
- [ ] Answer is **grounded in the video** (ask about something only visible in the clip to confirm).
- [ ] Measure record→first-token latency; confirm it's in the ~2.5–4s (≤10s) range.
- [ ] Start a new turn → previous context is gone (length-1 confirmed).

## Gotchas / decisions
- Keep everything **on one a3mega node** for V0 (so `file://` + `127.0.0.1` hold). If anything is split
  across nodes, the clip must be served over HTTP to vLLM instead — document it.
- The most likely failure modes: (a) HTTPS missing → camera dead; (b) streaming buffered somewhere
  (backend, cloudflared) → answer arrives all-at-once; (c) timestamp assertion if `fps` not passed with
  `num_frames`; (d) iOS MP4 quirks. The wsN gotcha sections cover each.
- Don't expand scope: V0 is single-turn, clip-based, video-only. Park multi-turn/streaming for V1/V2.

## Definition of done
The checklist passes on a real iPhone; `run.sh` brings the stack up reproducibly; README/HANDOFF reflect
the working system with the real runbook and measured latency.

## Integration fixes applied (2026-06-30)
1. **ASR wiring (`backend/app.py`).** `from asr import transcribe` now resolves to the real WS4 module
   (`_ASR_AVAILABLE=True`, verified). `transcribe()` is called via
   `fastapi.concurrency.run_in_threadpool` (it's sync/blocking — ffmpeg + faster-whisper — must not stall
   the event loop). Added a FastAPI **`lifespan`** handler that runs `asr.warmup()` at startup (off the
   event loop, best-effort) so the first real `/api/transcribe` doesn't pay the model-load cost.
2. **Backend env.** Runs in conda **`moe`**, which already has BOTH stacks: fastapi/uvicorn/httpx/
   python-multipart (WS2) + faster-whisper/ctranslate2/numpy + ffmpeg 7.1 (WS4). No separate install
   needed; `backend/requirements-asr.txt` deps are all present. `backend/run.sh` documents this, exports
   `HF_HOME=/mnt/localssd/.hf-home`, and pins `ASR_DEVICE_INDEX=7` so ASR shares the node with vLLM TP=8.
3. **Clip dir.** `/mnt/localssd/poc/live_video_chat/turns/` is **under** vLLM's
   `--allowed-local-media-path /mnt/localssd` → `file://` loads are permitted. Verified, no change needed.
4. **`scripts/run_all.sh`** (new). Asserts vLLM up → starts backend detached (nohup+pidfile) → starts
   cloudflared detached → prints the phone URL + on-device checklist. `--status` / `--stop` / `--restart`.
   Backend + tunnel reparent to init (PPID 1) so they survive the launching shell exiting.

## End-to-end test results (server-side — proven up to the device)
- [x] **Real grounded turn, local** (`POST :8080/api/turn`, `server/samples/sample_counter.mp4`): answer
      correctly read "numbers 1 through 6, white on a dark blue square, bright blue background" — grounded
      in clip-only detail. **Streamed** (read byte-by-byte). **First token 0.62 s**, total 0.99 s.
- [x] **Real 30s clip** (generated H.264 512×512, ~3.7 MB): grounded multi-paragraph answer (color bars,
      moving lines, gray cross/square, ms timer + frame counter — all real). **First token 1.81 s**, total
      3.99 s. **No HTTP 400** → encoder-cache ceiling (~8192 tok) NOT exceeded by the 30s contract; **no
      serve.sh/config change required.**
- [x] **Through the public HTTPS tunnel** (large 30s clip): HTTP 200 `server: cloudflare`, grounded answer
      arrived in **326 incremental reads** (streamed, not buffered). **First token 0.94 s over HTTPS**,
      total 3.15 s. Confirms WS5's chunked-framing finding (uvicorn/Starlette frames it correctly).
- [x] **ASR** (`POST /api/transcribe`, WS4 fixtures): exact transcripts for iOS `audio/mp4` (AAC) AND
      `audio/webm` (Opus) — JFK + "conservation of mechanical energy" clips. Sub-300 ms warm.
- [x] **UI served** at `/`; `/api/config` returns the shared constants; `/healthz` reaches vLLM.
- [ ] **On a real iPhone** (the user's device — the only leg I can't run): camera/mic permission prompts,
      MediaRecorder MP4 output, auto-stop at 30s, the touch flow. Checklist below is for the user.

## Stack state (left running for the user)
- vLLM (WS1): UP, `127.0.0.1:8000`, pid in `/mnt/localssd/.hf-home/vllm_serve.pid` (managed by serve.sh).
- backend (WS2/WS4): UP, `:8080`, pid in `/mnt/localssd/.hf-home/backend_uvicorn.pid`, log alongside.
- tunnel (WS5): UP, pid in `scripts/.tunnel.pid`, URL in `scripts/.tunnel_url`. **URL rotates per restart.**
- Restart everything: `server/serve.sh --bg` (if vLLM down) then `scripts/run_all.sh`.
- Stop backend+tunnel: `scripts/run_all.sh --stop` (leaves vLLM up).

## Worklog
- 2026-06-30 — file created (scaffolding). Not started.
- 2026-06-30 — **DONE (server-side).** Read all handoffs; verified WS1 vLLM up, frontend files intact
  (index.html/app.js/styles.css all present), clip dir under the allowed root, `moe` env has all deps.
  Applied the ASR wiring fixes to app.py (run_in_threadpool + lifespan warmup). Wrote `scripts/run_all.sh`.
  Brought the stack up detached and ran the live tests above (local turn, 30s clip / encoder ceiling,
  through-tunnel turn, ASR) — all PASS. Updated README.md + HANDOFF.md (statuses → done, real runbook,
  measured TTFT table, vLLM-0.19.1/driver-cap note, `--allowed-local-media-path` requirement, encoder
  ceiling note). Remaining: the on-device iPhone leg, which needs the user's physical device.
