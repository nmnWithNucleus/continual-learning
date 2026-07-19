// @ts-nocheck
/* global chrome */
/*
 * WS-E — Nucleus Capture service worker: ORCHESTRATION ONLY (D-E5).
 *
 * PASSIVE POSTURE (D-E1) — stated here because manifest.json cannot carry
 * comments: this extension has NO content scripts, never calls
 * chrome.scripting, and never reads or touches the DOM of any page. It is a
 * capture surface, not an agent: permissions are exactly tabCapture,
 * desktopCapture, offscreen, storage — no "tabs", no "activeTab", no static
 * host_permissions. The user-configured server origin is granted at runtime
 * via optional_host_permissions (chrome.permissions.request from the popup's
 * Save/Record gesture); an extension context holding that grant bypasses
 * CORS, so the recording server needs no CORS middleware.
 *
 * What lives here (and only this):
 * - start: read config from the popup's message, ensure the offscreen
 *   document exists, acquire capture stream IDs (D-E2), hand both to the
 *   offscreen capture engine, reply with the per-source outcome.
 * - stop: forward to offscreen (which stops the capture loops and replies
 *   while uploads keep draining); when offscreen later reports "drained"
 *   (all end markers posted + report polls terminal), close its document.
 * - device-id: single home of the stable ext-chrome-<suffix> identity
 *   (chrome.storage.local; popup and offscreen both receive it from here).
 *
 * Stream acquisition (D-E2 — stream-ID handoff, not getDisplayMedia):
 * - Screen video: real Chrome REFUSES chooseDesktopMedia from this worker
 *   ("A target tab is required when called from a service worker context" —
 *   alpha finding 2026-07-19), and a targetTab would bind the stream to that
 *   tab's origin, away from our offscreen document. So the worker opens
 *   picker.html in a tiny popup window; that extension PAGE runs the native
 *   share dialog and posts the stream id back (see picker.js). Closing the
 *   picker window without choosing = cancelled.
 * - THE START IS A CONTINUATION, NOT A CALL STACK (skeptic round on the
 *   picker fix): the share dialog is human-paced and this worker is killed
 *   after ~30 s idle — any state held only in worker memory (and any
 *   in-flight await) dies with it, silently dropping the user's pick. So the
 *   pending start (config, tab id, picker window id) is persisted in
 *   chrome.storage.session (survives worker restarts, not browser restarts),
 *   and the picker-result message — which WAKES a fresh worker — resumes the
 *   start from that state. A new start supersedes a stale pending one (its
 *   picker window is closed).
 * - Tab audio: chrome.tabCapture.getMediaStreamId with an EXPLICIT
 *   targetTabId, pinned by the popup at Record time (tab ids need no "tabs"
 *   permission; only url/title are gated). Omitting it resolved "the active
 *   tab of the last-active window" — which, minted right after the picker
 *   result, was the PICKER window itself (skeptic round). The popup-open
 *   invocation is tabCapture's gate and persists for that tab.
 * - ORDER MATTERS: the tab-audio id is minted AFTER the picker resolves —
 *   unused stream ids expire in ~10 s and the picker is unbounded.
 * - Acquisition failures ride into the offscreen document with the start
 *   message (acquireErrors) so they surface in the status snapshot even when
 *   the popup died with the picker (a lone worker reply reaches no one).
 * - An empty/undefined stream id = user cancelled the picker: that source is
 *   skipped and reported; if no source survives, start fails honestly.
 */

const OFFSCREEN_URL = "offscreen.html";
const DEVICE_KEY = "nucleus.ext.device_suffix";

// Crockford base32 (same idiom as clients/web/app.js) for the device suffix.
const CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
function randomChars(length) {
  const bytes = new Uint8Array(length);
  crypto.getRandomValues(bytes);
  let out = "";
  for (let i = 0; i < length; i++) out += CROCKFORD[bytes[i] & 31];
  return out;
}

const errText = (err) => String((err && err.message) || err);

