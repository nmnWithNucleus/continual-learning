/*
 * WS-B — Nucleus phone web client (recording-led capture M1).
 *
 * Design intent (spec: handoff/ws-b-phone-web-client.md; decisions pinned there):
 *
 * - D-M1-1 edge chunking: MediaRecorder timeslice fragments are not self-contained
 *   (and iOS is unreliable with timeslice), so the recorder is RESTARTED every
 *   SEGMENT_SECONDS; each stop yields a standalone playable blob = one upload unit.
 *   The ~tens-of-ms capture gap per restart is a stated capture reality: `seq`
 *   stays dense and per-segment t_start/t_end carry the true wall-clock spans.
 *
 * - ONE serialized upload queue: segment `seq` is sent only after `seq-1` is acked,
 *   so arrival is in-order by construction. Network errors / 5xx retry forever
 *   (backoff 1s * 2^n, cap 30s); a 4xx is a client bug — surfaced, never retried.
 *   The queue is in-memory: a page reload loses queued segments and the server
 *   ledger flags exactly which (IndexedDB persistence is a later hardening).
 *
 * - Wire (same-origin, pinned jointly with WS-C — internal, not a C-contract):
 *   POST /ingest/segments?session_id=&seq=&...  (raw blob body) -> {ok, status};
 *   POST /ingest/sessions/{id}/end {last_seq};  GET /ingest/sessions/{id}/report
 *   polled every 5s — its verdict is the tester's "it landed" signal.
 *
 * No dependencies, no build step, no external resources: this IIFE is the client.
 */
