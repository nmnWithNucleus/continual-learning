// @ts-nocheck
/* global chrome */
/*
 * WS-E — offscreen capture engine (D-E5): streams, recorders, queues, polls.
 *
 * One recording = up to TWO ingest sessions, one per source (D-E3): the wire's
 * `seq` is dense per session, so screen video and tab audio each mint their own
 * session_id (own dense seq, own end marker, own gap report), both carrying the
 * SAME device_id. Server demux then yields separate C1 streams — zero server
 * changes.
 *
 * Per source: getUserMedia from the stream id handed over by background
 * (D-E2), a segmenter (D-M1-1 restart loop, ~10s self-contained blobs), a
 * serialized uploader, and a 5s report poll whose verdict is the tester's
 * "it landed" signal (mirrors clients/web/app.js pollReport, including the
 * poll-stop condition: ended AND terminal verdict AND server drained).
 *
 * Tab audio is routed through an AudioContext passthrough because tabCapture
 * silences the captured tab for the user; the passthrough keeps it audible.
 *
 * Source-ended semantics: Chrome's "Stop sharing" bar (or closing the captured
 * tab) fires track.onended -> THAT source stops cleanly (final segment, drain,
 * end marker); the other keeps recording. Honest partial capture.
 *
 * Shutdown reality: pagehide -> keepalive end markers, best-effort only. A hard
 * Chrome kill leaves sessions unterminated — exactly what the ledger's
 * `unterminated` flag is for.
 */

import { createUploader } from "./uploader.js";
import { createSegmenter } from "./segmenter.js";

const SEGMENT_MS = 10000;
const REPORT_POLL_MS = 5000;
const SCREEN_BPS = 2_500_000; // screen content, not motion video (D-E4)

// Mime preference (Chrome), probed via MediaRecorder.isTypeSupported (D-E4).
const SCREEN_MIMES = ["video/webm;codecs=vp9", "video/webm;codecs=vp8", "video/webm"];
const TAB_AUDIO_MIMES = ["audio/webm;codecs=opus", "audio/webm"];

const errText = (err) => String((err && err.message) || err);

// ULID-ish session id (same Crockford idiom as clients/web/app.js): 48-bit ms
// timestamp + 80-bit randomness. Ordering is carried by seq, never by the id.
const CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
function encodeTime(ms, length) {
  let out = "";
  for (let i = 0; i < length; i++) {
    out = CROCKFORD[ms % 32] + out;
    ms = Math.floor(ms / 32);
  }
  return out;
}
function randomChars(length) {
  const bytes = new Uint8Array(length);
  crypto.getRandomValues(bytes);
  let out = "";
  for (let i = 0; i < length; i++) out += CROCKFORD[bytes[i] & 31];
  return out;
}
const newSessionId = () => encodeTime(Date.now(), 10) + randomChars(16);

function pickMime(candidates) {
  if (globalThis.MediaRecorder && MediaRecorder.isTypeSupported) {
    for (const m of candidates) if (MediaRecorder.isTypeSupported(m)) return m;
  }
  return ""; // let the browser choose; the real type is read off the recorder
}

// ------------------------------------------------------------------- state
let config = null; // {baseUrl, userId, deviceId} — set by the start message
const sources = { screen: null, tabAudio: null };
// A source that FAILED to start (expired stream id, getUserMedia refusal)
// must stay visible: the popup may have died with the picker, so the start
// reply is lost — the status snapshot is the only surface left (review round).
const startErrors = { screen: null, tabAudio: null };
let drainedSent = false;

// After the end marker, a report that keeps answering non-OK will never turn
// terminal (e.g. every segment was 4xx-dropped: the session does not exist
// server-side). Give it this many post-end polls, then stop honestly.
const DEAD_POLL_LIMIT = 6; // x 5s interval = ~30s of grace

// A start where NO source began (all acquisition failed/cancelled) keeps this
// document alive only long enough to explain itself, then asks to be closed.
const ZERO_SOURCE_LINGER_MS = 60_000;
let startGeneration = 0; // bumps per start; stale linger timers self-disarm

const liveSources = () => [sources.screen, sources.tabAudio].filter(Boolean);
const isActive = () => liveSources().some((s) => s.active);

// -------------------------------------------------------------- report poll