// ------------------------------------------------------------- device identity
// Single home of device_id (get-or-create, latched so concurrent messages
// can't mint two suffixes). Stable per browser profile.
let deviceIdPromise = null;
function getDeviceId() {
  if (!deviceIdPromise) {
    deviceIdPromise = (async () => {
      const stored = await chrome.storage.local.get(DEVICE_KEY);
      let suffix = stored[DEVICE_KEY];
      if (!suffix) {
        suffix = randomChars(8);
        await chrome.storage.local.set({ [DEVICE_KEY]: suffix });
      }
      return "ext-chrome-" + suffix;
    })();
    deviceIdPromise.catch(() => {
      deviceIdPromise = null; // storage hiccup: retry on the next ask
    });
  }
  return deviceIdPromise;
}

// --------------------------------------------------------- offscreen lifecycle

async function hasOffscreen() {
  const contexts = await chrome.runtime.getContexts({
    contextTypes: ["OFFSCREEN_DOCUMENT"],
  });
  return contexts.length > 0;
}

let creatingOffscreen = null; // guard: createDocument throws if one exists
async function ensureOffscreen() {
  if (await hasOffscreen()) return;
  if (!creatingOffscreen) {
    creatingOffscreen = chrome.offscreen
      .createDocument({
        url: OFFSCREEN_URL,
        reasons: ["USER_MEDIA"],
        justification:
          "Hold getUserMedia capture streams (screen video / tab audio) and " +
          "upload recorded segments to the user-configured Nucleus recording server.",
      })
      .finally(() => {
        creatingOffscreen = null;
      });
  }
  await creatingOffscreen;
}

async function closeOffscreen() {
  try {
    await chrome.offscreen.closeDocument();
  } catch {
    /* already closed / never existed — fine either way */
  }
}

// ------------------------------------------------------- stream ID acquisition

// The pending start, persisted so it survives worker suspension while the
// user deliberates in the share dialog. Exactly one pending start at a time;
// a new start supersedes it (closing its picker window).
const PENDING_KEY = "nucleus.ext.pendingStart";

async function getPending() {
  try {
    const stored = await chrome.storage.session.get(PENDING_KEY);
    return stored[PENDING_KEY] || null;
  } catch {
    return null;
  }
}
async function setPending(pending) {
  try {
    await chrome.storage.session.set({ [PENDING_KEY]: pending });
  } catch {
    /* storage.session unavailable would surface as a dropped continuation;
       nothing better to do than proceed and hope the worker lives */
  }
}
async function clearPending() {
  try {
    await chrome.storage.session.remove(PENDING_KEY);
  } catch {
    /* see setPending */
  }
}

// EVERY pending-state transition (supersede, picker-result take, window-close
// take, drained-close check) runs through this lock. The takes are multi-step
// (read -> verify -> clear -> continue) over async storage, and the round-2
// skeptic pass showed every unserialized interleaving loses a start or records
// under a stale config — a promise-chain mutex removes the interleavings
// wholesale. Cross-life ordering needs no lock: one worker instance at a time,
// events queue into it.
let pendingChain = Promise.resolve();
function withPendingLock(fn) {
  const run = pendingChain.then(() => fn());
  pendingChain = run.then(
    () => {},
    () => {},
  );
  return run;
}

function acquireTabStreamId(targetTabId) {
  return new Promise((resolve) => {
    // Explicit target (pinned at Record time by the popup). Fallback to
    // active-tab semantics only if the popup could not resolve a tab id.
    const opts = targetTabId != null ? { targetTabId } : {};
    try {
      chrome.tabCapture.getMediaStreamId(opts, (streamId) => {
        if (chrome.runtime.lastError) {
          resolve({ error: chrome.runtime.lastError.message });
        } else if (!streamId) {
          resolve({ error: "no tab stream id returned" });
        } else {
          resolve({ streamId });
        }
      });
    } catch (err) {
      resolve({ error: errText(err) });
    }
  });
}

chrome.windows.onRemoved.addListener((windowId) => {
  // Picker window gone without a result message = the user dismissed it.
  // (picker.js's normal path sends the result FIRST; that locked take clears
  // the pending state, so this locked take then finds nothing and no-ops.
  // A supersede also clears pending BEFORE removing the stale window, so the
  // stale window's close lands here as a no-op too.)
  withPendingLock(async () => {
    const pending = await getPending();
    if (!pending || pending.pickerWindowId !== windowId) return;
    await clearPending();
    await finishStart(pending, { cancelled: true });
  }).catch((err) => console.warn("picker-cancel continuation failed:", err));
});

