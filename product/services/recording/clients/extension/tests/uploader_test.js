/*
 * WS-E — uploader.js conformance (deno test; no network, no chrome.*).
 *
 * Everything the module promises is checked against fakes: serialized order
 * (one request in flight, seq n only after n-1 acked), retry-forever backoff
 * (1s*2^n cap 30s) on 5xx AND thrown network errors, 4xx = dropped+surfaced
 * with the queue continuing, the exact wire shape (query params, RFC3339
 * timestamps, octet-stream blob body), injected-digest behaviour incl. the
 * empty-on-failure contract, and end() ordering/retry semantics.
 *
 * Local assert helpers on purpose: no remote std imports, so the suite runs
 * with zero network access.
 */

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

// A macrotask tick flushes every pending microtask chain in the pump.
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

const ok200 = () => ({ ok: true, status: 200, text: async () => "" });
const status = (code, body = "") => ({ ok: false, status: code, text: async () => body });

const seg = (size = 4) => ({
  blob: new Blob([new Uint8Array(size)]),
  tStart: 1752800000000,
  tEnd: 1752800010000,
  mime: "video/webm",
});

// Fake fetch: records calls (with parsed URLs) and tracks concurrency.
function fakeFetch(handler) {
  const calls = [];
  let inFlight = 0;
  let maxInFlight = 0;
  const fn = async (url, opts) => {
    inFlight += 1;
    maxInFlight = Math.max(maxInFlight, inFlight);
    const call = { url: new URL(url), opts, n: calls.length };
    calls.push(call);
    try {
      return await handler(call);
    } finally {
      inFlight -= 1;
    }
  };
  fn.calls = calls;
  fn.maxInFlight = () => maxInFlight;
  return fn;
}

const mkUploader = (fetch, extra = {}) =>
  createUploader({
    baseUrl: "http://server:8084",
    sessionId: "S",
    userId: "u",
    deviceId: "ext-chrome-test",
    fetch,
    sleep: async () => {},
    digest: async () => "aa",
    ...extra,
  });

Deno.test("serialized pump: one request in flight, seq n only after n-1 acked", async () => {
  const pending = [];
  const fetch = fakeFetch(() => new Promise((resolve) => pending.push(resolve)));
  const up = mkUploader(fetch);
  up.enqueue(seg());
  up.enqueue(seg());
  up.enqueue(seg());
  await tick();
  // Only seq 0 has been attempted — 1 and 2 wait for their predecessor's ack.
  assertEquals(pending.length, 1);
  assertEquals(fetch.calls[0].url.searchParams.get("seq"), "0");
  assertEquals(up.state().queued, 3);
  pending[0](ok200());
  await tick();
  assertEquals(pending.length, 2);
  assertEquals(fetch.calls[1].url.searchParams.get("seq"), "1");
  pending[1](ok200());
  await tick();
  assertEquals(fetch.calls[2].url.searchParams.get("seq"), "2");
  pending[2](ok200());
  await up.drain();
  assertEquals(fetch.calls.length, 3);
  assertEquals(fetch.maxInFlight(), 1, "serialization is by construction");
  assertEquals(up.state().uploaded, 3);
  assertEquals(up.state().queued, 0);
});

Deno.test("5xx retries forever with 1s*2^n backoff capped at 30s", async () => {
  const delays = [];
  let failures = 7;
  const fetch = fakeFetch(() => (failures-- > 0 ? status(503, "unavailable") : ok200()));
  const up = mkUploader(fetch, {
    sleep: async (ms) => {
      delays.push(ms);
    },
  });
  up.enqueue(seg());
  await up.drain();
  assertEquals(delays, [1000, 2000, 4000, 8000, 16000, 30000, 30000]);
  assertEquals(fetch.calls.length, 8);
  const st = up.state();
  assertEquals(st.uploaded, 1);
  assertEquals(st.dropped, 0);
});

Deno.test("thrown network error retries like a 5xx and surfaces in lastError", async () => {
  const delays = [];
  let threw = false;
  const fetch = fakeFetch(() => {
    if (!threw) {
      threw = true;
      throw new TypeError("Failed to fetch");
    }
    return ok200();
  });
  const seenErrors = [];
  const up = mkUploader(fetch, {
    sleep: async (ms) => delays.push(ms),
    onState: (st) => {
      if (st.lastError) seenErrors.push(st.lastError);
    },
  });
  up.enqueue(seg());
  await up.drain();
  assertEquals(delays, [1000]);
  assertEquals(up.state().uploaded, 1);
  assert(seenErrors.some((e) => e.includes("network error")), "network error surfaced");
});

Deno.test("4xx: dropped + surfaced, never retried, queue continues", async () => {
  const fetch = fakeFetch((call) =>
    call.url.searchParams.get("seq") === "0" ? status(400, "bad t_start") : ok200()
  );
  const up = mkUploader(fetch);
  up.enqueue(seg());
  await up.drain();
  let st = up.state();
  assertEquals(st.dropped, 1);
  assertEquals(st.uploaded, 0);
  assertEquals(fetch.calls.length, 1, "a 4xx is never retried");
  assert(st.lastError.includes("400"), "status surfaced");
  assert(st.lastError.includes("bad t_start"), "body detail surfaced");
  // The queue keeps moving past the drop: the next segment still uploads.
  up.enqueue(seg());
  await up.drain();
  st = up.state();
  assertEquals(st.uploaded, 1);
  assertEquals(st.dropped, 1);
  assertEquals(fetch.calls[1].url.searchParams.get("seq"), "1", "seq stays dense after a drop");
});

