// @ts-nocheck
/* global chrome */
/*
 * WS-E — offscreen capture engine (D-E5): the stream, recorder, queue, poll.
 *
 * D-E7 — ONE tab, ONE muxed capture, ONE ingest session. getUserMedia pulls the
 * active tab's video + audio together (chromeMediaSource "tab") from the stream
 * id background minted; a single MediaRecorder writes ~10 s self-contained webm
 * segments (video+audio muxed), a serialized uploader speaks the wire, and the
 * server demuxes each segment into audio + video C1 streams — the same
 * muxed-A/V path the phone and mac clients already use. No screen picker, no
 * two-session bookkeeping, no cross-context stream-id handoff.
 *
 * Segmentation is the D-M1-1 restart loop (~10 s self-contained blobs; timeslice
 * fragments aren't self-contained). The 5 s report poll's verdict is the
 * tester's "it landed" signal (mirrors clients/web/app.js pollReport).
 *
 * tabCapture silences the captured tab for the user, so the audio is routed
 * through an AudioContext passthrough to keep it audible.
 *
 * Source-ended semantics: closing / navigating the captured tab (or the browser
 * stop-sharing affordance) fires track.onended -> the session stops cleanly
 * (final segment, drain, end marker).
 *
 * Shutdown reality: pagehide -> keepalive end marker, best-effort only. A hard
 * Chrome kill leaves the session unterminated — exactly what the ledger's
 * `unterminated` flag is for.
 */

import { createUploader } from "./uploader.js";
import { createSegmenter } from "./segmenter.js";

const SEGMENT_MS = 10000;
const REPORT_POLL_MS = 5000;
const TAB_VIDEO_BPS = 2_500_000; // page content, not motion video

// Mime preference (Chrome), probed via MediaRecorder.isTypeSupported.
const AV_MIMES = ["video/webm;codecs=vp9,opus", "video/webm;codecs=vp8,opus", "video/webm"];
const AUDIO_MIMES = ["audio/webm;codecs=opus", "audio/webm"];

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
let session = null; // the single active/draining capture session (see startCapture)
let starting = false; // a start is between its guard and assigning `session`
let startError = null; // a start that never produced a session (getUserMedia refusal)
let drainedSent = false;

// After the end marker, a report that keeps answering non-OK will never turn
// terminal (e.g. every segment 4xx-dropped: no session server-side). Stop after
// this many post-end polls instead of leaking the document open.
const DEAD_POLL_LIMIT = 6; // x 5s interval = ~30s of grace

// A start that produced NO session keeps the document alive only long enough to
// explain itself, then asks to be closed.
const ZERO_SOURCE_LINGER_MS = 60_000;
let startGeneration = 0; // bumps per start; stale linger timers self-disarm

const isActive = () => !!(session && session.active);

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

function stopPolling(s) {
  if (s.pollTimer) clearInterval(s.pollTimer);
  s.pollTimer = null;
}

function startPolling(s) {
  stopPolling(s);
  s.pollTimer = setInterval(() => pollReport(s), REPORT_POLL_MS);
  pollReport(s);
}

async function pollReport(s) {
  if (s !== session) return; // a newer session owns the document now
  if (s.ended && s.uploader.state().captured === 0) {
    // Nothing ever reached the server — there is no report to fetch.
    stopPolling(s);
    maybeSendDrained();
    return;
  }
  let report;
  try {
    const res = await fetch(
      config.baseUrl + "/capture/sessions/" + encodeURIComponent(s.sessionId) + "/report",
    );
    if (!res.ok) {
      // 404 until the first segment lands; 5xx: poll again. Once ENDED, a report
      // that keeps failing will never turn terminal — stop after the grace
      // window instead of leaking this document open forever.
      if (s.ended && ++s.deadPolls >= DEAD_POLL_LIMIT) {
        s.reportLines = [
          "no server report for this session (HTTP " + res.status + ") — " +
            "nothing landed, or the server lost it; the ledger is the record",
        ];
        stopPolling(s);
        maybeSendDrained();
      }
      return;
    }
    s.deadPolls = 0;
    report = await res.json();
  } catch {
    return; // offline — keep polling (server unreachable is not terminal)
  }
  s.report = report;
  s.verdict = report.verdict || null;
  s.reportLines = reportLines(report);
  // Stop only when the answer can't change (mirrors clients/web/app.js): ended,
  // terminal verdict, and the server has drained (a "gaps" verdict can appear
  // while segments are still being emitted).
  const drained = !report.segment_states || report.segment_states.received === 0;
  if (s.ended && report.verdict && report.verdict !== "recording" && drained) {
    stopPolling(s);
    maybeSendDrained();
  }
}

function maybeSendDrained() {
  // The document's job is done when the session posted its end marker and its
  // report poll reached a terminal state (or had nothing to poll). Background
  // then closes this document. While the server is unreachable the polls (and
  // end-marker retries) keep going — the document honestly stays open until the
  // ledger has the truth.
  if (drainedSent) return;
  if (!session || !session.ended || session.pollTimer) return;
  drainedSent = true;
  try {
    chrome.runtime.sendMessage({ target: "background", type: "drained" }).catch(() => {});
  } catch {
    /* background gone — nothing to tell */
  }
}

// ------------------------------------------------------------------ capture