function reportLines(report) {
  const lines = [];
  if (typeof report.received_segments === "number") {
    let t = "received " + report.received_segments;
    if (report.expected_segments != null) t += " / " + report.expected_segments;
    lines.push(t + " segments");
  }
  const st = report.segment_states || {};
  if (st.received) lines.push("server processing " + st.received + " segment(s)…");
  if (st.failed) lines.push(st.failed + " segment(s) FAILED server-side");
  const cl = report.client_leg || {};
  if (Array.isArray(cl.missing_seqs) && cl.missing_seqs.length) {
    lines.push("client leg missing seqs: " + cl.missing_seqs.join(", "));
  }
  for (const leg of report.emit_leg || []) {
    let t = (leg.modality || "?") + ": " + leg.chunks_emitted + " chunks emitted";
    if (leg.pending) t += ", " + leg.pending + " pending";
    if (leg.failed) t += ", " + leg.failed + " failed";
    const dp = leg.dp || {};
    if (dp.checked === false) t += " (DP unchecked)";
    else {
      // missing_unacked (ack-reconciled) is authoritative; entries may be
      // [lo,hi] runs or flat seqs — count CHUNKS, not runs.
      const miss = Array.isArray(dp.missing_unacked) ? dp.missing_unacked : (dp.missing || []);
      const n = miss.reduce((acc, m) => acc + (Array.isArray(m) ? m[1] - m[0] + 1 : 1), 0);
      if (n) t += ", DP missing " + n;
    }
    lines.push(t);
  }
  return lines;
}

function stopPolling(src) {
  if (src.pollTimer) clearInterval(src.pollTimer);
  src.pollTimer = null;
}

function startPolling(src) {
  stopPolling(src);
  src.pollTimer = setInterval(() => pollReport(src), REPORT_POLL_MS);
  pollReport(src);
}

async function pollReport(src) {
  if (src.ended && src.uploader.state().captured === 0) {
    // Nothing ever reached the server — there is no report to fetch.
    stopPolling(src);
    maybeSendDrained();
    return;
  }
  let report;
  try {
    const res = await fetch(
      config.baseUrl + "/capture/sessions/" + encodeURIComponent(src.sessionId) + "/report",
    );
    if (!res.ok) {
      // 404 until the first segment lands; 5xx: poll again. But once the
      // session has ENDED, a report that keeps failing will never turn
      // terminal (nothing may have landed at all) — stop after the grace
      // window instead of leaking this document open forever.
      if (src.ended && ++src.deadPolls >= DEAD_POLL_LIMIT) {
        src.reportLines = [
          "no server report for this session (HTTP " + res.status + ") — " +
            "nothing landed, or the server lost it; the ledger is the record",
        ];
        stopPolling(src);
        maybeSendDrained();
      }
      return;
    }
    src.deadPolls = 0;
    report = await res.json();
  } catch {
    return; // offline — keep polling (server unreachable is not terminal)
  }
  src.report = report;
  src.verdict = report.verdict || null;
  src.reportLines = reportLines(report);
  // Stop only when the answer can't change (mirrors clients/web/app.js):
  // session ended, terminal verdict, and the server has drained its processing
  // (a "gaps" verdict can appear while segments are still being emitted).
  const drained = !report.segment_states || report.segment_states.received === 0;
  if (src.ended && report.verdict && report.verdict !== "recording" && drained) {
    stopPolling(src);
    maybeSendDrained();
  }
}

function maybeSendDrained() {
  // The document's job is done when EVERY source it ran has posted its end
  // marker and its report poll has reached a terminal state (or had nothing to
  // poll). Background then closes this document. While the server is
  // unreachable the polls (and end-marker retries) keep going — the document
  // honestly stays open until the ledger has the truth.
  if (drainedSent) return;
  const live = liveSources();
  if (live.length === 0) return;
  if (!live.every((s) => s.ended && !s.pollTimer)) return;
  drainedSent = true;
  try {
    chrome.runtime.sendMessage({ target: "background", type: "drained" }).catch(() => {});
  } catch {
    /* background gone — nothing to tell */
  }
}

// ------------------------------------------------------------------ capture

