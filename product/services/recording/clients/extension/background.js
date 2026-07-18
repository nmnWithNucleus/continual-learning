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
 * - Screen video: chrome.desktopCapture.chooseDesktopMedia(["screen",
 *   "window","tab"], cb) with no targetTab, so the returned stream id is
 *   consumable by extension-origin contexts — our offscreen document.
 * - Tab audio: chrome.tabCapture.getMediaStreamId with targetTabId OMITTED —
 *   the documented behaviour then captures the ACTIVE tab of the current
 *   window (the tab the popup sat over; opening the action popup = the
 *   extension was invoked on that tab, which is tabCapture's gate — the
 *   grant persists while the picker is up).
 * - ORDER MATTERS: the screen picker runs FIRST because it is human-paced
 *   (unbounded), and unused stream ids expire in ~10 s — a tab id minted
 *   before the picker was dead on arrival at the offscreen document whenever
 *   the user took their time choosing a screen (review round, WS-E worklog).
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

function acquireTabStreamId() {
  return new Promise((resolve) => {
    try {
      chrome.tabCapture.getMediaStreamId({}, (streamId) => {
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

function acquireScreenStreamId() {
  return new Promise((resolve) => {
    try {
      chrome.desktopCapture.chooseDesktopMedia(
        ["screen", "window", "tab"],
        (streamId) => {
          if (chrome.runtime.lastError) {
            resolve({ error: chrome.runtime.lastError.message });
          } else if (!streamId) {
            resolve({ cancelled: true }); // user dismissed Chrome's picker
          } else {
            resolve({ streamId });
          }
        },
      );
    } catch (err) {
      resolve({ error: errText(err) });
    }
  });
}

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

  // Offscreen document FIRST: acquired stream ids expire within seconds, so
  // the consumer must already exist when they are minted.
  await ensureOffscreen();

  const outcome = {};
  let tabStreamId = null;
  let screenStreamId = null;

  // Screen FIRST (human-paced picker), tab id SECOND (instant) — see the
  // acquisition-order note in the header. Both ids are then young when the
  // offscreen document consumes them.
  if (want.screen) {
    const r = await acquireScreenStreamId();
    if (r.streamId) screenStreamId = r.streamId;
    else if (r.cancelled) outcome.screen = "cancelled";
    else outcome.screen = "error: " + r.error;
  }
  if (want.tabAudio) {
    const r = await acquireTabStreamId();
    if (r.streamId) tabStreamId = r.streamId;
    else outcome.tabAudio = "error: " + r.error;
  }

  if (!tabStreamId && !screenStreamId) {
    return {
      ok: false,
      sources: outcome,
      error: "no capture source available (cancelled or failed)",
    };
  }

  // The picker may have taken minutes: a previous session's drained-close can
  // have removed the document since the check above — re-ensure before handoff.
  await ensureOffscreen();

  let reply;
  try {
    reply = await chrome.runtime.sendMessage({
      target: "offscreen",
      type: "start",
      screenStreamId,
      tabStreamId,
      config: { baseUrl: cfg.baseUrl, userId: cfg.userId, deviceId },
    });
  } catch (err) {
    return { ok: false, sources: outcome, error: "offscreen start failed: " + errText(err) };
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
    case "drained":
      // Offscreen: all sessions posted end markers, report polls terminal.
      // Its job is done — close the document (guarded; it may already be gone)
      // UNLESS a new start is mid-flight: the new capture needs the document,
      // and its own start handler resets the offscreen state.
      sendResponse({ ok: true });
      if (startsInFlight === 0) closeOffscreen();
      return false;
    default:
      sendResponse({ ok: false, error: "unknown message type: " + msg.type });
      return false;
  }
});