async function startCapture(tabStreamId, wantVideo) {
  // The muxed tab stream: video + audio (or audio-only). chromeMediaSource
  // "tab" with the id background minted from the popup-pinned tab.
  const audio = { mandatory: { chromeMediaSource: "tab", chromeMediaSourceId: tabStreamId } };
  const video = wantVideo
    ? { mandatory: { chromeMediaSource: "tab", chromeMediaSourceId: tabStreamId } }
    : false;
  const stream = await navigator.mediaDevices.getUserMedia({ audio, video });

  const s = {
    sessionId: newSessionId(),
    stream,
    audioCtx: null,
    audioOnly: !wantVideo,
    active: true,
    ended: false, // end marker posted (uploader.end() completed)
    captureStopped: null,
    stopChain: null,
    report: null,
    verdict: null,
    reportLines: [],
    pollTimer: null,
    deadPolls: 0,
    lastCaptureError: "",
  };

  // tabCapture silences the tab for the user; passthrough keeps it audible. A
  // fresh AudioContext can start suspended (no user gesture in this doc) — resume
  // it so the passthrough is actually audible.
  try {
    s.audioCtx = new AudioContext();
    s.audioCtx.createMediaStreamSource(stream).connect(s.audioCtx.destination);
    if (s.audioCtx.state === "suspended") s.audioCtx.resume().catch(() => {});
  } catch (err) {
    s.lastCaptureError = "audio passthrough failed: " + errText(err);
  }

  const mime = pickMime(wantVideo ? AV_MIMES : AUDIO_MIMES);
  const recOpts = {};
  if (mime) recOpts.mimeType = mime;
  if (wantVideo) recOpts.videoBitsPerSecond = TAB_VIDEO_BPS;

  s.uploader = createUploader({
    baseUrl: config.baseUrl,
    sessionId: s.sessionId,
    userId: config.userId,
    deviceId: config.deviceId,
  });
  s.segmenter = createSegmenter({
    createRecorder: () => new MediaRecorder(stream, recOpts),
    segmentMs: SEGMENT_MS,
    onSegment: (seg) => s.uploader.enqueue(seg), // seq assigned at enqueue
    onError: (err) => {
      s.lastCaptureError = "capture: " + errText(err);
    },
  });

  // Captured tab closed / navigated (or a stop-sharing affordance) ends a
  // track -> stop the session cleanly. Identity-guarded: an onended from a
  // superseded stream must never stop the CURRENT session.
  for (const track of stream.getTracks()) {
    track.onended = () => {
      if (session === s) stopCapture();
    };
  }

  s.segmenter.start();
  session = s;
  startPolling(s);
  return s;
}

function stopCapture() {
  const s = session;
  if (!s || s.stopChain) return s ? s.stopChain : Promise.resolve();
  s.captureStopped = (async () => {
    await s.segmenter.stop(); // final segment flushed (capture-loop drain)
    for (const t of s.stream.getTracks()) {
      try {
        t.stop();
      } catch {
        /* already ended */
      }
    }
    if (s.audioCtx) {
      try {
        s.audioCtx.close();
      } catch {
        /* already closed */
      }
    }
    s.active = false;
  })();
  s.stopChain = (async () => {
    await s.captureStopped;
    await s.uploader.drain(); // queue empties before the end marker
    await s.uploader.end();
    s.ended = true;
    pollReport(s); // immediate post-end poll; interval runs to terminal
    maybeSendDrained();
  })();
  return s.stopChain;
}

// ----------------------------------------------------------- message handlers

async function handleStart(msg) {
  // `starting` spans the getUserMedia await inside startCapture — without it a
  // second concurrent start (e.g. an impatient double Record during the
  // first-run permission dialog) would pass the guard while `session` is still
  // null and orphan the first capture (skeptic round).
  if (starting || (session && (session.active || !session.ended))) {
    return { ok: false, error: "a recording is still active or draining" };
  }
  if (session) stopPolling(session);
  config = msg.config;
  session = null;
  startError = null;
  drainedSent = false;
  starting = true;

  try {
    await startCapture(msg.tabStreamId, msg.video !== false);
    startGeneration += 1; // a real session invalidates any stale linger timer
    return { ok: true, session: snapshot() };
  } catch (err) {
    // getUserMedia refused (e.g. the tab became uncapturable). No session
    // exists; keep the reason visible on the status surface (the popup may have
    // died) but don't leak the document — self-close after the grace window.
    startError = errText(err);
    const gen = ++startGeneration;
    setTimeout(() => {
      if (gen !== startGeneration || session) return;
      try {
        chrome.runtime.sendMessage({ target: "background", type: "drained" }).catch(() => {});
      } catch {
        /* background gone */
      }
    }, ZERO_SOURCE_LINGER_MS);
    return { ok: false, error: startError };
  } finally {
    starting = false;
  }
}

async function handleStop() {
  const s = session;
  if (!s) return { ok: true, idle: true };
  stopCapture();
  await s.captureStopped; // reply once capture stopped; uploads drain after
  return { ok: true };
}

function snapshot() {
  if (!session) return null;
  const s = session;
  return {
    active: s.active,
    ended: s.ended,
    audioOnly: s.audioOnly,
    sessionId: s.sessionId,
    uploader: s.uploader.state(),
    verdict: s.verdict,
    reportLines: s.reportLines,
    lastCaptureError: s.lastCaptureError,
  };
}

function statusSnapshot() {
  return {
    ok: true,
    active: isActive(),
    config,
    session: snapshot(),
    // A start that produced no session (getUserMedia refusal): the popup renders
    // this reason as an error row — otherwise a failed start simply vanishes.
    startError,
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

// Best-effort last-gasp end marker (stated shutdown reality): keepalive fetch
// with the last ENQUEUED seq if the session isn't ended. A hard kill still
// leaves it unterminated — the ledger's `unterminated` flag is for that.
window.addEventListener("pagehide", () => {
  const s = session;
  if (!s || s.ended) return;
  const captured = s.uploader.state().captured;
  if (captured === 0) return; // no session row exists server-side
  try {
    fetch(
      config.baseUrl + "/capture/sessions/" + encodeURIComponent(s.sessionId) + "/end",
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
});
