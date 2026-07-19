# WS-E — Browser extension (`clients/extension/`, Chrome MV3)

> Computer capture surfaces slice, priority 1 (founders 2026-07-18 follow-up). A **passive
> capture surface** — a stripped-down Loom, NOT an agent extension: it never reads page
> content, never injects content scripts, never touches the DOM of any page. It only
> captures media streams (screen-share video, tab audio) and speaks the exact
> `POST /capture/segments` wire pinned in [ws-b](ws-b-phone-web-client.md) §Wire /
> [ws-c](ws-c-ingest-demux-ledger.md). **Zero server changes** — the wire is client-agnostic.

**Status:** built + unit/conformance-tested; **needs a human Chrome leg** (this box is
headless Linux — see §Human test steps) · **Owner session:** recording computer-capture lead

---

## Decisions

- **D-E1 — passive-permission posture.** Manifest permissions: `tabCapture`,
  `desktopCapture`, `offscreen`, `storage` — nothing else. **No content scripts, no static
  `host_permissions`, no `activeTab`, no `tabs`.** The server origin is user-configured, so
  cross-origin `fetch` uses `optional_host_permissions: ["http://*/*", "https://*/*"]` with a
  **runtime grant for exactly the configured origin** (`chrome.permissions.request` from the
  popup — a user gesture — when the server URL is saved). Extension contexts with a granted
  host permission bypass CORS, so the recording server needs **no CORS middleware** — the
  no-server-changes constraint holds.
- **D-E2 — stream acquisition = stream-ID handoff, not getDisplayMedia.** MV3 reality: the
  popup dies on focus loss, the service worker has no DOM, and `getDisplayMedia` inside an
  offscreen document has transient-activation problems. The documented-reliable pattern:
  - **Screen video:** service worker calls `chrome.desktopCapture.chooseDesktopMedia(
    ["screen", "window", "tab"], cb)` (no `targetTab` ⇒ the stream is consumable by
    extension-origin contexts, i.e. our offscreen document) → Chrome's native picker → the
    offscreen document opens it with `getUserMedia({video: {mandatory:
    {chromeMediaSource: "desktop", chromeMediaSourceId}}, audio: false})`. Video only —
    **no system audio in this slice** (recorded; a later surface).
  - **Tab audio:** service worker calls `chrome.tabCapture.getMediaStreamId({targetTabId})`
    for the tab the popup was opened on (opening the popup = the required extension
    invocation on that tab) → offscreen document `getUserMedia({audio: {mandatory:
    {chromeMediaSource: "tab", chromeMediaSourceId}}, video: false})`. tabCapture silences
    the tab for the user, so the offscreen doc routes the stream through an
    `AudioContext` → `destination` passthrough to keep it audible.
  - Fallback noted (untested-here): if `chooseDesktopMedia` misbehaves from a MV3 worker on
    some Chrome build, the offscreen document can try `getDisplayMedia` directly (reason
    `DISPLAY_MEDIA`); not wired in v0 — one path, honestly tested by the human leg.
- **D-E3 — one recording = up to TWO ingest sessions, one per source.** The wire's `seq`
  is dense per session; two independent segment loops can't share one counter. So screen
  video and tab audio each mint their **own `session_id`** (own dense `seq`, own end marker,
  own gap report), both carrying the **same `device_id`** (`ext-chrome-<suffix>`, suffix
  persisted in `chrome.storage.local`). Server demux then yields **separate C1 streams (own
  `stream_id` each, same `device_id`)** — exactly the slice requirement, with zero server
  changes (single-modality segments are already handled: the ffprobe track probe decides).