// ------------------------------------------------------------------ start/stop

// A "drained" message arriving while a new start is between its picker and its
// offscreen handoff must NOT close the document out from under it.
let startsInFlight = 0;

async function handleStart(msg) {
  startsInFlight += 1;
  try {
    return await doStart(msg);
  } finally {
    startsInFlight -= 1;
  }
}

async function doStart(msg) {
  const cfg = (msg && msg.config) || {};
  const want = cfg.sources || {};
  const deviceId = await getDeviceId();

  const pending = {
    config: { baseUrl: cfg.baseUrl, userId: cfg.userId, deviceId },
    // Pinned by the popup at Record time — see the header's tab-audio note.
    tabId: cfg.tabId != null ? cfg.tabId : null,
    wantTabAudio: !!want.tabAudio,
    pickerWindowId: null,
  };

  // Offscreen document FIRST: it must exist by the time stream ids are minted
  // (they expire unused in ~10 s), and pre-creating it here keeps the
  // continuation path short.
  await ensureOffscreen();

  if (!want.screen) {
    // No picker, no suspension window: finish synchronously.
    return finishStart(pending, { skipped: true });
  }

  // Supersede any stale pending start (e.g. a picker window orphaned by a
  // worker restart). ORDER MATTERS, and the whole transition is locked:
  // pending is cleared BEFORE the stale window is removed, so the window's
  // onRemoved take finds nothing (round-2 skeptic pass: remove-then-clear let
  // that take run a phantom cancel-continuation under the stale config).
  const staleWindowId = await withPendingLock(async () => {
    const stale = await getPending();
    await clearPending();
    return stale && stale.pickerWindowId != null ? stale.pickerWindowId : null;
  });
  if (staleWindowId != null) {
    try {
      await chrome.windows.remove(staleWindowId);
    } catch {
      /* already gone */
    }
  }

  let win;
  try {
    win = await chrome.windows.create({
      url: "picker.html",
      type: "popup",
      width: 420,
      height: 140,
      focused: true,
    });
  } catch (err) {
    return finishStart(pending, { error: errText(err) });
  }
  pending.pickerWindowId = (win && win.id) != null ? win.id : null;
  await withPendingLock(() => setPending(pending));
  // Reply NOW and let the message channel go: the popup dies when the picker
  // takes focus, and this worker may be suspended while the user deliberates.
  // The start resumes from storage.session on picker-result / window-close.
  return {
    ok: true,
    pendingPicker: true,
    sources: { screen: "choose what to share in the picker window" },
  };
}

// The second half of a start: runs after screen acquisition settles (or was
// skipped), possibly in a DIFFERENT worker life than doStart — everything it
// needs must be in `pending`, nothing in closures.
async function finishStart(pending, screenResult) {
  const outcome = {};
  const acquireErrors = {};
  let screenStreamId = null;
  if (screenResult && screenResult.streamId) {
    screenStreamId = screenResult.streamId;
  } else if (screenResult && screenResult.cancelled) {
    outcome.screen = "cancelled";
    acquireErrors.screen = "cancelled in the picker";
  } else if (screenResult && screenResult.error) {
    outcome.screen = "error: " + screenResult.error;
    acquireErrors.screen = screenResult.error;
  }
  // screenResult.skipped: source not requested — no outcome entry.

  // Tab id minted LAST (it expires fast and the picker was unbounded).
  let tabStreamId = null;
  if (pending.wantTabAudio) {
    const r = await acquireTabStreamId(pending.tabId);
    if (r.streamId) tabStreamId = r.streamId;
    else {
      outcome.tabAudio = "error: " + r.error;
      acquireErrors.tabAudio = r.error;
    }
  }

  // The picker may have taken minutes; a drained-close or worker restart may
  // have removed the document — re-ensure before handoff. Even with ZERO
  // surviving sources the start message is sent: the offscreen document is
  // the only surface that outlives the popup, so it must carry the
  // acquisition errors for the status pull to render.
  await ensureOffscreen();
  let reply;
  try {
    reply = await chrome.runtime.sendMessage({
      target: "offscreen",
      type: "start",
      screenStreamId,
      tabStreamId,
      config: pending.config,
      acquireErrors,
    });
  } catch (err) {
    return { ok: false, sources: outcome, error: "offscreen start failed: " + errText(err) };
  }
  if (!screenStreamId && !tabStreamId) {
    return {
      ok: false,
      sources: outcome,
      error: "no capture source available (cancelled or failed)",
    };
  }
  const off = (reply && reply.sources) || {};
  if (screenStreamId) outcome.screen = off.screen || "error: no offscreen outcome";
  if (tabStreamId) outcome.tabAudio = off.tabAudio || "error: no offscreen outcome";
  const ok = !!(reply && reply.ok);
  return ok
    ? { ok: true, sources: outcome }
    : { ok: false, sources: outcome, error: (reply && reply.error) || "no source started" };
}

