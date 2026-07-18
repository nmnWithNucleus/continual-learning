/*
 * WS-E — segmenter.js conformance (deno test; fake recorders, fake timers,
 * fake clock — the D-M1-1 restart loop exercised as a pure state machine).
 *
 * Covered: wall-clock stamping from the injected `now`, restart-while-rolling,
 * stop() draining the pending onstop exactly once, the stop-during-pending-
 * onstop race (stop() landing between the timer-fired recorder.stop() and its
 * async onstop) yielding no double segment and no hang, and empty-blob skip
 * with uploader seq numbering unaffected (density lives at enqueue).
 *
 * Fake recorders mirror the MediaRecorder contract the segmenter relies on:
 * stop() flips state to "inactive" SYNCHRONOUSLY, onstop fires later (the test
 * fires it by hand) — that asymmetry is exactly what the awaitingStop latch
 * guards.
 */

import { createSegmenter } from "../segmenter.js";
import { createUploader } from "../uploader.js";

function assert(cond, msg) {
  if (!cond) throw new Error("assert failed" + (msg ? ": " + msg : ""));
}
function assertEquals(actual, expected, msg) {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) {
    throw new Error("assertEquals failed" + (msg ? " (" + msg + ")" : "") + ": " + a + " !== " + e);
  }
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

function harness(opts = {}) {
  // Fake timers: collected, fired by hand, duration recorded.
  const timers = new Map();
  let nextTimerId = 1;
  const setT = (fn, ms) => {
    const id = nextTimerId++;
    timers.set(id, { fn, ms });
    return id;
  };
  const clearT = (id) => timers.delete(id);
  const fireNext = () => {
    const entry = timers.entries().next().value;
    if (!entry) throw new Error("no pending timer to fire");
    const [id, t] = entry;
    timers.delete(id);
    t.fn();
    return t;
  };

  // Fake recorders: state flips synchronously on stop(); onstop is manual.
  const recs = [];
  const createRecorder = () => {
    const rec = {
      state: "inactive",
      mimeType: opts.mimeType ?? "video/webm",
      ondataavailable: null,
      onstop: null,
      onerror: null,
      start() {
        this.state = "recording";
      },
      stop() {
        this.state = "inactive"; // onstop fired later, by the test
      },
      data(size) {
        this.ondataavailable({ data: new Blob([new Uint8Array(size)]) });
      },
    };
    recs.push(rec);
    return rec;
  };

  let t = 0;
  const clock = { now: () => t, set: (v) => (t = v) };

  const segments = [];
  const errors = [];
  const seg = createSegmenter({
    createRecorder,
    segmentMs: opts.segmentMs ?? 10000,
    setTimeout: setT,
    clearTimeout: clearT,
    now: clock.now,
    onSegment: (s) => {
      segments.push(s);
      if (opts.onSegment) opts.onSegment(s);
    },
    onError: (e) => errors.push(e),
  });
  return { seg, recs, segments, errors, clock, timers, fireNext };
}

Deno.test("emits wall-clock-stamped segments and restarts while rolling", () => {
  const h = harness();
  h.clock.set(1000);
  h.seg.start();
  assertEquals(h.recs.length, 1);
  assertEquals(h.recs[0].state, "recording");
  assertEquals(h.timers.size, 1, "segment timer armed");

  h.recs[0].data(7);
  h.clock.set(11000);
  const timer = h.fireNext(); // segmentMs elapsed -> rec.stop()
  assertEquals(timer.ms, 10000);
  assertEquals(h.recs[0].state, "inactive", "stop flips state synchronously");
  assertEquals(h.segments.length, 0, "nothing emitted until onstop");

  h.recs[0].onstop();
  assertEquals(h.segments.length, 1);
  assertEquals(h.segments[0].tStart, 1000, "stamped from injected now at recorder start");
  assertEquals(h.segments[0].tEnd, 11000, "stamped from injected now at onstop");
  assertEquals(h.segments[0].mime, "video/webm");
  assertEquals(h.segments[0].blob.size, 7);
  // Restarted while rolling: a NEW recorder, already recording, timer re-armed.
  assertEquals(h.recs.length, 2);
  assertEquals(h.recs[1].state, "recording");
  assertEquals(h.timers.size, 1);
  assertEquals(h.seg.state(), "rolling");

  // Second cycle keeps rolling — restarts are unbounded by design.
  h.recs[1].data(3);
  h.clock.set(21000);
  h.fireNext();
  h.recs[1].onstop();
  assertEquals(h.segments.length, 2);
  assertEquals(h.segments[1].tStart, 11000);
  assertEquals(h.recs.length, 3);
});

Deno.test("stop() mid-segment resolves only after the final onstop emitted", async () => {
  const h = harness();
  h.clock.set(0);
  h.seg.start();
  h.recs[0].data(3);
  h.clock.set(4000);

  const p = h.seg.stop(); // user stop mid-segment
  assertEquals(h.recs[0].state, "inactive", "stop() stopped the live recorder");
  let resolved = false;
  p.then(() => (resolved = true));
  await tick();
  assertEquals(resolved, false, "stop() must wait for the pending onstop");

  h.recs[0].onstop();
  await p; // resolves — capture loop drained
  assertEquals(h.segments.length, 1, "final segment emitted exactly once");
  assertEquals(h.segments[0].tEnd, 4000);
  assertEquals(h.recs.length, 1, "no restart after stop");
  assertEquals(h.timers.size, 0, "segment timer cleared");
  assertEquals(h.seg.state(), "stopped");
});

