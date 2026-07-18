# WS3 — Frontend / UI (the phone web app)

Status: **built; served + Contract-B confirmed by WS6 (real on-device iOS test pending the user)** · Owner/agent: WS3 build agent · Last updated: 2026-06-30

> **Start here:** read the global [`../HANDOFF.md`](../HANDOFF.md) in full, then this file. You own
> **Contract B** (UI ↔ backend) on the UI side, plus all the iOS Safari capture details. Keep the
> Worklog current; flip your status row when done.

## Goal
A **miniature single-page web app** (vanilla HTML/CSS/JS, no build step) that runs in **iOS Safari**,
opens the camera, lets me ask by voice or text, sends a clip + question, and **streams** the answer
into an output box. It should feel like a tiny chat app.

## Deliverables (in `frontend/`)
- `index.html`, `app.js`, `styles.css` — served by WS2 at `/`.
- Works on a real iPhone over the cloudflared HTTPS URL (camera requires HTTPS).

## UI / UX spec (V0)
- **On load:** fetch `GET /api/config`; show the **hello greeting** in the output area.
- **Camera preview:** a `<video autoplay playsinline muted>` element showing the live camera.
  `getUserMedia({video:true, audio:false})` for the preview (audio for ASR is captured separately).
- **Ask by voice (🎤):** push-to-talk button. Record a short **audio-only** blob (`MediaRecorder` on a
  `getUserMedia({audio:true})` stream), `POST /api/transcribe`, put the returned text into the **editable
  text box**. (User can also just type.)
- **Record (video):** start `MediaRecorder` on the camera stream → record an **MP4/H.264** clip;
  **auto-stop at `max_clip_seconds`** (show a countdown/elapsed indicator); allow manual stop. Keep the
  resulting Blob.
- **Send:** `POST /api/turn` as `multipart/form-data` with `video` (the MP4 Blob) + `text` (the box).
  Disable Send until there's a clip.
- **Output box:** the moment Send is tapped, show a **spinner/“thinking…”** state. Stream the response
  with `fetch()` + `response.body.getReader()`, appending text as it arrives; hide the spinner on first
  token. On stream end, re-enable controls. (V0 resets — no transcript history kept.)

## The contract you consume (from global → Contract B)
`GET /api/config`, `POST /api/transcribe` (field `audio`), `POST /api/turn` (fields `video`+`text`,
streamed text response). Same-origin (served by WS2) → no CORS needed.

## iOS Safari must-knows (from research — build to these)
- **HTTPS is mandatory.** On plain HTTP, `navigator.mediaDevices` is `undefined` and `getUserMedia`
  throws. Dev on `localhost`/`127.0.0.1` works; on the phone you go through WS5's HTTPS tunnel.
- **MediaRecorder → MP4/H.264 only** on iOS (no WebM/VP8/VP9). Feature-detect with
  `MediaRecorder.isTypeSupported('video/mp4')` and request `mimeType:'video/mp4'`.
- **Capture pattern:** call `start()` **without** a timeslice and read the single Blob in `onstop`. Do
  **not** rely on periodic `dataavailable` chunks (on iOS it commonly fires once).
- `<video>` needs `playsinline muted autoplay` or iOS hijacks fullscreen / won't autoplay.
- Capture must be **initiated from a user tap** (gesture requirement). Avoid two concurrent
  `getUserMedia` calls fighting over the mic — prefer separate, short-lived audio capture for ASR.
- **Streaming read:** use `response.body.getReader()` + manual decode; `for await...of` over streams only
  exists in Safari ≥ 26.4. Don't use `EventSource` (it can't POST / set auth headers).
- Measure real clip sizes on the device; tens of MB for 30s is expected.

## Suggested steps
1. Build the static page + layout (preview, text box, 🎤, Record, Send, output). Mock the API first.
2. Wire camera preview + video recording with auto-stop; verify the MP4 Blob on a real iPhone.
3. Wire 🎤 audio capture + `/api/transcribe` → fill text box.
4. Wire Send + streaming read from `/api/turn`; spinner → streamed tokens.
5. Polish: greeting, disabled/enabled states, a simple elapsed/countdown, error display.

## Key files & paths
- `frontend/index.html`, `frontend/app.js`, `frontend/styles.css` (served by WS2 at `/`).

## Gotchas / decisions
- Keep it dependency-free (no React/build). One small `app.js`.
- Read `max_clip_seconds` from `/api/config`, don't hardcode (one source of truth).
- Test in real iOS Safari early via WS5's tunnel — desktop Chrome will hide the iOS-only quirks.