async function startSource(kind, streamId) {
  // Mandatory chromeMediaSource constraints: the stream-id handoff shape
  // (D-E2). Screen capped 1920x1080 @ 15fps — screen content, not motion.
  const constraints =
    kind === "screen"
      ? {
          audio: false, // no system audio in this slice (recorded decision)
          video: {
            mandatory: {
              chromeMediaSource: "desktop",
              chromeMediaSourceId: streamId,
              maxWidth: 1920,
              maxHeight: 1080,
              maxFrameRate: 15,
            },
          },
        }
      : {
          audio: {
            mandatory: { chromeMediaSource: "tab", chromeMediaSourceId: streamId },
          },
          video: false,
        };
  const stream = await navigator.mediaDevices.getUserMedia(constraints);

  const src = {
    kind,
    sessionId: newSessionId(), // minted per source per record-press (D-E3)
    stream,
    audioCtx: null,
    active: true,
    ended: false, // end marker posted (uploader.end() completed)
    captureStopped: null,
    stopChain: null,
    report: null,
    verdict: null,
    reportLines: [],
    pollTimer: null,
    deadPolls: 0, // consecutive post-end non-OK report answers
    lastCaptureError: "",
  };

  if (kind === "tabAudio") {
    // tabCapture silences the tab for the user; passthrough keeps it audible.
    try {
      src.audioCtx = new AudioContext();
      src.audioCtx.createMediaStreamSource(stream).connect(src.audioCtx.destination);
    } catch (err) {
      // The passthrough is a courtesy; capture itself still works.
      src.lastCaptureError = "audio passthrough failed: " + errText(err);
    }
  }

  const mime = pickMime(kind === "screen" ? SCREEN_MIMES : TAB_AUDIO_MIMES);
  const recOpts = {};
  if (mime) recOpts.mimeType = mime;
  if (kind === "screen") recOpts.videoBitsPerSecond = SCREEN_BPS;

  src.uploader = createUploader({
    baseUrl: config.baseUrl,
    sessionId: src.sessionId,
    userId: config.userId,
    deviceId: config.deviceId,
  });
  src.segmenter = createSegmenter({
    createRecorder: () => new MediaRecorder(stream, recOpts),
    segmentMs: SEGMENT_MS,
    onSegment: (seg) => src.uploader.enqueue(seg), // seq assigned at enqueue
    onError: (err) => {
      src.lastCaptureError = "capture: " + errText(err);
    },
  });

  // Chrome's "Stop sharing" bar / captured tab closed -> THIS source ends
  // cleanly; the other source keeps recording (spec source-ended semantics).
  const track = stream.getTracks()[0];
  if (track) track.onended = () => stopSource(src);

  src.segmenter.start();
  startPolling(src);
  sources[kind] = src;
  return src;
}

function stopSource(src) {
  if (!src.stopChain) {
    src.captureStopped = (async () => {
      await src.segmenter.stop(); // final segment flushed (capture-loop drain)
      for (const t of src.stream.getTracks()) {
        try {
          t.stop();
        } catch {
          /* already ended */
        }
      }
      if (src.audioCtx) {
        try {
          src.audioCtx.close();
        } catch {
          /* already closed */
        }
      }
      src.active = false;
    })();
    src.stopChain = (async () => {
      await src.captureStopped;
      await src.uploader.drain(); // queue empties before the end marker
      await src.uploader.end();
      src.ended = true;
      pollReport(src); // immediate post-end poll; interval runs to terminal
      maybeSendDrained();
    })();
  }
  return src.stopChain;
}

// ----------------------------------------------------------- message handlers

