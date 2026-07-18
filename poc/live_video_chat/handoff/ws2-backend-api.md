# WS2 — Backend / orchestration API (FastAPI)

Status: **built + validated (full vLLM integration pending WS1)** · Owner/agent: WS2 build agent · Last updated: 2026-06-30

> **Start here:** read the global [`../HANDOFF.md`](../HANDOFF.md) in full, then this file. You own
> **Contract B** (UI ↔ backend) on the backend side, and you are the **client** of Contract A (vLLM)
> and the consumer of WS4's ASR module. Keep the Worklog current; flip your status row when done.

## Goal
A small **FastAPI** app that is the hub: serves the UI, accepts a recorded clip + text, runs ASR when
needed, calls the vLLM server, and **streams** the answer back to the phone. Stateless / single-turn.

## Deliverables (in `backend/`)
- `app.py` — the FastAPI app implementing Contract B.
- `model_client.py` — builds the Qwen3-VL chat request (Contract A) and relays the SSE token stream.
- `config.py` — the shared constants (served at `/api/config`).
- `asr.py` — **thin wrapper around WS4's module** (`transcribe(audio_bytes, mime) -> str`); until WS4
  lands, stub it to return a fixed string so you can build/test the rest.
- `requirements.txt` / run notes; a `run.sh` that starts uvicorn.
- Static mount that serves WS3's `frontend/` at `/`.

## The contract you must honor (from global → Contract B)
- `GET /` → serve `frontend/index.html` (+ assets).
- `GET /api/config` → `{"max_clip_seconds":30,"video_mime":"video/mp4","greeting":"…","max_new_tokens":512}`.
- `POST /api/transcribe` → multipart field `audio` (blob) → `{"text": "..."}`.
- `POST /api/turn` → multipart fields `video` (MP4) + `text` (string, may be empty) → **streamed**
  answer text (chunked `text/plain`; UI reads via `getReader()`). End the stream when done; on error
  append `\n[error] <msg>`.
- **Stateless:** each `/api/turn` is independent. No history.

## Turn handling (the core path)
1. Receive `video` + `text`. Reject if no video, or if clip longer than `max_clip_seconds` (best-effort).
2. Save the clip to a node-local path, e.g. `/mnt/localssd/poc/live_video_chat/turns/turn_<uuid>.mp4`
   (this path is what vLLM reads via `file://`). Clean up old files opportunistically.
3. Determine the prompt: if `text` non-empty use it; else (voice-only turn) you may transcribe the clip's
   audio — but the **primary ASR path is the UI's separate `/api/transcribe` call**, so `text` is usually
   already filled. Keep it simple: use `text`; if empty, send a default like "Describe what you see and
   answer any implicit question."
4. Build Contract-A request (`file://` clip + prompt, `stream:true`, `extra_body.mm_processor_kwargs`),
   POST to `http://127.0.0.1:8000/v1/chat/completions`.
5. **Relay the stream:** parse vLLM SSE chunks, extract `choices[].delta.content`, and yield the plain
   text to the client as it arrives (FastAPI `StreamingResponse`). Flush promptly so first token reaches
   the phone fast.

## Suggested steps
1. Scaffold FastAPI + uvicorn; mount `frontend/` static; implement `/api/config`.
2. Implement `/api/transcribe` against WS4's `transcribe()` (stub first).
3. Implement `model_client.py` streaming relay against WS1 (use a sample MP4 + curl WS1 first).
4. Implement `/api/turn`; test with `curl -F video=@sample.mp4 -F text='what is this?'` and confirm streaming.
5. Coordinate the exact streaming format with WS3 (plain chunked text is the default).