- **D-E4 — segmented recorder, D-M1-1 reused verbatim.** One `MediaRecorder` per source,
  restarted every ~10 s (no timeslice — fragments aren't self-contained); each stop yields a
  standalone blob = one upload unit. Mime preference (Chrome): screen
  `video/webm;codecs=vp9` → `vp8` → `video/webm`; tab audio `audio/webm;codecs=opus` →
  `audio/webm`. Screen capped at 1920×1080 @ 15 fps, ~2.5 Mbps (screen content, not motion
  video). Each source's uploader is the phone client's serialized queue: send `seq` only
  after `seq-1` acked; retry forever on network/5xx (backoff 1 s·2ⁿ cap 30 s); 4xx surfaced
  + dropped (never retried); sha256 via `crypto.subtle`.
- **D-E5 — context topology.** `popup` = UI only (record/stop, counters, verdicts,
  settings). `background.js` (service worker) = orchestration only (stream-ID acquisition,
  offscreen lifecycle). `offscreen.html/js` = the capture engine (streams, recorders,
  queues, report polls) — it must outlive the popup, and it does: an offscreen document
  with active capture stays alive. All `chrome.runtime` messages carry a `target` field
  (`"offscreen"` | "background"`) — every listener ignores non-matching messages (multiple
  contexts share the message bus). Popup pulls status (~1 s) straight from the offscreen doc.
- **Source-ended semantics:** Chrome's native "Stop sharing" bar (or closing the captured
  tab) fires `track.onended` → that **source's session stops cleanly** (drain + end marker);
  the other source keeps recording until the user stops it. Honest partial capture, honest
  ledger.
- **Shutdown reality (stated, not hidden):** if Chrome itself quits mid-recording, there is
  no reliable last-gasp hook in MV3; the offscreen doc registers `pagehide` →
  `fetch(…, {keepalive: true})` end markers as best-effort. A hard kill leaves the sessions
  unterminated — which is exactly what the ledger's `unterminated` flag is for.

## Files

```
clients/extension/
  manifest.json      MV3; minimum_chrome_version 116 (offscreen + getMediaStreamId consumption)
  background.js      service worker: start/stop orchestration, stream-ID acquisition,
                     offscreen document lifecycle (guarded createDocument)
  offscreen.html/.js capture engine: getUserMedia from stream IDs, AudioContext passthrough,
                     per-source segment loop + uploader + report poll, status snapshots
  uploader.js        shared ES module: createUploader(...) — serialized queue, backoff,
                     4xx drop, sha256, end marker; DI'd fetch/sleep/now (deno-testable)
  segmenter.js       shared ES module: createSegmenter(...) — the D-M1-1 restart loop as a
                     pure state machine over an injected recorder factory (deno-testable)
  popup.html/.js/.css status panel mirroring the phone client: record/stop, per-source
                     counters (captured/uploaded/queued), last error, verdict badge per
                     session, settings (server URL, user id, source toggles)
  tests/uploader_test.js, tests/segmenter_test.js   deno test, mocked fetch/clock
```

Unit tests run via `tests/test_extension_assets.py` (pytest): manifest invariants (the
passive posture: no content_scripts/host_permissions), `deno check` on every JS file, and
`deno test` on the two module test files — all skip cleanly if deno is absent.

## Wire mapping (per source session)

Identical to ws-b §Wire; only the values differ: `session_id` minted per source per
record-press; `user_id` from settings (default `beta-user`); `device_id` =
`ext-chrome-<suffix>`; `mime` = the recorder's actual mimeType; `t_start`/`t_end` wall-clock
ms stamped at recorder start/stop; end marker `{last_seq}` after drain. Server URL prefix is
the configured setting (tunnel URL or `http://localhost:8084`).

## Human test steps (Chrome leg — this box cannot run a browser)

1. `chrome://extensions` → enable **Developer mode** → **Load unpacked** →
   `product/services/recording/clients/extension/`.
2. Click the Nucleus Capture action icon. In **Settings**: server URL = the tunnel URL from
   `var/tunnel_url.txt` (or `http://localhost:8084` against a local `run_learn.sh` fleet),
   user id, then **Save** → accept the host-permission prompt for that origin.
3. Pick sources (screen ✓ / tab audio ✓), **Record** → Chrome's screen picker appears →
   choose a screen/window. Tab audio captures the tab the popup was opened on — open the
   popup on a tab that is playing audio (the tab stays audible while captured).
4. Watch the popup counters climb (~1 segment per 10 s per source) and the per-source
   verdict badges; **Stop** → queues drain. You may glimpse the first source's `clean`
   badge, then the popup resets to idle within seconds (the capture document auto-closes
   after full drain — the popup pulls live state and has nowhere to pull from). NOT a
   failure; the verdicts are confirmed in step 5. *Follow-up noted: persist final
   per-source verdicts (chrome.storage) so the popup can show them after close.*
5. Cross-check server-side: `GET /capture/sessions` shows both sessions (same `device_id`);
   each report shows its C1 stream (screen → `video`, tab → `audio`) with dense sequences;
   audio transcripts land in `/context` via DP.
6. Drills worth one minute: use Chrome's "Stop sharing" bar (screen session ends clean,
   audio keeps going); pull the network mid-recording (counters stall, retries resume).

## Worklog

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
- 2026-07-19 — fresh-eyes verification round (recording M1 lead, during the CTO's alpha
  pass): 2 confirmed tester-facing gaps, both documentation-level. (1) "both badges land
  clean" is unobservable — the offscreen capture document auto-closes on full drain and
  the popup resets to idle before the second badge renders; runbook + §Human-test-steps
  rewired to the server-side verdict check, and persisting final per-source verdicts via
  chrome.storage recorded as a follow-up. (2) cancel-picker's `screen: cancelled` row
  dies with the picker-closed popup — documented as cosmetic (lone tab-audio block is
  the real signal). No extension code changed mid-alpha (the CTO has it loaded unpacked).
