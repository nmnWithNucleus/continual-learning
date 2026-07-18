/*
 * WS-E — segmented capture loop (shared ES module; the D-M1-1 restart loop as
 * a pure state machine over an injected recorder factory, `deno test`-able).
 *
 * Design intent (spec: handoff/ws-e-extension.md D-E4; lineage: ws-b D-M1-1,
 * clients/web/app.js):
 *
 * - MediaRecorder timeslice fragments are NOT self-contained (only the first
 *   carries the container init segment), so each ~segmentMs window runs its
 *   OWN recorder, stopped after segmentMs; every onstop yields a standalone
 *   playable blob = one upload unit. The ~tens-of-ms capture gap per restart
 *   is a stated capture reality; per-segment tStart/tEnd carry the true
 *   wall-clock spans (from the injected `now`).
 * - This module does NOT assign seq — onSegment receives {blob, tStart, tEnd,
 *   mime} and the uploader numbers densely at enqueue, so an empty segment
 *   (blob.size 0: skipped here, loop continues) never punches a seq hole.
 * - stop() resolves after the FINAL segment's onstop has emitted — it drains
 *   the capture loop, not the upload queue (the uploader owns that drain).
 * - The stop-during-pending-onstop race (stop() landing between a timer-fired
 *   recorder.stop() and its async onstop) is guarded exactly like the phone
 *   client: an `awaitingStop` latch marks the window, and the pending onstop
 *   — seeing mode "stopping" — finishes the machine itself. A recorder that
 *   self-stops (its stream ended, e.g. Chrome's "Stop sharing" bar) is the
 *   same window without our latch, so stop() also defers to any segment whose
 *   onstop hasn't fired yet.
 * - createRecorder() must return a MediaRecorder-like object: {state, mimeType,
 *   start(), stop(), ondataavailable, onstop, onerror}. If creating/starting
 *   the next recorder throws (dead stream), the loop finishes cleanly via
 *   onError — capture cannot continue, and pretending otherwise would be a
 *   silent gap.
 */

export function createSegmenter(opts) {
  const createRecorder = opts.createRecorder;
  const segmentMs = opts.segmentMs ?? 10000;
  const setT = opts.setTimeout || ((fn, ms) => globalThis.setTimeout(fn, ms));
  const clearT = opts.clearTimeout || ((id) => globalThis.clearTimeout(id));
  const nowFn = opts.now || (() => Date.now());
  const onSegment = opts.onSegment || (() => {});
  const onError = opts.onError || (() => {});

  let mode = "idle"; // idle | rolling | stopping | stopped
  let current = null; // {rec, parts, tStart, done} — the segment in flight
  let awaitingStop = false; // a rec.stop() was issued; its onstop hasn't fired
  let timer = null;
  let finished = false;
  let stopWaiters = [];

  function clearTimer() {
    if (timer !== null) {
      clearT(timer);
      timer = null;
    }
  }

  function finish() {
    if (finished) return; // exactly one terminal transition
    finished = true;
    mode = "stopped";
    clearTimer();
    const waiters = stopWaiters;
    stopWaiters = [];
    for (const resolve of waiters) resolve();
  }

  function startSegment() {
    let rec;
    try {
      rec = createRecorder();
    } catch (err) {
      onError(err);
      finish();
      return;
    }
    const seg = { rec, parts: [], tStart: nowFn(), done: false };
    current = seg;
    rec.ondataavailable = (event) => {
      if (event && event.data && event.data.size > 0) seg.parts.push(event.data);
    };
    rec.onerror = (event) => {
      // The recorder goes inactive on error; its onstop still fires and
      // continues (or ends) the loop — nothing else to unwind here.
      onError((event && event.error) || new Error("recorder error"));
    };
    rec.onstop = () => {
      const tEnd = nowFn();
      seg.done = true;
      awaitingStop = false;
      clearTimer();
      const mime = rec.mimeType || "application/octet-stream";
      const blob = new Blob(seg.parts, { type: mime });
      if (blob.size > 0) {
        try {
          onSegment({ blob, tStart: seg.tStart, tEnd, mime });
        } catch (err) {
          onError(err);
        }
      }
      // Empty blob: skipped, but the loop continues — numbering lives at the
      // uploader's enqueue, so density is unaffected by the skip.
      if (mode === "rolling") startSegment();
      else if (mode === "stopping") finish();
    };
    try {
      rec.start(); // NO timeslice — fragments would not be self-contained
    } catch (err) {
      onError(err);
      finish();
      return;
    }
    timer = setT(() => {
      timer = null;
      // stop() flips recorder state synchronously but fires onstop later;
      // awaitingStop marks that window so a stop() call landing inside it
      // defers to the pending onstop instead of double-finishing.
      if (rec.state !== "inactive") {
        awaitingStop = true;
        rec.stop();
      }
    }, segmentMs);
  }

  function start() {
    if (mode !== "idle") throw new Error("segmenter already started (mode " + mode + ")");
    mode = "rolling";
    startSegment();
  }

  function stop() {
    if (mode === "idle") {
      finish(); // never started — nothing to flush
    } else if (mode === "rolling") {
      mode = "stopping";
      clearTimer();
      if (current && current.rec.state !== "inactive") {
        awaitingStop = true;
        current.rec.stop(); // its onstop emits the tail segment, then finishes
      } else if (!awaitingStop && (!current || current.done)) {
        finish(); // no recorder running, no onstop in flight — nothing to flush
      }
      // else: an onstop is pending (our timer's stop(), or the recorder
      // self-stopped because its stream ended); it sees mode "stopping"
      // and finishes after emitting the final segment.
    }
    // mode "stopping"/"stopped": idempotent — await the same completion.
    if (finished) return Promise.resolve();
    return new Promise((resolve) => stopWaiters.push(resolve));
  }

  return { start, stop, state: () => mode };
}
