# WS-E — Browser extension (`clients/extension/`, Chrome MV3)

> Computer capture surfaces slice, priority 1 (founders 2026-07-18 follow-up). A **passive
> capture surface** — a stripped-down Loom, NOT an agent extension: it never reads page
> content, never injects content scripts, never touches the DOM of any page. It captures the
> **active browser tab** (video + audio) as media streams and speaks the exact
> `POST /capture/segments` wire pinned in [ws-b](ws-b-phone-web-client.md) §Wire /
> [ws-c](ws-c-ingest-demux-ledger.md). **Zero server changes** — the wire is client-agnostic.

**CAPTURE MODEL PIVOTED 2026-07-19 (D-E7):** the original screen-share design (desktopCapture
picker → offscreen handoff) proved fragile on real browsers during alpha (see worklog) and was
replaced by **direct tab capture** — see D-E7. Decisions D-E2/D-E3/D-E6 below describe the
retired picker path and are kept for history, marked SUPERSEDED.

**Status:** built + unit/conformance-tested + adversarially reviewed; **needs a human Chrome
leg** (this box is headless Linux — see §Human test steps) · **Owner session:** recording
computer-capture lead

---

## Decisions

- **D-E7 — DIRECT TAB CAPTURE (the current model, 2026-07-19).** The extension records the
  **active tab**: video + audio in ONE muxed stream via
  `chrome.tabCapture.getMediaStreamId({targetTabId})` (targetTabId pinned by the popup at
  Record) → offscreen `getUserMedia({audio:{mandatory:{chromeMediaSource:"tab",
  chromeMediaSourceId}}, video:{…same id…}})` (video toggle off ⇒ `video:false`, audio-only).
  ONE tab = ONE capture = ONE ingest session; a single `MediaRecorder` writes ~10 s
  self-contained **muxed webm** (vp8+opus) segments, and the **server demuxes each into audio +
  video C1 streams** — the exact muxed-A/V path the phone (mp4) and mac (mp4) clients already
  use, zero server changes. **Why this replaced the picker path:** the desktopCapture picker
  (D-E2) failed on the tester's real browser (Comet) — "Entire Screen" errored with "Error
  starting tab capture", the picker's Window list enumerated only the extension's own picker
  window, and the cross-context stream-id handoff is inherently fragile; it can't be verified
  here (headless Linux, and Comet ≠ vanilla Chromium). tabCapture is the most stable Chromium
  capture primitive, needs no picker/handoff, and can't hit the same-tab collision (it IS one
  capture of the tab). **Trade-off, accepted:** the extension captures a browser *tab*, not the
  whole desktop or other apps — full-screen capture is the **mac CLI's** job (ws-f, verified).
  This collapses the client to: mint id → one getUserMedia → one segmenter → one uploader → one
  report. It removed picker.html/picker.js, `chooseDesktopMedia`, the `desktopCapture`
  permission, the persisted picker continuation, D-E6's abort logic, and the two-session
  bookkeeping. Passthrough (D-E4) still applies: tabCapture silences the tab, so audio routes
  through an `AudioContext` to stay audible.