## Definition of done
On a real iPhone over HTTPS: greeting shows; voice fills the text box; a ≤30s clip records and
auto-stops; Send streams the model's answer into the output box with a spinner first; controls reset for
the next turn. Contract B confirmed with WS2/WS6.

## Worklog
- 2026-06-30 — file created (scaffolding). Not started.
- 2026-06-30 — **Built the V0 web app** (`frontend/index.html`, `app.js`, `styles.css`), vanilla, no
  build step. Implements Contract B end-to-end:
  - On load `GET /api/config`; greeting shown in the output box; `max_clip_seconds` read from config
    (never hardcoded — defaults only used if the fetch fails).
  - Camera preview via `<video autoplay playsinline muted>` + `getUserMedia({video:{facingMode:environment},audio:false})`,
    started from a user tap (tap-to-start overlay) per the iOS gesture requirement.
  - 🎤 push-to-talk (pointer/touch hold): short audio-only `MediaRecorder` (single-Blob, no timeslice),
    `POST /api/transcribe` (multipart field `audio`), returned `text` placed in the editable box.
  - Record: `MediaRecorder` on the camera stream, mimeType feature-detected
    (`isTypeSupported('video/mp4')` first, webm fallback only for desktop dev), **single-Blob
    start()→stop()** (no timeslice chunks), **auto-stop at `max_clip_seconds`** with a visible
    countdown ("Ns left") + manual stop. Send disabled until a clip exists.
  - Send: `POST /api/turn` multipart (`video` Blob + `text`); output shows a spinner ("thinking…")
    until the first token; streamed via `fetch()` + `response.body.getReader()` + `TextDecoder`
    (manual `for(;;)` loop, **not** EventSource, **not** `for await...of`); spinner cleared on first
    token; controls reset on stream end. Context resets each turn (no history kept).
  - Robust feature-detection + graceful messages when camera/HTTPS unavailable (secure-context check,
    named getUserMedia error mapping); dark mobile-first UI with safe-area insets, 16px inputs
    (no iOS zoom), `100dvh`.
  - **Validated** (desktop logic, no real iOS here): wrote a tiny mock backend (Contract B:
    `/api/config`, `/api/transcribe`, token-streamed `/api/turn`) and ran it; curl confirmed
    incremental token arrival (~50ms apart). A Deno test harness ran the real client paths:
    streaming reader assembled the full answer over **37 incremental reads**; all four mimeType
    feature-detection branches correct (iOS→mp4, webm-only→webm, no-detect→preferred, none→`''`);
    auto-stop timer fires at max + countdown math decreases. `deno check app.js` clean; CSS braces
    balanced; served HTML carries all iOS-critical attrs (playsinline/muted/autoplay, viewport-fit).
  - **Not verifiable here (WS6 on real iPhone over WS5's HTTPS tunnel):** actual MP4/H.264 output &
    clip sizes, the iOS single-`dataavailable` behavior, mic/camera permission prompts, autoplay,
    and real model token stream from WS2.
- 2026-06-30 — **V0.1 frontend extensions** (6 changes; preserved all V0 capture/stream behavior).
  Consumes the **UPDATED Contract B**: `/api/config` now also returns `model_id`,
  `video_longest_side`, `target_fps`, `max_new_tokens`; `/api/transcribe` returns `{text, asr_ms}`;
  `/api/turn` streams `<answer markdown>` then a `` (RECORD SEPARATOR, U+001E) then a metrics JSON
  frame, then EOF.
  - **(1) Text-only send.** Send is now enabled when there is **EITHER a recorded clip OR non-empty
    text** (`refreshSendEnabled()` checks `state.clipBlob || hasText()`). `sendTurn()` only appends
    the `video` field when a clip exists; `text` is always sent. Clip-info helper text now reads
    "No clip — sending text only" when text is present without a clip.
  - **(5a) Markdown rendering.** Vendored `marked` + `DOMPurify` into `frontend/vendor/` (no CDN at
    runtime), loaded via `<script src>` in the head. Answer is rendered with
    `DOMPurify.sanitize(marked.parse(answerText))`, **throttled via `requestAnimationFrame`** so we
    don't parse on every token, with a final flush render at stream end. If either lib is missing or
    parsing throws, falls back to plain-text (`textContent`) so the app never breaks (`MD_OK` guard +
    try/catch in `renderAnswer`).
  - **(5b) Seamless flowing output.** Replaced the bordered, fixed-height, inner-scrolling output box
    with a **borderless flowing answer region** (`.output`: no background/border, no inner scrollbar,
    `max-width: 70ch`, comfortable line-height + full markdown element typography). `#app` is now
    `min-height:100dvh` so the **page** scrolls; sticky top bar keeps the title/gear reachable.
    Auto-scroll **sticks to the page bottom while near bottom** and **does not yank** if the user
    scrolled up (`stickToBottom` toggled by a passive `scroll` listener; `maybeAutoScroll()`). Spinner
    ("thinking…") shows until the first token, then a blinking caret while streaming. Greeting still
    renders here on load (plain-text mode).
  - **(6) Swapped buttons.** Order is now **Record (left)** then **Hold to ask (right)**.
  - **(7) Settings gear + modal.** Added a ⚙ icon button in the header. Opens a centered modal over a
    backdrop; **tap backdrop or the ✕ at the top-LEFT** dismisses (Esc too). Shows **Model** (`model_id`),
    **Video FPS** (`target_fps`), **Max video length** (`max_clip_seconds` s "@ {fps} fps"),
    **Resolution fed to model** (`video_longest_side` px, longest side).
  - **(8) Per-turn usage modal.** After a metric'd answer finishes, a small "ⓘ usage" chip mounts under
    the answer (hidden on `[error]` turns / when no metrics frame arrived). Tapping it opens the **same
    modal component** as #7: **Input tokens** (System/Video/Text + Total prompt) with the Whisper-ASR
    note that audio is not sent as tokens; **Output tokens**; **Timings** = ASR `asr_ms` (from this
    turn's transcribe; "—" if the user typed), **Send→first-token** (client-measured around the fetch),
    Model **TTFT**, **Inference**, **Normalize**. Client values (`asr_ms`, send→first-token) are tracked
    in `state`/locals and combined with the backend metrics frame.
  - ** split handling.** The getReader() loop accumulates and splits on `` mid-chunk: everything
    before is the answer (markdown), everything after is buffered as the metrics JSON. The RS is never
    appended to the answer; metrics never leak into the rendered output. Still **no EventSource**, **no
    `for await...of`** (iOS), single-Blob capture unchanged.
  - **VALIDATED (desktop logic; no real iOS):** wrote a stdlib **mock backend** in scratchpad
    (`mock_backend_v2.py`) implementing the updated contract (`/api/config` new fields,
    `/api/transcribe`→`{text,asr_ms}`, `/api/turn` streaming markdown then ``+metrics, plus an
    `/api/turn-error` no-metrics turn); curl confirmed the RS frame at the right byte offset and a
    clean metrics parse. Three Deno harnesses: **(a)** 20/20 assertions on the RS-split state machine
    across adversarial chunk boundaries (RS mid-chunk, RS at boundary, RS split across chunks, metrics
    split across chunks, error turn with no RS, empty-answer edge) — answer never contains RS/metrics;
    **(b)** 8/8 on real `marked` output (h1/bold/italic/strike/inline-code/list/code-fence/link) +
    DOMPurify loads; **(c)** 21/21 full **DOM integration** (linkedom) driving the actual `app.js`:
    greeting after config, settings modal shows all four config fields + backdrop dismiss, **text-only
    send enabled**, answer rendered as markdown (`<strong>/<em>/<code>/<li>`), **answer contains no RS
    and does not leak `prompt_total`/`inference_total`**, usage chip mounts, usage modal shows token
    counts + Whisper note + ttft/inference ms + ASR "—" for typed input; plus 4/4 on the error-turn
    (text shown, **no usage chip**) and transcribe `asr_ms` contract. `deno check app.js` clean; CSS
    braces balanced (99/99); served HTML keeps all iOS attrs + references the two vendored libs.
    **Note:** DOMPurify's *script-stripping* could not be asserted under linkedom (`isSupported` is
    `undefined` there → it passes input through; it sanitizes correctly only against a real browser
    DOM) — a harness limitation, not a code issue. Files added: `frontend/vendor/marked.min.js`
    (v15.0.12), `frontend/vendor/purify.min.js` (v3.4.11).
  - **On-device-only (final phone test):** real iOS markdown rendering + DOMPurify sanitization in
    Safari, page-scroll auto-follow feel with real streaming cadence, the gear/usage modal tap targets
    + backdrop dismiss on touch, and the real backend's `` frame + metrics shape from WS2.