## Key files & paths
- `backend/app.py`, `backend/model_client.py`, `backend/asr.py`, `backend/config.py`.
- Clip scratch dir on local SSD (shared with WS1's vLLM): `/mnt/localssd/poc/live_video_chat/turns/`.
- vLLM endpoint: `http://127.0.0.1:8000/v1/chat/completions`.

## Gotchas / decisions
- Run **on the same node as WS1** so `file://` clip paths and `127.0.0.1` work with no extra plumbing.
- Use an **async HTTP client** (httpx) and stream the upstream response; don't buffer the whole answer.
- Multipart video uploads are tens of MB — set generous body-size limits; confirm they pass through the
  cloudflared tunnel (WS5) with WS6.
- CORS: the UI is same-origin (served by you), so CORS shouldn't be needed — keep it that way.
- Keep `config.py` the single source of truth for the shared constants; the UI reads `/api/config`.

## Definition of done
`/`, `/api/config`, `/api/transcribe`, `/api/turn` all working; `/api/turn` streams a real answer from a
real clip via WS1; ASR wired to WS4; `run.sh` documented; Contract B confirmed with WS3 and WS6.

## Worklog
- 2026-06-30 — file created (scaffolding). Not started.
- 2026-06-30 — **BUILT** the FastAPI hub. Files in `backend/`:
  - `config.py` — single source of truth for shared constants (`max_clip_seconds=30`,
    `target_fps=2.0`, `video_mime=video/mp4`, `greeting`, `max_new_tokens=512`) + Contract-A
    client settings (vLLM URL, model id, system prompt, mm_processor_kwargs pixels, timeouts,
    `TURNS_DIR`, `FRONTEND_DIR`). `public_config()` is what `/api/config` serves.
  - `model_client.py` — `build_request_payload()` assembles the exact Contract-A body
    (`video_url` `file://` clip + text, `stream:true`, `max_tokens`, `extra_body.mm_processor_kwargs`
    fps/max_pixels/min_pixels). `stream_answer()` POSTs with async httpx and relays the SSE token
    stream (`data:` lines, `[DONE]`), extracting `choices[].delta.content` and yielding plain text
    as it arrives. Graceful `\n[error] ...` on connect/timeout/non-200. `health_check()` for `/healthz`.
  - `app.py` — Contract B: `GET /api/config`, `POST /api/transcribe` (multipart `audio` → `{text}`
    via WS4's `transcribe()`, imported defensively with an empty-string fallback so we don't block
    on WS4), `POST /api/turn` (multipart `video`+`text` → saves `turn_<uuid>.mp4` to TURNS_DIR,
    best-effort ffprobe duration reject > max_clip_seconds, empty text → default prompt, streams the
    answer as chunked `text/plain` with `X-Accel-Buffering: no`). Static mount of `frontend/` at `/`
    (`html=True`), mounted LAST so it never shadows `/api/*`. Plus `GET /healthz`.
  - `requirements.txt` (fastapi/uvicorn/httpx/python-multipart — all already in `moe`), `run.sh`
    (uvicorn on **port 8080**, activates `moe`, makes TURNS_DIR).
  - Did NOT create `backend/asr.py` (WS4-owned); import it defensively.
- 2026-06-30 — **VALIDATED** in the `moe` env. vLLM (WS1) is not up yet, so `/api/turn` was tested
  against a local mock of the OpenAI streaming endpoint on 127.0.0.1:8000 (and against the real
  down-server path):
  - `GET /api/config` → `{"max_clip_seconds":30.0,"target_fps":2.0,"video_mime":"video/mp4","greeting":"…","max_new_tokens":512}`.
  - `POST /api/transcribe` (no WS4 → fallback) → `{"text":""}`; missing `audio` field → 422.
  - `POST /api/turn` → **truly streams**: 11 chunks arrived ~0.15s apart (matching the mock's
    spacing), proving incremental relay, not buffered. Full answer reassembled correctly.
  - Reject paths: 31s clip → `[error] clip too long: 31.0s > 30s max`; empty `text` → default prompt
    still streams; missing `video` → 422; **vLLM down → graceful `[error] could not reach the model
    server …` body at HTTP 200** (UI's single getReader path handles it).
  - Clip saved to `/mnt/localssd/poc/live_video_chat/turns/turn_<uuid>.mp4` (the file:// path vLLM reads).
  - Cross-checked WS3's `frontend/app.js` (landed concurrently): it calls `/api/config`, `/api/transcribe`
    (field `audio`), `/api/turn` (fields `video`+`text`), and reads the stream via
    `response.body.getReader()` + `TextDecoder` — **Contract B matches end-to-end with the real UI code.**
  - Test servers stopped; node left clean.
- **Pending:** full integration test against WS1's real vLLM (a real clip → live token stream) once
  WS1 is up at 127.0.0.1:8000. The Contract-A payload shape is confirmed; just needs the live server.
- 2026-06-30 — **V0.1 backend extension** (4 contract changes, validated LIVE against the running
  vLLM 0.19.1 on `nucla3m-a3meganodeset-7`; backend restarted, vLLM + tunnel untouched):
  1. **Text-only turn — `video` is now OPTIONAL on `POST /api/turn`.** `video: Optional[UploadFile]
     = File(None)`, `text: str = Form("")`. Cases: no video AND no text → streamed
     `[error] nothing to send (record a clip or type/speak a question)`; video present → existing
     save→normalize→video+text path; no video but text → a **text-only** chat request (system + user
     text, NO `video_url` part) via new `model_client.build_text_payload(prompt)`. `stream_answer()`
     now takes `clip_path: Optional[str]` (None → text-only); `build_request_payload(None, prompt)`
     delegates to `build_text_payload`. An empty-file upload + real text degrades to a text-only turn.
     Stateless / length-1 preserved.
  2. **`/api/config` — added `model_id` (=`config.MODEL_ID`) and `video_longest_side`
     (=`config.NORMALIZE_LONGEST_SIDE`, the px the model is actually fed).** Existing keys unchanged.
     Final shape: `{max_clip_seconds,target_fps,video_mime,greeting,max_new_tokens,model_id,
     video_longest_side}`. `config.py` stays the single source of truth.
  3. **`/api/transcribe` — now returns `{"text":"...","asr_ms":<int>}`** (wall-time around
     `transcribe()`). Empty-text fallback kept; `asr_ms` reported even on the error path.
  4. **Per-turn usage-metrics tail on `/api/turn` (#8).** After the answer fully streams, the body
     gets ONE final frame: the RECORD-SEPARATOR byte `\x1e` (U+001E) + a compact JSON metrics object,
     then the stream ends. **Body = `<answer text>\x1e<json>`** — the frontend splits on `\x1e`
     (text before = answer, after = metrics). Skipped on an `[error]`. JSON shape (all ints):
     `{"tokens":{"system":S,"text":T,"video":V,"prompt_total":P,"output":O},
       "timing_ms":{"normalize":N,"ttft":F,"inference_total":I},"model":"<id>"}`.
     - `prompt_total`/`output` from `stream_options.include_usage=true` (the FINAL chunk has
       `choices:[]` + `usage`).
     - `system`/`text`/`video` from the vLLM `/tokenize` endpoint
       (`model_client.token_breakdown()`): `system`=tokenize(SYSTEM_PROMPT).count,
       `text`=tokenize(prompt).count, `non_video`=tokenize(messages,add_generation_prompt=true).count,
       `video`=max(0, prompt_total − non_video) (0 for text-only).
     - `normalize`=ffmpeg wall-ms (0 if text-only); `ttft`=ms POST→first content token;
       `inference_total`=ms POST→stream done. Best-effort: any sub-step failure still emits a frame.
  - **VALIDATED LIVE (curl):**
    - `/api/config` → `…,"model_id":"Qwen/Qwen3-VL-32B-Instruct","video_longest_side":512`.
    - `/api/transcribe` (sample_short.m4a) → `{"text":"Now I want to return to the conservation of
      mechanical energy.","asr_ms":739}`.
    - text-only turn (`-F text='what is the capital of France'`, no video) → `The capital of France
      is Paris.` then `\x1e{"tokens":{"system":15,"text":6,"video":0,"prompt_total":34,"output":8},
      "timing_ms":{"normalize":0,"ttft":35,"inference_total":82},"model":"Qwen/Qwen3-VL-32B-Instruct"}`.
    - real video turn (a turns/*.mp4 phone clip + `text='What do you see in this video?'`) → grounded
      streamed description, then `\x1e{"tokens":{"system":15,"text":8,"video":6000,"prompt_total":6036,
      "output":294},"timing_ms":{"normalize":220,"ttft":496,"inference_total":2473},"model":"…"}`
      (system+text+video=6023 ≈ prompt_total 6036, the 13-token gap = chat-template scaffolding).
    - no-video+no-text and whitespace-only text → `[error] nothing to send …`, no `\x1e` tail.
  - **Restart procedure used (no self-matching pkill):** kill the actual uvicorn pid bound to :8080
    (note: `run.sh` activates conda then `exec uvicorn`, but the conda shim can leave uvicorn as a
    *child* of the `setsid bash run.sh` launcher — so the pidfile must hold the **uvicorn** pid, read
    from `ss -ltnp | grep :8080`, not the launcher pid), `setsid bash run.sh >LOG 2>&1 </dev/null &`,
    write the uvicorn pid to `/mnt/localssd/.hf-home/backend_uvicorn.pid`, poll `/healthz` until 200.
  - Files touched: `backend/app.py`, `backend/config.py`, `backend/model_client.py` (+ this worklog).
    vLLM (:8000) and the cloudflared tunnel were left running and untouched.
