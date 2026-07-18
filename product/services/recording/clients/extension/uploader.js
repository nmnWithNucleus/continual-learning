/*
 * WS-E — serialized segment uploader (shared ES module; the extension flavour
 * of the WS-B phone-client queue — same semantics, adapted to absolute server
 * URLs and full dependency injection so it runs under `deno test`, no browser).
 *
 * Design intent (spec: handoff/ws-e-extension.md D-E4; wire: ws-b §Wire / ws-c):
 *
 * - ONE serialized queue per ingest session: `seq` is assigned densely at
 *   enqueue, and segment n is sent only after n-1 is acked — in-order arrival
 *   by construction (the server ledger still catches anomalies).
 * - Network errors / 5xx retry forever with exponential backoff (1s * 2^n,
 *   cap 30s). A 4xx is a client bug — surfaced via state() and DROPPED so the
 *   queue (and the end marker) keeps moving; the server's client-leg report
 *   then names exactly the dropped seq as missing. Never silent.
 * - sha256 via an injectable digest (default: crypto.subtle over the blob's
 *   bytes; empty string on ANY failure — the server computes it then).
 * - end() waits for the queue to drain, then POSTs {last_seq} with the same
 *   retry semantics (4xx logged, not retried — the session reads as
 *   unterminated in the ledger, which is the honest outcome).
 * - The queue is in-memory: if the offscreen document dies, queued segments
 *   are lost and the ledger flags exactly which (unterminated / missing tail).
 *
 * Wire (absolute, cross-origin — the extension's runtime host grant bypasses
 * CORS, so the server needs no CORS middleware; D-E1):
 *   POST {baseUrl}/capture/segments?session_id=&seq=&user_id=&device_id=&
 *        t_start=&t_end=&mime=&sha256=       raw blob body, octet-stream
 *   POST {baseUrl}/capture/sessions/{id}/end  JSON {last_seq}
 */

const BACKOFF_BASE_MS = 1000;
const BACKOFF_CAP_MS = 30000;

const backoffDelay = (attempt) =>
  Math.min(BACKOFF_BASE_MS * 2 ** attempt, BACKOFF_CAP_MS);

const errText = (err) => String((err && err.message) || err);

// crypto.subtle needs a secure context; extension pages have one, but any
// failure degrades to "" and the server computes the digest itself (spec).
async function defaultDigest(blob) {
  try {
    const subtle = globalThis.crypto && globalThis.crypto.subtle;
    if (!subtle || !subtle.digest) return "";
    const digest = await subtle.digest("SHA-256", await blob.arrayBuffer());
    return Array.from(new Uint8Array(digest))
      .map((b) => b.toString(16).padStart(2, "0"))
      .join("");
  } catch {
    return "";
  }
}