- **D-E1 — passive-permission posture** (amended by D-E7). Manifest permissions are now
  exactly `tabCapture`, `offscreen`, `storage` — `desktopCapture` removed with the picker
  path. **No content scripts, no static `host_permissions`, no `activeTab`, no `tabs`** (the
  popup reads the active tab's id, which is not gated by `tabs`). The server origin is
  user-configured, so cross-origin `fetch` uses `optional_host_permissions: ["http://*/*",
  "https://*/*"]` with a **runtime grant for exactly the configured origin**
  (`chrome.permissions.request` from the popup — a user gesture — when the server URL is
  saved; **persisted BEFORE the prompt**, alpha fix). Extension contexts with a granted host
  permission bypass CORS, so the recording server needs **no CORS middleware**.
- **D-E2 — [SUPERSEDED by D-E7] stream acquisition = stream-ID handoff, not getDisplayMedia.** MV3 reality: the
  popup dies on focus loss, the service worker has no DOM, and `getDisplayMedia` inside an
  offscreen document has transient-activation problems. The pattern:
  - **Screen video (amended 2026-07-19, alpha):** real Chrome refuses
    `chooseDesktopMedia` from a service worker with no `targetTab` ("A target tab is
    required when called from a service worker context"), and passing a `targetTab` binds
    the stream to that tab's origin — unusable by our offscreen document. So the worker
    opens **`picker.html` in a tiny popup window**; that extension PAGE runs
    `chrome.desktopCapture.chooseDesktopMedia(["screen","window","tab"], cb)` (no
    `targetTab` ⇒ extension-consumable id), posts the result back, and closes itself.
    Closing the picker window without choosing = cancelled. The offscreen document then
    opens the id with `getUserMedia({video: {mandatory: {chromeMediaSource: "desktop",
    chromeMediaSourceId}}, audio: false})`. Video only — **no system audio in this
    slice** (recorded; a later surface).
  - **Tab audio:** service worker calls `chrome.tabCapture.getMediaStreamId({targetTabId})`
    for the tab the popup was opened on (opening the popup = the required extension
    invocation on that tab) → offscreen document `getUserMedia({audio: {mandatory:
    {chromeMediaSource: "tab", chromeMediaSourceId}}, video: false})`. tabCapture silences
    the tab for the user, so the offscreen doc routes the stream through an
    `AudioContext` → `destination` passthrough to keep it audible.
  - Fallback noted (untested-here): if `chooseDesktopMedia` misbehaves from a MV3 worker on
    some Chrome build, the offscreen document can try `getDisplayMedia` directly (reason
    `DISPLAY_MEDIA`); not wired in v0 — one path, honestly tested by the human leg.
- **D-E3 — [SUPERSEDED by D-E7: the extension is now ONE muxed session, not two] one
  recording = up to TWO ingest sessions, one per source.** The wire's `seq`
  is dense per session; two independent segment loops can't share one counter. So screen
  video and tab audio each mint their **own `session_id`** (own dense `seq`, own end marker,
  own gap report), both carrying the **same `device_id`** (`ext-chrome-<suffix>`, suffix
  persisted in `chrome.storage.local`). Server demux then yields **separate C1 streams (own
  `stream_id` each, same `device_id`)** — exactly the slice requirement, with zero server
  changes (single-modality segments are already handled: the ffprobe track probe decides).
- **D-E4 — segmented recorder, D-M1-1 reused verbatim** (D-E7 update: ONE recorder for the
  muxed tab stream, not one-per-source). The `MediaRecorder` restarts every ~10 s (no
  timeslice — fragments aren't self-contained); each stop yields a standalone blob = one
  upload unit. Mime preference (Chrome): muxed `video/webm;codecs=vp9,opus` → `vp8,opus` →
  `video/webm`; audio-only (video toggle off) `audio/webm;codecs=opus` → `audio/webm`. Tab
  video ~2.5 Mbps. The uploader is the phone client's serialized queue: send `seq` only after
  `seq-1` acked; retry forever on network/5xx (backoff 1 s·2ⁿ cap 30 s); 4xx surfaced +
  dropped (never retried); sha256 via `crypto.subtle`.
- **D-E5 — context topology.** `popup` = UI only (record/stop, counters, verdicts,
  settings). `background.js` (service worker) = orchestration only (stream-ID acquisition,
  offscreen lifecycle). `offscreen.html/js` = the capture engine (streams, recorders,
  queues, report polls) — it must outlive the popup, and it does: an offscreen document
  with active capture stays alive. All `chrome.runtime` messages carry a `target` field
  (`"offscreen"` | "background"`) — every listener ignores non-matching messages (multiple
  contexts share the message bus). Popup pulls status (~1 s) straight from the offscreen doc.
- **D-E6 — [SUPERSEDED by D-E7: no picker, no two sources → nothing to abort at start] cancel/
  failure at START aborts the whole recording.** (History: with the desktop picker, a cancelled/
  failed screen aborted the whole start rather than silently recording audio-only. D-E7's
  single tab stream either starts or surfaces an honest `startError` — there is no partial-set
  to abort.) The former "one capture per tab" collision is also gone: tabCapture is one capture
  of the tab, so it cannot collide with itself.
- **Source-ended semantics:** closing/navigating the captured tab (or a stop-sharing
  affordance) fires `track.onended` → the session **stops cleanly** (final segment, drain, end
  marker). One session now, so this ends the recording.
- **Shutdown reality (stated, not hidden):** if Chrome itself quits mid-recording, there is
  no reliable last-gasp hook in MV3; the offscreen doc registers `pagehide` →
  `fetch(…, {keepalive: true})` end markers as best-effort. A hard kill leaves the sessions
  unterminated — which is exactly what the ledger's `unterminated` flag is for.

## Files

```
clients/extension/
  manifest.json      MV3; minimum_chrome_version 116; permissions tabCapture/offscreen/storage
  background.js      service worker: start/stop orchestration, tabCapture stream-id mint for the
                     popup-pinned tab, offscreen document lifecycle (guarded createDocument)
  offscreen.html/.js capture engine: ONE muxed getUserMedia (tab video+audio), AudioContext
                     passthrough, segment loop + uploader + report poll, status snapshot
  uploader.js        shared ES module: createUploader(...) — serialized queue, backoff,
                     4xx drop, sha256, end marker; DI'd fetch/sleep/now (deno-testable)
  segmenter.js       shared ES module: createSegmenter(...) — the D-M1-1 restart loop as a
                     pure state machine over an injected recorder factory (deno-testable)
  popup.html/.js/.css status panel mirroring the phone client: record/stop, one-session
                     counters (captured/uploaded/queued), last error, verdict badge, Discard
                     escape hatch, settings (server URL, user id, video toggle)
  tests/uploader_test.js, tests/segmenter_test.js   deno test, mocked fetch/clock
```
(picker.html/picker.js removed with D-E7.)

Unit tests run via `tests/test_extension_assets.py` (pytest): manifest invariants (the
passive posture: correct permission set, no content_scripts/host_permissions, no
desktop-picker machinery), `deno check` on every JS file, and `deno test` on the two module
test files — all skip cleanly if deno is absent. `tests/test_wire_conformance.py` proves the
muxed-webm shape (vp8+opus) demuxes to audio+video C1 streams.

## Wire mapping (one session per recording)

Identical to ws-b §Wire; only the values differ: `session_id` minted per record-press;
`user_id` from settings (default `beta-user`); `device_id` = `ext-chrome-<suffix>`; `mime` =
the recorder's actual muxed webm mimeType; `t_start`/`t_end` wall-clock ms stamped at recorder
start/stop; end marker `{last_seq}` after drain. Server URL prefix is the configured setting
(tunnel URL or `http://localhost:8084`). The server demuxes each muxed segment into audio +
video C1 streams.

## Human test steps (Chrome leg — this box cannot run a browser)

1. `chrome://extensions` → enable **Developer mode** → **Load unpacked** →
   `product/services/recording/clients/extension/` (after a code change, hit the extension's
   **reload** icon).
2. Click the Nucleus Capture action icon. In **Settings**: server URL = the tunnel URL from
   `var/tunnel_url.txt` (or `http://localhost:8084` against a local `run_learn.sh` fleet),
   user id, then **Save** → accept the host-permission prompt. **Reopen the popup and confirm
   the fields kept your values** (Save-persistence regression check).
3. Open the popup **on an ordinary web-page tab** (a chrome:// or extension page is not
   capturable — you'll get an honest error). Leave "video" ✓ (or uncheck it for audio-only),
   **Record** → **no picker** — capture starts immediately on that tab (it stays audible via
   the passthrough).
4. Watch the popup counters climb (~1 segment / 10 s); **Stop** → queue drains. The popup
   resets to idle within seconds of the drain (the capture document auto-closes) — NOT a
   failure; the verdict is confirmed server-side in step 5.
5. Cross-check: `GET /capture/sessions` shows the session (`ext-chrome-*` device); its report
   shows an `audio` + `video` stream (or just `audio` if the video toggle was off) with dense
   sequences and verdict `clean`; audio transcripts land in `/context` via DP.
6. Drills: close/navigate the captured tab mid-record (session ends `clean` on its own); set a
   bogus server URL, record, Stop → stuck `draining` → **Discard unsent** unlocks; pull the
   network mid-record (counters stall, retries resume).

## Worklog

- 2026-07-19 — **CAPTURE MODEL PIVOTED to direct tab capture (D-E7).** Second/third real-Chrome
  alpha runs (CTO, on Comet — a Chromium fork) showed the desktop-picker path failing beyond the
  same-tab case: "Entire Screen" errored with "Error starting tab capture", and the picker's
  Window list enumerated ONLY the extension's own picker window — the separate-picker-window +
  cross-context stream-id handoff is fundamentally fragile, and I can't reproduce/fix Comet here
  (headless Linux; Comet ≠ vanilla Chromium). Decision: stop fighting the desktop picker and use
  the robust primitive that fits the extension's lane — `tabCapture` of the active tab (video +
  audio, ONE muxed stream, ONE session, server-demux). This deleted picker.html/picker.js,
  `chooseDesktopMedia`, the `desktopCapture` permission, the persisted picker continuation, and
  the D-E6 abort logic; it collapsed offscreen to a single session. Trade-off (extension = tab,
  not full screen) accepted — the mac CLI does full-screen reliably. Verified: 110 recording
  tests (incl. a NEW muxed-webm vp8+opus conformance shape → audio+video C1), 17 deno tests,
  deno check, asset tests (permission set + no-picker-machinery), 3-skeptic adversarial pass.
  Skeptic 1 confirmed the tabCapture flow correct (SW `getMediaStreamId`, both tracks from one
  id, permission set, min-version); the round's fixes: a `starting` latch spanning the
  getUserMedia await + Record disabled before its prompt (no concurrent-start orphan),
  track.onended identity-guarded, the tab stream id minted BEFORE the offscreen document (no
  leak on a chrome:// tab), the drained-close re-checks `startsInFlight` after its status probe,
  the host-permission match pattern drops the port (Chrome patterns ignore ports —
  `localhost:8084/*` was invalid), and the AudioContext is resumed so the passthrough is
  audible. **The Comet/Chrome retest is the CTO's step.** Everything below this line predates
  the pivot.
- 2026-07-18 — spec written (decisions D-E1…D-E5); handed to the build fan-out.
- 2026-07-18 — built as specced (11 files): manifest (passive posture pinned by
  `tests/test_extension_assets.py`), background SW (guarded offscreen lifecycle, stream-ID
  acquisition, device-id single home), offscreen engine (per-source segmenter + uploader +
  report poll, AudioContext passthrough, track-ended → per-source clean stop), popup
  (settings + runtime origin grant + per-source status/verdict panel), `uploader.js` /
  `segmenter.js` as DI'd ES modules with 17 deno tests. Verified here: `deno check` (syntax/
  module-graph strength — deno does NOT type-check plain .js; stated honestly in the test
  module), `deno test`, pytest asset invariants. **No browser run on this box — the Chrome
  leg is §Human test steps.**
- 2026-07-18 — **adversarial review round** (5-lens find → 2-skeptic verify, 19 findings →
  10 confirmed after dedup) fixed here: (1) *stream-id expiry* (HIGH): tab-capture id was
  minted BEFORE the human-paced screen picker and expired (~10 s unused TTL) while the user
  chose — acquisition reordered (picker first, tab id after; offscreen consumes the younger
  tab id first), and a failed source now surfaces as `startErrors` in the status snapshot +
  an error row in the popup (the start reply is lost when the picker kills the popup);
  (2) *drained-close race*: a Record pressed during the previous session's drain could have
  the offscreen document closed mid-start — `startsInFlight` guard on the drained handler +
  re-`ensureOffscreen` after acquisition; (3) *orphaned polls / 404-forever leak*: restart
  now stops old sources' poll timers, and a post-end report that keeps failing stops after
  6 polls (~30 s) instead of keeping the document open forever; (4) test honesty: the
  segmenter factory-failure test now ASSERTS `onError` fired (the hole was
  mutation-verified), plus a recorder-onerror-surfacing test; the no-op `@ts-nocheck`
  pragma test was removed.
- 2026-07-18 — **wire rename adopted** (founders): all client URLs moved to `/capture/*`;
  ws-b §Wire carries the rename note. No extension behaviour change.
- 2026-07-19 — **first real-Chrome run (CTO alpha) found 3 defects, all fixed same hour:**
  (1) *Save lost the settings* — `chrome.permissions.request` can close the popup, killing
  everything after its await; the `chrome.storage.local.set` ran AFTER it, so the grant
  went through while the URL/user reverted to defaults. Persist-before-prompt now.
  (2) *No screen picker* — the D-E2 amendment above: real Chrome refuses worker-context
  `chooseDesktopMedia`; screen acquisition moved to `picker.html`/`picker.js` (the popup's
  error row surfaced the exact failure string — the review-round surfacing fix earning
  its keep). (3) *Draining soft-deadlock* — with an unreachable server URL the uploader
  retries forever (by design), the drain never finishes, and settings are LOCKED while
  draining: no way out. New **Discard unsent** button (draining state only) → background
  closes the capture document; unsent segments drop (stated on the button), the ledger
  keeps what arrived, settings unlock. Verified here: deno check + 17 deno tests +
  asset tests + 3-skeptic adversarial pass over the patch; the Chrome retry is the
  CTO's step.
- 2026-07-19 — **two skeptic rounds over the picker fix reshaped it into a persisted
  continuation** (all found-before-the-tester): round 1 — (a) the tab-audio id, minted
  right after the picker resolved, targeted the PICKER window as "active tab"; (b) MV3
  kills an idle worker in ~30 s, so worker-memory pending state silently dropped any
  pick the user deliberated over; (c) acquisition-stage errors died with the popup.
  Rework: the popup pins the target `tabId` at Record (tab ids need no permission);
  the pending start persists in `chrome.storage.session`; `picker-result` — which
  WAKES a fresh worker — resumes it (`finishStart`), minting the tab id with explicit
  `targetTabId` and carrying `acquireErrors` into the offscreen status surface (even
  for a zero-source start). Round 2 over the rework — (d) the supersede path removed
  the stale picker window BEFORE clearing pending, letting its onRemoved take run a
  phantom cancel-continuation under stale config; (e) the drained-close guard didn't
  cover the continuation; (f) a superseded picker's late result could consume the new
  pending; (g) a zero-source start leaked the document forever. Fixes: every
  pending-state transition serialized through one promise-chain lock; supersede clears
  pending before removing the window; drained-close is now AUTHORITATIVE (skips if a
  pending exists, then asks the offscreen doc's status and only closes when no source
  is active or draining); picker results carry their window id and mismatches are
  dropped; zero-source starts linger 60 s (generation-guarded) then self-request
  close; the dead round-1 `acquireTabStreamId` duplicate deleted. **Stated residual:**
  a worker death in the sub-second continuation window itself (post-take, pre-handoff)
  still loses that start silently — rare, recoverable by pressing Record again;
  accepted for v0.
- 2026-07-19 — **second real-Chrome run (CTO): Save now sticks ✓, picker window opens ✓,
  tab audio records ✓.** Two findings: (1) picking the SAME tab for screen-video that we
  capture audio from → video fails "Error starting tab capture" (Chromium one-capture-per-tab
  — see Known limitation). (2) the user (rightly) found audio-recording-anyway-when-screen-
  cancelled counterintuitive. Fix = **D-E6, enforced at BOTH layers** — a bounded skeptic
  pass caught the first draft placing the abort only at acquisition (`finishStart`), but the
  same-tab failure surfaces LATER, at the offscreen `getUserMedia` stage (the picker returns
  a stream id fine; the desktop-tab video only fails when opened). So the abort lives in both
  `finishStart` (picker cancel/error) AND offscreen `handleStart` (getUserMedia refusal →
  skip tab audio, seed the same-tab hint). This is what actually closes the user's bug (pick
  the audio tab for video → previously a silent audio-only recording; now aborts with "…
  Chrome can't capture one tab twice. Choose Entire Screen or a Window"). Runbook drills
  updated. The picker appears in its own small window (D-E2 workaround) with the browser's
  native share dialog over it — inherent to the MV3-worker constraint; noted in the runbook
  so it doesn't read as broken. deno check + 17 deno tests + asset tests + a bounded
  adversarial pass (which found exactly the layering miss above). Re-test is the CTO's step.
- 2026-07-19 — fresh-eyes verification round (recording M1 lead, during the CTO's alpha
  pass): 2 confirmed tester-facing gaps, both documentation-level. (1) "both badges land
  clean" is unobservable — the offscreen capture document auto-closes on full drain and
  the popup resets to idle before the second badge renders; runbook + §Human-test-steps
  rewired to the server-side verdict check, and persisting final per-source verdicts via
  chrome.storage recorded as a follow-up. (2) cancel-picker's `screen: cancelled` row
  dies with the picker-closed popup — documented as cosmetic (lone tab-audio block is
  the real signal). No extension code changed mid-alpha (the CTO has it loaded unpacked).