Deno.test("stop() inside the pending-onstop window: one segment, no hang", async () => {
  const h = harness();
  h.clock.set(0);
  h.seg.start();
  h.recs[0].data(2);
  h.clock.set(10000);

  h.fireNext(); // timer fired: rec.stop() issued, onstop still pending
  assertEquals(h.recs[0].state, "inactive");
  const p = h.seg.stop(); // lands exactly inside the race window

  h.recs[0].onstop(); // the pending onstop sees mode "stopping" and finishes
  await p; // no hang
  assertEquals(h.segments.length, 1, "no double segment");
  assertEquals(h.recs.length, 1, "the rollover did NOT start a new recorder");
  assertEquals(h.seg.state(), "stopped");
});

Deno.test("empty segment skipped, loop continues, uploader numbering unaffected", async () => {
  const calls = [];
  const fetch = async (url, _opts) => {
    calls.push(new URL(url));
    return { ok: true, status: 200, text: async () => "" };
  };
  let up;
  const h = harness({ onSegment: (s) => up.enqueue(s) });
  up = createUploader({
    baseUrl: "http://s",
    sessionId: "S",
    userId: "u",
    deviceId: "d",
    fetch,
    sleep: async () => {},
    digest: async () => "aa",
  });

  h.clock.set(0);
  h.seg.start();
  // Segment window 0: no data -> empty blob -> SKIPPED, but the loop continues.
  h.clock.set(10000);
  h.fireNext();
  h.recs[0].onstop();
  assertEquals(h.segments.length, 0, "empty blob not emitted");
  assertEquals(h.recs.length, 2, "loop continued past the skip");

  // Two real segments follow.
  h.recs[1].data(4);
  h.clock.set(20000);
  h.fireNext();
  h.recs[1].onstop();
  h.recs[2].data(5);
  h.clock.set(30000);
  h.fireNext();
  h.recs[2].onstop();

  const p = h.seg.stop();
  h.recs[3].onstop(); // empty tail — skipped too
  await p;
  await up.drain();

  assertEquals(h.segments.length, 2);
  assertEquals(
    calls.map((u) => u.searchParams.get("seq")),
    ["0", "1"],
    "seq assigned at enqueue: dense despite skipped windows",
  );
  assertEquals(up.state().captured, 2);
});

Deno.test("stop() on an idle segmenter resolves; repeated stop() is idempotent", async () => {
  const h = harness();
  await h.seg.stop(); // never started — nothing to flush, no hang
  assertEquals(h.seg.state(), "stopped");

  const h2 = harness();
  h2.seg.start();
  h2.recs[0].data(1);
  const p1 = h2.seg.stop();
  const p2 = h2.seg.stop(); // second stop awaits the SAME completion
  h2.recs[0].onstop();
  await p1;
  await p2;
  assertEquals(h2.segments.length, 1, "exactly one final segment despite two stop() calls");
});

Deno.test("recorder factory failure ends the loop cleanly via onError", async () => {
  // A dead stream (e.g. Chrome's Stop-sharing) makes the NEXT recorder throw:
  // the loop must finish — pretending to continue would be a silent gap.
  const timers = new Map();
  let id = 1;
  let created = 0;
  let rec0;
  const errors = [];
  const seg = createSegmenter({
    createRecorder: () => {
      created += 1;
      if (created > 1) throw new Error("stream ended");
      rec0 = {
        state: "inactive",
        mimeType: "video/webm",
        ondataavailable: null,
        onstop: null,
        onerror: null,
        start() {
          this.state = "recording";
        },
        stop() {
          this.state = "inactive";
        },
      };
      return rec0;
    },
    segmentMs: 10000,
    setTimeout: (fn, ms) => (timers.set(id, { fn, ms }), id++),
    clearTimeout: (tid) => timers.delete(tid),
    now: () => 0,
    onSegment: () => {},
    onError: (e) => errors.push(e),
  });
  seg.start();
  const t = timers.values().next().value;
  timers.clear();
  t.fn(); // timer: rec0.stop()
  rec0.ondataavailable && rec0.ondataavailable({ data: new Blob([new Uint8Array(1)]) });
  rec0.onstop(); // rolling -> tries to start the next recorder -> throws
  assertEquals(seg.state(), "stopped");
  // "via onError" must be TRUE, not just claimed: the factory error surfaced.
  assertEquals(errors.length, 1, "onError received the factory failure");
  assertEquals(String(errors[0].message), "stream ended");
  await seg.stop(); // already finished — resolves immediately, no hang
});

Deno.test("recorder onerror events surface through onError while the loop lives on", () => {
  const h = harness();
  h.clock.set(0);
  h.seg.start();
  h.recs[0].onerror({ error: new Error("encoder hiccup") });
  assertEquals(h.errors.length, 1, "recorder error surfaced");
  assertEquals(String(h.errors[0].message), "encoder hiccup");
  // The recorder goes inactive on error and its onstop still fires — the loop
  // continues exactly like a normal rollover.
  h.recs[0].stop();
  h.recs[0].onstop();
  assertEquals(h.recs.length, 2, "loop continued after the recorder error");
  assertEquals(h.seg.state(), "rolling");
});