Deno.test("wire shape: path, query params, RFC3339 stamps, octet-stream blob body", async () => {
  const fetch = fakeFetch(() => ok200());
  const up = createUploader({
    baseUrl: "http://server:8084/", // trailing slash must be normalized away
    sessionId: "SESH",
    userId: "u1",
    deviceId: "ext-chrome-abc",
    fetch,
    sleep: async () => {},
    digest: async () => "cafe01",
  });
  up.enqueue({
    blob: new Blob([new Uint8Array(3)]),
    tStart: 1752800000000,
    tEnd: 1752800010000,
    mime: "video/webm;codecs=vp9",
  });
  await up.drain();
  const call = fetch.calls[0];
  assertEquals(call.url.pathname, "/capture/segments");
  const sp = call.url.searchParams;
  assertEquals(sp.get("session_id"), "SESH");
  assertEquals(sp.get("seq"), "0");
  assertEquals(sp.get("user_id"), "u1");
  assertEquals(sp.get("device_id"), "ext-chrome-abc");
  assertEquals(sp.get("t_start"), new Date(1752800000000).toISOString());
  assertEquals(sp.get("t_end"), new Date(1752800010000).toISOString());
  assertEquals(sp.get("mime"), "video/webm;codecs=vp9");
  assertEquals(sp.get("sha256"), "cafe01", "injected digest lands in the query");
  assertEquals(call.opts.method, "POST");
  assertEquals(call.opts.headers["content-type"], "application/octet-stream");
  assertEquals(call.opts.body.size, 3, "raw blob is the body");
});

Deno.test("digest failure degrades to sha256= empty (server computes)", async () => {
  const fetch = fakeFetch(() => ok200());
  const up = mkUploader(fetch, {
    digest: async () => {
      throw new Error("no crypto.subtle here");
    },
  });
  up.enqueue(seg());
  await up.drain();
  assertEquals(fetch.calls[0].url.searchParams.get("sha256"), "");
  assertEquals(up.state().uploaded, 1);
});

Deno.test("end(): drains first, then posts {last_seq}; endedOk set", async () => {
  const fetch = fakeFetch(() => ok200());
  const up = mkUploader(fetch);
  up.enqueue(seg());
  up.enqueue(seg());
  await up.end();
  assertEquals(fetch.calls.length, 3, "both segments before the end marker");
  const endCall = fetch.calls[2];
  assertEquals(endCall.url.pathname, "/capture/sessions/S/end");
  assertEquals(endCall.opts.method, "POST");
  assertEquals(endCall.opts.headers["content-type"], "application/json");
  assertEquals(JSON.parse(endCall.opts.body), { last_seq: 1 });
  assert(up.state().endedOk, "endedOk after a 2xx end marker");
});

Deno.test("end(): 5xx retried with backoff; 4xx logged, not retried", async () => {
  // 5xx then success
  const delays = [];
  let first = true;
  let fetch = fakeFetch((call) => {
    if (call.url.pathname.endsWith("/end") && first) {
      first = false;
      return status(503);
    }
    return ok200();
  });
  let up = mkUploader(fetch, { sleep: async (ms) => delays.push(ms) });
  up.enqueue(seg());
  await up.end();
  assertEquals(delays, [1000]);
  assert(up.state().endedOk);

  // 4xx: exactly one attempt, endedOk stays false, error surfaced
  fetch = fakeFetch((call) => (call.url.pathname.endsWith("/end") ? status(404) : ok200()));
  up = mkUploader(fetch);
  up.enqueue(seg());
  await up.end();
  const endCalls = fetch.calls.filter((c) => c.url.pathname.endsWith("/end"));
  assertEquals(endCalls.length, 1, "4xx end marker not retried");
  assertEquals(up.state().endedOk, false);
  assert(up.state().lastError.includes("404"));
});

Deno.test("end() with zero captured segments: no wire call, vacuously ended", async () => {
  const fetch = fakeFetch(() => ok200());
  const up = mkUploader(fetch);
  await up.end();
  assertEquals(fetch.calls.length, 0, "no session row exists server-side — nothing to end");
  assert(up.state().endedOk);
});

Deno.test("state() counters and onState notifications track the queue", async () => {
  const pending = [];
  const fetch = fakeFetch(() => new Promise((resolve) => pending.push(resolve)));
  const snapshots = [];
  const up = mkUploader(fetch, { onState: (st) => snapshots.push(st) });
  assertEquals(up.state(), {
    captured: 0,
    uploaded: 0,
    queued: 0,
    dropped: 0,
    lastError: "",
    endedOk: false,
  });
  up.enqueue(seg());
  up.enqueue(seg());
  await tick();
  assertEquals(up.state().captured, 2);
  assertEquals(up.state().queued, 2);
  assertEquals(up.state().uploaded, 0);
  pending[0](ok200());
  await tick();
  assertEquals(up.state().uploaded, 1);
  assertEquals(up.state().queued, 1);
  pending[1](ok200());
  await up.drain();
  assertEquals(up.state().uploaded, 2);
  assertEquals(up.state().queued, 0);
  assert(snapshots.length >= 4, "onState fired for enqueues and acks");
});