async function handleStart(msg) {
  const live = liveSources();
  if (live.some((s) => s.active || !s.ended)) {
    return { ok: false, sources: {}, error: "a recording is still active or draining" };
  }
  // A previous run's sources may still be poll-terminal-pending or already
  // terminal: kill their timers before dropping the references, or the
  // orphaned intervals poll dead sessions forever (review round).
  for (const old of live) stopPolling(old);
  config = msg.config;
  sources.screen = null;
  sources.tabAudio = null;
  // Seed with ACQUISITION-stage failures (picker cancelled/errored, tab id
  // refused) carried in by background: the popup usually died with the picker,
  // so this snapshot is the only surface that can still report them. A source
  // that then fails in startSource below overwrites its own entry.
  const acq = msg.acquireErrors || {};
  startErrors.screen = acq.screen || null;
  startErrors.tabAudio = acq.tabAudio || null;
  drainedSent = false;

  const outcome = {};
  // Screen id FIRST: it is the OLDER of the two (minted at the user's pick;
  // the tab id is minted after) and unused stream ids expire in ~10 s —
  // consume the nearer-expiry one first. Screen-first ALSO serves D-E6 here:
  // if a screen id was handed over but getUserMedia fails to open it, that is
  // "screen requested but did not start" at THIS layer (the same-tab collision
  // throws 'Error starting tab capture' right here, not at acquisition), so
  // tab audio is skipped — no silent audio-only recording.
  let screenFailedAtStart = false;
  if (msg.screenStreamId) {
    try {
      await startSource("screen", msg.screenStreamId);
      outcome.screen = "capturing";
    } catch (err) {
      const raw = errText(err);
      screenFailedAtStart = true;
      outcome.screen = "error: " + raw;
      // The overwhelmingly common cause: the user picked, as their screen
      // source, the SAME tab we capture audio from — Chrome forbids capturing
      // one tab twice. Steer them to a collision-free source.
      const hint =
        msg.tabStreamId && /tab capture/i.test(raw)
          ? " — you likely picked the tab you're also capturing audio from; " +
            "Chrome can't capture one tab twice. Choose Entire Screen or a Window."
          : " — recording aborted.";
      startErrors.screen = raw + hint;
    }
  }
  if (msg.tabStreamId && !screenFailedAtStart) {
    try {
      await startSource("tabAudio", msg.tabStreamId);
      outcome.tabAudio = "capturing";
    } catch (err) {
      outcome.tabAudio = "error: " + errText(err);
      startErrors.tabAudio = errText(err);
    }
  } else if (msg.tabStreamId && screenFailedAtStart) {
    // D-E6 at the getUserMedia layer: screen was requested and failed to open,
    // so the whole recording aborts — do not start tab audio either.
    outcome.tabAudio = "skipped";
    startErrors.tabAudio = "the screen source was required for this recording";
  }
  const ok = liveSources().length > 0;
  if (!ok) {
    // Nothing started (e.g. picker cancelled on a screen-only config). The
    // startErrors just seeded must stay visible long enough for a reopened
    // popup to explain WHY — but this document must not leak forever with an
    // undismissable error row (round-2 skeptic pass): after a grace window,
    // ask background to close us, unless a newer start took the document over.
    const gen = ++startGeneration;
    setTimeout(() => {
      if (gen !== startGeneration || liveSources().length > 0) return;
      try {
        chrome.runtime.sendMessage({ target: "background", type: "drained" }).catch(() => {});
      } catch {
        /* background gone */
      }
    }, ZERO_SOURCE_LINGER_MS);
  } else {
    startGeneration += 1; // invalidate any zero-source linger timer
  }
  return ok
    ? { ok: true, sources: outcome }
    : { ok: false, sources: outcome, error: "no source could start" };
}

async function handleStop() {
  const all = liveSources();
  for (const src of all) stopSource(src);
  // Reply once the CAPTURE LOOPS have stopped; the upload drain + end markers
  // continue in this document, then the drained message closes it.
  await Promise.all(all.map((src) => src.captureStopped));
  return { ok: true, stopped: all.map((s) => s.kind) };
}

function srcSnapshot(src) {
  if (!src) return null;
  return {
    active: src.active,
    ended: src.ended,
    sessionId: src.sessionId,
    uploader: src.uploader.state(),
    verdict: src.verdict,
    reportLines: src.reportLines,
    lastCaptureError: src.lastCaptureError,
  };
}

function statusSnapshot() {
  return {
    ok: true,
    active: isActive(),
    config,
    sources: {
      screen: srcSnapshot(sources.screen),
      tabAudio: srcSnapshot(sources.tabAudio),
    },
    // Sources that failed to START (no session exists): the popup renders
    // these as error rows — without them a failed source simply vanishes.
    startErrors: { screen: startErrors.screen, tabAudio: startErrors.tabAudio },
  };
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg || msg.target !== "offscreen") return false;
  if (msg.type === "start") {
    handleStart(msg).then(sendResponse, (err) =>
      sendResponse({ ok: false, error: errText(err) })
    );
    return true; // async sendResponse
  }
  if (msg.type === "stop") {
    handleStop().then(sendResponse, (err) =>
      sendResponse({ ok: false, error: errText(err) })
    );
    return true;
  }
  if (msg.type === "status") {
    sendResponse(statusSnapshot());
    return false;
  }
  sendResponse({ ok: false, error: "unknown message type: " + msg.type });
  return false;
});

// Best-effort last-gasp end markers (stated shutdown reality): keepalive fetch
// with the last ENQUEUED seq for any session not yet ended. A hard kill still
// leaves sessions unterminated — the ledger's `unterminated` flag is for that.
window.addEventListener("pagehide", () => {
  for (const src of liveSources()) {
    if (src.ended) continue;
    const captured = src.uploader.state().captured;
    if (captured === 0) continue; // no session row exists server-side
    try {
      fetch(
        config.baseUrl + "/capture/sessions/" + encodeURIComponent(src.sessionId) + "/end",
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ last_seq: captured - 1 }),
          keepalive: true,
        },
      ).catch(() => {});
    } catch {
      /* best-effort by design */
    }
  }
});