async function handleStop() {
  if (!(await hasOffscreen())) return { ok: true, idle: true };
  try {
    // Offscreen replies once its capture loops have stopped; the upload
    // drain continues there in the background, ending with a "drained"
    // message that closes the document (below).
    const reply = await chrome.runtime.sendMessage({ target: "offscreen", type: "stop" });
    return reply || { ok: true };
  } catch (err) {
    return { ok: false, error: "offscreen stop failed: " + errText(err) };
  }
}

// -------------------------------------------------------------- message router
// Every runtime message carries {target, type}; multiple contexts share the
// bus, so each listener ignores non-matching targets (D-E5).

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg || msg.target !== "background") return false;
  switch (msg.type) {
    case "start":
      handleStart(msg).then(sendResponse, (err) =>
        sendResponse({ ok: false, error: errText(err) })
      );
      return true; // async sendResponse
    case "stop":
      handleStop().then(sendResponse, (err) =>
        sendResponse({ ok: false, error: errText(err) })
      );
      return true;
    case "device-id":
      getDeviceId().then(
        (deviceId) => sendResponse({ ok: true, deviceId }),
        (err) => sendResponse({ ok: false, error: errText(err) }),
      );
      return true;
    case "picker-result":
      // From picker.js (extension page): the share dialog's outcome. This
      // message may be what WOKE this worker — the pending start lives in
      // storage.session, not in memory (see the header's continuation note).
      sendResponse({ ok: true });
      withPendingLock(async () => {
        const pending = await getPending();
        if (!pending) return; // superseded / already continued — drop it
        // Correlate: a superseded picker's late result must not consume the
        // NEW pending (the result carries its own window id for this).
        if (
          msg.windowId != null &&
          pending.pickerWindowId != null &&
          msg.windowId !== pending.pickerWindowId
        ) {
          return;
        }
        await clearPending();
        await finishStart(
          pending,
          msg.streamId
            ? { streamId: msg.streamId }
            : msg.cancelled
              ? { cancelled: true }
              : { error: msg.error || "picker failed" },
        );
      }).catch((err) => console.warn("picker-result continuation failed:", err));
      return false;
    case "abandon":
      // Popup's Discard: a drain that can never finish (e.g. wrong server URL)
      // must not hold the extension hostage. Killing the capture document drops
      // unsent segments (its pagehide still best-effort-beacons end markers);
      // whatever already reached the server stays in the ledger.
      sendResponse({ ok: true });
      closeOffscreen();
      return false;
    case "drained":
      // Offscreen: all sessions posted end markers, report polls terminal.
      // Close the document ONLY when nothing needs it: no start mid-flight,
      // no picker pending (its continuation will hand off to this doc), and —
      // authoritatively, asked of the doc itself — no source active or still
      // draining. The status probe closes the round-2 race where a stale
      // session's drained landed just as a NEW capture began in the same doc.
      sendResponse({ ok: true });
      withPendingLock(async () => {
        if (startsInFlight > 0) return;
        if (await getPending()) return;
        let st = null;
        try {
          st = await chrome.runtime.sendMessage({ target: "offscreen", type: "status" });
        } catch {
          return; // no receiver: the document is already gone
        }
        const srcs = (st && st.sources) || {};
        const busy =
          st && st.ok &&
          (st.active ||
            ["screen", "tabAudio"].some((k) => srcs[k] && !srcs[k].ended));
        if (!busy) closeOffscreen();
      }).catch((err) => console.warn("drained-close check failed:", err));
      return false;
    default:
      sendResponse({ ok: false, error: "unknown message type: " + msg.type });
      return false;
  }
});