(() => {
  "use strict";

  // ---------------------------------------------------------------- tunables
  const SEGMENT_SECONDS = 10;
  const REPORT_POLL_MS = 5000;
  const BACKOFF_BASE_MS = 1000;
  const BACKOFF_CAP_MS = 30000;

  // Mime preference, probed via MediaRecorder.isTypeSupported (spec order).
  // iOS Safari lands on MP4/H.264+AAC.
  const AV_MIMES = [
    "video/mp4;codecs=avc1.42E01E,mp4a.40.2", "video/mp4",
    "video/webm;codecs=vp8,opus", "video/webm",
  ];
  const AUDIO_MIMES = ["audio/mp4", "audio/webm"];

  const LS_USER = "nucleus.recorder.user_id";
  const LS_DEVICE = "nucleus.recorder.device_suffix";

  // ---------------------------------------------------------------- DOM refs
  const $ = (id) => document.getElementById(id);
  const el = {
    banner: $("error-banner"),
    preview: $("preview"),
    micOnly: $("mic-only"),
    timer: $("timer"),
    statePill: $("state-pill"),
    pauseBtn: $("pause-btn"),
    recordBtn: $("record-btn"),
    userId: $("user-id"),
    cameraToggle: $("camera-toggle"),
    sessionId: $("session-id"),
    deviceId: $("device-id"),
    captured: $("captured"),
    uploaded: $("uploaded"),
    queued: $("queued"),
    uploadErrorRow: $("upload-error-row"),
    uploadError: $("upload-error"),
    droppedRow: $("dropped-row"),
    dropped: $("dropped"),
    reportVerdict: $("report-verdict"),
    reportStreams: $("report-streams"),
  };

  // ---------------------------------------------------------------- utilities
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const backoffDelay = (n) => Math.min(BACKOFF_BASE_MS * 2 ** n, BACKOFF_CAP_MS);

  // localStorage can throw (e.g. some private-browsing modes) — degrade quietly.
  function lsGet(key) {
    try { return localStorage.getItem(key); } catch { return null; }
  }
  function lsSet(key, value) {
    try { localStorage.setItem(key, value); } catch { /* per-session fallback */ }
  }

  // ULID-ish id (matches the server's Crockford-base32 style): 48-bit ms
  // timestamp + 80-bit randomness. Time-ordered prefix is a debugging nicety;
  // ordering is carried authoritatively by seq, never by the id.
  const CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
  function encodeTime(ms, length) {
    let out = "";
    for (let i = 0; i < length; i++) { out = CROCKFORD[ms % 32] + out; ms = Math.floor(ms / 32); }
    return out;
  }
  function randomChars(length) {
    const bytes = new Uint8Array(length);
    if (window.crypto && crypto.getRandomValues) crypto.getRandomValues(bytes);
    else for (let i = 0; i < length; i++) bytes[i] = Math.floor(Math.random() * 256);
    let out = "";
    for (let i = 0; i < length; i++) out += CROCKFORD[bytes[i] & 31];
    return out;
  }
  const newSessionId = () => encodeTime(Date.now(), 10) + randomChars(16);

  // device_id: stable per browser via a localStorage-persisted random suffix.
  function getDeviceId() {
    let suffix = lsGet(LS_DEVICE);
    if (!suffix) { suffix = randomChars(8); lsSet(LS_DEVICE, suffix); }
    return "phone-web-" + suffix;
  }
  const deviceId = getDeviceId();

  async function digestHex(blob) {
    // crypto.subtle needs a secure context; without it send sha256= empty and
    // the server computes (spec).
    if (!(window.crypto && crypto.subtle && crypto.subtle.digest)) return "";
    try {
      const digest = await crypto.subtle.digest("SHA-256", await blob.arrayBuffer());
      return Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, "0")).join("");
    } catch { return ""; }
  }

  // ---------------------------------------------------------------- state
  // UI states: idle | recording | paused | uploading | error (colors in CSS).
  let state = "idle";
  let session = null;   // per-record-press: see startSession() for the shape
  let stream = null;    // the live MediaStream (one getUserMedia per session)
  let recorder = null;  // the MediaRecorder for the CURRENT segment
  let mimeType = "";    // picked once per session
  let segmentTimer = null;
  let timerInterval = null;
  let pollTimer = null;
  let wakeLock = null;
  let accumulatedMs = 0; // recording duration accrued before the current run
  let activeSinceMs = 0; // wall-clock start of the current recording run

  function setState(next) {
    state = next;
    document.body.dataset.state = next;
    el.statePill.textContent = next;
    el.recordBtn.disabled = next === "uploading";
    const inSession = next === "recording" || next === "paused";
    el.recordBtn.setAttribute("aria-label", inSession ? "stop" : "record");
    el.pauseBtn.hidden = !inSession;
    el.pauseBtn.textContent = next === "paused" ? "Resume" : "Pause";
    const settingsLocked = !(next === "idle" || next === "error");
    el.userId.disabled = settingsLocked;
    el.cameraToggle.disabled = settingsLocked;
  }

  function showBanner(msg) { el.banner.textContent = msg; el.banner.hidden = false; }
  function hideBanner() { el.banner.textContent = ""; el.banner.hidden = true; }
  function fail(msg) { showBanner(msg); setState("error"); }

  function setLastError(msg) {
    el.uploadError.textContent = msg;
    el.uploadErrorRow.hidden = !msg;
  }

  // ---------------------------------------------------------------- status UI
  function renderStatus() {
    const s = session;
    el.sessionId.textContent = s ? s.id : "—"; // full id (wraps): new id = new session
    el.captured.textContent = s ? s.captured : "0";
    el.uploaded.textContent = s ? s.uploaded : "0";
    el.queued.textContent = s ? s.queue.length : "0";
    el.dropped.textContent = s ? s.failedCount : "0";
    el.droppedRow.hidden = !(s && s.failedCount > 0);
  }

  function fmtDuration(ms) {
    const total = Math.floor(ms / 1000);
    const two = (n) => String(n).padStart(2, "0");
    const h = Math.floor(total / 3600), m = Math.floor(total / 60) % 60;
    return (h ? h + ":" + two(m) : String(m)) + ":" + two(total % 60);
  }
  function renderTimer() {
    const running = state === "recording" ? Date.now() - activeSinceMs : 0;
    el.timer.textContent = fmtDuration(accumulatedMs + running);
  }

  function setVerdict(text, kind) {
    el.reportVerdict.textContent = text;
    const known = kind === "clean" || kind === "gaps" || kind === "recording";
    el.reportVerdict.className = known ? "v " + kind : "v none";
  }

  function renderReport(s, report) {
    el.reportStreams.textContent = "";
    if (!report) {
      setVerdict(s.ended && s.nextSeq === 0 ? "no segments captured" : "waiting for report…", null);
      return;
    }
    setVerdict(report.verdict || "?", report.verdict);
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
    for (const text of lines) {
      const div = document.createElement("div");
      div.className = "stream-line";
      div.textContent = text; // textContent: server strings never parsed as HTML
      el.reportStreams.appendChild(div);
    }
  }

  // ---------------------------------------------------------------- report poll
  function stopPolling() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = null;
  }
  function startPolling(s) {
    stopPolling();
    pollTimer = setInterval(() => pollReport(s), REPORT_POLL_MS);
    pollReport(s);
  }
  async function pollReport(s) {
    let report;
    try {
      const res = await fetch("/ingest/sessions/" + encodeURIComponent(s.id) + "/report");
      if (!res.ok) return; // 404 until the first segment lands; 5xx: just poll again
      report = await res.json();
    } catch { return; } // offline — keep polling
    if (s !== session) return; // a newer session owns the panel now
    s.report = report;
    renderReport(s, report);
    // Stop only when the answer can't change: session ended, a terminal verdict,
    // and the server has drained (a "gaps" verdict can appear while segments are
    // still processing — chunk counts keep moving until segment_states.received=0).
    const drained = !report.segment_states || report.segment_states.received === 0;
    if (s.ended && report.verdict && report.verdict !== "recording" && drained) stopPolling();
  }

  // ---------------------------------------------------------------- uploader
  const uploadError = (message, fatal) => Object.assign(new Error(message), { fatal });

  async function uploadSegment(s, seg) {
    if (seg.sha256 === undefined) seg.sha256 = await digestHex(seg.blob); // cached across retries
    const qs = new URLSearchParams({
      session_id: s.id, seq: String(seg.seq),
      user_id: s.userId, device_id: deviceId,
      t_start: new Date(seg.tStart).toISOString(), // RFC3339 UTC, ms precision
      t_end: new Date(seg.tEnd).toISOString(),
      mime: seg.mime, sha256: seg.sha256,
    });
    let res;
    try {
      res = await fetch("/ingest/segments?" + qs.toString(), {
        method: "POST",
        headers: { "content-type": "application/octet-stream" },
        body: seg.blob,
      });
    } catch (err) {
      throw uploadError("network error (" + (err && err.message ? err.message : err) + ")", false);
    }
    if (res.status >= 500) throw uploadError("server error HTTP " + res.status, false);
    if (!res.ok) {
      let detail = "";
      try { detail = (await res.text()).slice(0, 120); } catch { /* body optional */ }
      throw uploadError("HTTP " + res.status + (detail ? " — " + detail : ""), true);
    }
    // Ack body is {ok, session_id, seq, status:"received"|"duplicate"}; both
    // statuses mean the bytes are on the server, nothing to branch on here.
  }

  async function pump(s) {
    if (s.pumping) return;
    s.pumping = true;
    try {
      while (s.queue.length > 0) {
        const seg = s.queue[0];
        for (let attempt = 0; ; attempt++) {
          try {
            await uploadSegment(s, seg);
            s.uploaded += 1;
            setLastError("");
            break;
          } catch (err) {
            if (err.fatal) {
              // A 4xx is a bug, not a transient — retrying cannot help. Drop the
              // segment so the queue (and the end marker) keeps moving; the
              // ledger's client leg will show exactly this seq as missing.
              s.failedCount += 1;
              setLastError("seq " + seg.seq + ": " + err.message + " (4xx — not retried)");
              break;
            }
            const delay = backoffDelay(attempt);
            setLastError("seq " + seg.seq + ": " + err.message + " — retrying in " + Math.round(delay / 1000) + "s");
            await sleep(delay);
          }
        }
        s.queue.shift();
        renderStatus();
      }
    } finally {
      s.pumping = false;
    }
    if (s.drainResolve && s.queue.length === 0) {
      const resolve = s.drainResolve;
      s.drainResolve = null;
      resolve();
    }
  }

  function drained(s) {
    if (s.queue.length === 0 && !s.pumping) return Promise.resolve();
    return new Promise((resolve) => { s.drainResolve = resolve; });
  }

  // ---------------------------------------------------------------- end marker
  async function postEnd(s, lastSeq) {
    for (let attempt = 0; ; attempt++) {
      try {
        const res = await fetch("/ingest/sessions/" + encodeURIComponent(s.id) + "/end", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ last_seq: lastSeq }),
        });
        if (res.ok) return;
        if (res.status < 500) {
          setLastError("end marker: HTTP " + res.status + " (4xx — not retried)");
          return;
        }
      } catch { /* network — fall through to backoff */ }
      await sleep(backoffDelay(attempt));
    }
  }

  // A killed/hidden page still terminates the ledger session: beacon the end
  // marker with the last *enqueued* seq (idempotent; a clean Stop later posts
  // the same last_seq after the drain).
  function beaconEnd() {
    const s = session;
    if (!s || s.ended || s.nextSeq === 0) return;
    const url = "/ingest/sessions/" + encodeURIComponent(s.id) + "/end";
    const body = JSON.stringify({ last_seq: s.nextSeq - 1 });
    let sent = false;
    try {
      if (navigator.sendBeacon) sent = navigator.sendBeacon(url, new Blob([body], { type: "application/json" }));
    } catch { /* some Safari versions reject non-safelisted Blob types */ }
    if (!sent) {
      try {
        fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body, keepalive: true })
          .catch(() => {});
      } catch { /* best-effort by design */ }
    }
  }

  // ---------------------------------------------------------------- wake lock
  async function acquireWakeLock() {
    if (!("wakeLock" in navigator) || wakeLock) return;
    try {
      wakeLock = await navigator.wakeLock.request("screen");
      wakeLock.addEventListener("release", () => { wakeLock = null; });
    } catch { /* best-effort (iOS 16.4+/Chrome; may be denied) */ }
  }
  function releaseWakeLock() {
    try { if (wakeLock) wakeLock.release(); } catch { /* already gone */ }
    wakeLock = null;
  }

  // ---------------------------------------------------------------- capture
  function pickMime(audioOnly) {
    const candidates = audioOnly ? AUDIO_MIMES : AV_MIMES;
    if (window.MediaRecorder && MediaRecorder.isTypeSupported) {
      for (const m of candidates) if (MediaRecorder.isTypeSupported(m)) return m;
    }
    return ""; // let the browser choose; the real type is read off the recorder
  }

  function mediaErrorMessage(err, wantVideo) {
    const what = wantVideo ? "camera & microphone" : "microphone";
    if (!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia)) {
      return "Capture needs a secure context — open this page over HTTPS (or localhost).";
    }
    switch (err && err.name) {
      case "NotAllowedError":
      case "SecurityError":
        return "Permission for the " + what + " was denied. Allow access in the browser prompt (or Settings) and tap record again.";
      case "NotFoundError":
      case "OverconstrainedError":
        return "No usable " + what + " found on this device.";
      case "NotReadableError":
        return "The " + what + " is busy in another app — close it and tap record again.";
      default:
        return "Could not open the " + what + ": " + (err && err.message ? err.message : err);
    }
  }

  function startSegment(s) {
    let rec;
    try {
      rec = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
    } catch {
      mimeType = ""; // picked type refused at construction — fall back to browser default
      rec = new MediaRecorder(stream);
    }
    recorder = rec;
    const parts = [];
    const tStart = Date.now(); // wall-clock stamps, spec D-M1-1
    rec.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) parts.push(event.data);
    };
    rec.onerror = (event) => {
      // Recorder goes inactive on error; onstop still fires and continues the loop.
      setLastError("recorder error: " + (event && event.error ? event.error : "unknown"));
    };
    rec.onstop = () => {
      const tEnd = Date.now();
      s.awaitingStop = false;
      if (segmentTimer) { clearTimeout(segmentTimer); segmentTimer = null; }
      const mime = rec.mimeType || mimeType || (s.audioOnly ? "audio/webm" : "video/webm");
      const blob = new Blob(parts, { type: mime });
      if (blob.size > 0) {
        // seq assigned at enqueue so it stays dense even if a segment came up empty
        s.queue.push({ seq: s.nextSeq++, blob, tStart, tEnd, mime });
        s.captured += 1;
        pump(s);
      }
      renderStatus();
      if (s.mode === "rolling") startSegment(s); // same MediaStream — no re-prompt
      else if (s.mode === "stopping") finishSession(s);
      // mode === "paused": hold; resumeSession() starts the next segment.
    };
    rec.start(); // NO timeslice: fragments would not be self-contained (D-M1-1)
    segmentTimer = setTimeout(() => {
      // MediaRecorder.stop() flips state synchronously but fires onstop later;
      // awaitingStop marks that window so stop/pause taps landing inside it
      // defer to the pending onstop instead of double-finishing.
      if (rec.state !== "inactive") { s.awaitingStop = true; rec.stop(); }
    }, SEGMENT_SECONDS * 1000);
  }

  async function startSession() {
    hideBanner();
    setLastError("");
    const wantVideo = el.cameraToggle.checked;
    if (!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia)) {
      return fail(mediaErrorMessage(null, wantVideo));
    }
    if (typeof MediaRecorder === "undefined") {
      return fail("This browser has no MediaRecorder support — cannot capture.");
    }
    el.recordBtn.disabled = true; // guard double-tap while the permission prompt is up
    try {
      // Capture must start from this user tap (iOS requirement).
      stream = await navigator.mediaDevices.getUserMedia(
        wantVideo
          ? { video: { facingMode: "environment", width: { ideal: 640 } }, audio: true }
          : { video: false, audio: true }
      );
    } catch (err) {
      el.recordBtn.disabled = false;
      return fail(mediaErrorMessage(err, wantVideo));
    }
    el.recordBtn.disabled = false;

    mimeType = pickMime(!wantVideo);
    if (wantVideo) el.preview.srcObject = stream;
    el.micOnly.hidden = wantVideo;

    const userId = el.userId.value.trim() || "beta-user";
    el.userId.value = userId;
    lsSet(LS_USER, userId);

    stopPolling();
    session = {
      id: newSessionId(), // minted per record-press (spec)
      userId,
      audioOnly: !wantVideo,
      mode: "rolling", // rolling | paused | stopping
      nextSeq: 0, captured: 0, uploaded: 0, failedCount: 0,
      queue: [], pumping: false, drainResolve: null,
      awaitingStop: false, // a rec.stop() was requested but its onstop hasn't fired
      finishing: false, ended: false, report: null,
    };
    accumulatedMs = 0;
    activeSinceMs = Date.now();
    if (timerInterval) clearInterval(timerInterval);
    timerInterval = setInterval(renderTimer, 250);
    renderTimer();
    renderStatus();
    renderReport(session, null);
    setState("recording");
    acquireWakeLock();
    startPolling(session);
    startSegment(session);
  }

  function pauseSession() {
    const s = session;
    if (!s || s.mode !== "rolling") return;
    s.mode = "paused";
    if (segmentTimer) { clearTimeout(segmentTimer); segmentTimer = null; }
    accumulatedMs += Date.now() - activeSinceMs;
    setState("paused");
    if (recorder && recorder.state !== "inactive") {
      s.awaitingStop = true;
      recorder.stop(); // onstop enqueues the segment, does not restart
    }
  }

  function resumeSession() {
    const s = session;
    if (!s || s.mode !== "paused") return;
    s.mode = "rolling";
    activeSinceMs = Date.now();
    setState("recording");
    // If the pause's onstop is still pending (stop() flips state synchronously,
    // onstop fires later), IT starts the next segment on seeing mode "rolling" —
    // starting one here too would run two recorders over the same stream.
    if (!s.awaitingStop) startSegment(s);
  }

  function stopSession() {
    const s = session;
    if (!s || s.mode === "stopping") return;
    s.mode = "stopping";
    if (segmentTimer) { clearTimeout(segmentTimer); segmentTimer = null; }
    if (recorder && recorder.state !== "inactive") {
      s.awaitingStop = true;
      recorder.stop(); // onstop enqueues the tail segment, then calls finishSession
    } else if (!s.awaitingStop) {
      finishSession(s); // no recorder running, no onstop in flight — nothing to flush
    }
    // else: an onstop is in flight (segment rollover or a just-tapped pause);
    // it will see mode === "stopping" and call finishSession itself.
  }

  async function finishSession(s) {
    if (s.finishing) return; // belt-and-braces: exactly one drain + end marker
    s.finishing = true;
    if (state === "recording") accumulatedMs += Date.now() - activeSinceMs;
    recorder = null;
    if (stream) {
      for (const track of stream.getTracks()) track.stop();
      stream = null;
    }
    el.preview.srcObject = null;
    el.micOnly.hidden = true;
    releaseWakeLock();
    setState("uploading");
    if (timerInterval) { clearInterval(timerInterval); timerInterval = null; }
    renderTimer(); // freeze the final duration on screen

    await drained(s); // queue empties before the end marker (spec)
    if (s.nextSeq > 0) {
      await postEnd(s, s.nextSeq - 1);
      s.ended = true;
      pollReport(s); // immediate post-end poll; interval continues until terminal verdict
    } else {
      s.ended = true;
      stopPolling(); // nothing ever reached the server — there is no report
      renderReport(s, null);
    }
    renderStatus();
    setState("idle");
  }

  // ---------------------------------------------------------------- wiring
  el.recordBtn.addEventListener("click", () => {
    if (state === "idle" || state === "error") startSession();
    else if (state === "recording" || state === "paused") stopSession();
  });
  el.pauseBtn.addEventListener("click", () => {
    if (state === "recording") pauseSession();
    else if (state === "paused") resumeSession();
  });
  el.userId.addEventListener("change", () => {
    lsSet(LS_USER, el.userId.value.trim() || "beta-user");
  });

  window.addEventListener("pagehide", beaconEnd);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") beaconEnd();
    else if (session && !session.ended) acquireWakeLock(); // OS drops wake locks on hide
  });

  // ---------------------------------------------------------------- init
  el.userId.value = lsGet(LS_USER) || "beta-user";
  el.deviceId.textContent = deviceId;
  el.deviceId.title = deviceId;
  renderStatus();
  setState("idle");
})();