export function createUploader(opts) {
  const baseUrl = String(opts.baseUrl || "").replace(/\/+$/, "");
  const { sessionId, userId, deviceId } = opts;
  const fetchFn = opts.fetch || ((...args) => globalThis.fetch(...args));
  const sleepFn = opts.sleep || ((ms) => new Promise((r) => setTimeout(r, ms)));
  const digestFn = opts.digest || defaultDigest;
  const onState = opts.onState || (() => {});
  // `now` is accepted for DI symmetry with the segmenter; wire timestamps come
  // from the segments themselves (stamped at recorder start/stop, D-M1-1).
  void opts.now;

  const queue = []; // FIFO of {seq, blob, tStart, tEnd, mime, sha256?}
  let nextSeq = 0; // dense, zero-based — assigned at enqueue, never reused
  let uploaded = 0;
  let dropped = 0;
  let lastError = "";
  let endedOk = false;
  let pumping = false;
  let drainWaiters = [];

  function state() {
    return {
      captured: nextSeq,
      uploaded,
      queued: queue.length,
      dropped,
      lastError,
      endedOk,
    };
  }

  function emitState() {
    try {
      onState(state());
    } catch {
      /* an observer must never break the pump */
    }
  }

  // Error carrier: fatal = 4xx (retrying cannot help), else transient.
  const wireError = (message, fatal) =>
    Object.assign(new Error(message), { fatal });

  async function uploadOnce(seg) {
    if (seg.sha256 === undefined) {
      // Cached across retries — the bytes don't change between attempts.
      try {
        seg.sha256 = await digestFn(seg.blob);
      } catch {
        seg.sha256 = ""; // server computes (spec)
      }
    }
    const qs = new URLSearchParams({
      session_id: sessionId,
      seq: String(seg.seq),
      user_id: userId,
      device_id: deviceId,
      t_start: new Date(seg.tStart).toISOString(), // RFC3339 UTC, ms precision
      t_end: new Date(seg.tEnd).toISOString(),
      mime: seg.mime,
      sha256: seg.sha256,
    });
    let res;
    try {
      res = await fetchFn(baseUrl + "/capture/segments?" + qs.toString(), {
        method: "POST",
        headers: { "content-type": "application/octet-stream" },
        body: seg.blob,
      });
    } catch (err) {
      throw wireError("network error (" + errText(err) + ")", false);
    }
    if (res.status >= 500) throw wireError("server error HTTP " + res.status, false);
    if (!res.ok) {
      let detail = "";
      try {
        detail = String(await res.text()).slice(0, 160);
      } catch {
        /* body optional */
      }
      throw wireError("HTTP " + res.status + (detail ? " — " + detail : ""), true);
    }
    // Ack body is {ok, session_id, seq, status:"received"|"duplicate"}; both
    // statuses mean the bytes are durably on the server — nothing to branch on.
  }

  async function pump() {
    if (pumping) return;
    pumping = true;
    try {
      while (queue.length > 0) {
        const seg = queue[0];
        for (let attempt = 0; ; attempt++) {
          try {
            await uploadOnce(seg);
            uploaded += 1;
            lastError = "";
            break;
          } catch (err) {
            if (err.fatal) {
              // 4xx: drop so the queue keeps moving; the ledger's client leg
              // will show exactly this seq as missing (checked loss, D-E4).
              dropped += 1;
              lastError =
                "seq " + seg.seq + ": " + err.message + " (4xx — dropped, not retried)";
              break;
            }
            const delay = backoffDelay(attempt);
            lastError =
              "seq " + seg.seq + ": " + err.message +
              " — retrying in " + Math.round(delay / 1000) + "s";
            emitState();
            await sleepFn(delay);
          }
        }
        queue.shift();
        emitState();
      }
    } finally {
      pumping = false;
    }
    const waiters = drainWaiters;
    drainWaiters = [];
    for (const resolve of waiters) resolve();
  }

  function enqueue({ blob, tStart, tEnd, mime }) {
    const seg = { seq: nextSeq++, blob, tStart, tEnd, mime };
    queue.push(seg);
    emitState();
    pump();
    return seg.seq;
  }

  function drain() {
    if (queue.length === 0 && !pumping) return Promise.resolve();
    return new Promise((resolve) => drainWaiters.push(resolve));
  }

  async function end() {
    await drain();
    if (nextSeq === 0) {
      // No segment ever reached the wire, so the server holds no session row
      // to terminate (the POST would 404). Vacuously ended.
      endedOk = true;
      emitState();
      return;
    }
    const body = JSON.stringify({ last_seq: nextSeq - 1 });
    for (let attempt = 0; ; attempt++) {
      let res;
      try {
        res = await fetchFn(
          baseUrl + "/capture/sessions/" + encodeURIComponent(sessionId) + "/end",
          {
            method: "POST",
            headers: { "content-type": "application/json" },
            body,
          },
        );
      } catch (err) {
        lastError = "end marker: network error (" + errText(err) + ") — retrying";
        emitState();
        await sleepFn(backoffDelay(attempt));
        continue;
      }
      if (res.ok) {
        endedOk = true;
        lastError = "";
        emitState();
        return;
      }
      if (res.status < 500) {
        // A 4xx end marker is a bug: logged, never retried; the session stays
        // unterminated in the ledger — visible, not silent.
        lastError = "end marker: HTTP " + res.status + " (4xx — not retried)";
        emitState();
        return;
      }
      lastError = "end marker: server error HTTP " + res.status + " — retrying";
      emitState();
      await sleepFn(backoffDelay(attempt));
    }
  }

  return { enqueue, drain, end, state };
}
