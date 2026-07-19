// @ts-nocheck
/* global chrome */
/*
 * WS-E — Nucleus Capture service worker: ORCHESTRATION ONLY (D-E5).
 *
 * PASSIVE POSTURE (D-E1) — stated here because manifest.json cannot carry
 * comments: this extension has NO content scripts, never calls
 * chrome.scripting, and never reads or touches the DOM of any page. It is a
 * capture surface, not an agent: permissions are exactly tabCapture, offscreen,
 * storage. The user-configured server origin is granted at runtime via
 * optional_host_permissions (chrome.permissions.request from the popup's
 * Save/Record gesture); an extension context holding that grant bypasses CORS,
 * so the recording server needs no CORS middleware.
 *
 * CAPTURE MODEL (D-E7 — direct tab capture, replaced the desktop-picker path
 * 2026-07-19 after it proved fragile on real browsers): the extension records
 * the ACTIVE TAB — video + audio in ONE muxed stream via
 * chrome.tabCapture.getMediaStreamId({targetTabId}). No screen picker, no
 * chooseDesktopMedia, no cross-context stream-id handoff. One tab = one capture
 * = one ingest session; the server demuxes each muxed segment into audio + video
 * C1 streams exactly like the phone/mac clients. Full-screen / other-app capture
 * is the mac CLI's job (clients/mac), which does it reliably.
 *
 * What lives here (and only this):
 * - start: read config from the popup (baseUrl, userId, tabId, video flag),
 *   ensure the offscreen document exists, mint the tab-capture stream id for the
 *   popup-pinned tab, hand it to the offscreen capture engine, reply.
 * - stop: forward to offscreen (which stops capture and replies while uploads
 *   drain); "drained" later closes the document.
 * - abandon: the popup's Discard — kill the capture document for a drain that
 *   can never finish (e.g. a wrong server URL).
 * - device-id: single home of the stable ext-chrome-<suffix> identity.
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
          "Hold the getUserMedia tab-capture stream (video + audio) and upload " +
          "recorded segments to the user-configured Nucleus recording server.",
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

// ----------------------------------------------------- tab stream-id acquisition
// Explicit targetTabId, pinned by the popup at Record time: opening the action
// popup is the tabCapture invocation on that tab, and we mint immediately (no
// human-paced picker to age the invocation). No "tabs" permission needed —
// the popup reads the active tab's id, which is not gated.
function getTabStreamId(tabId) {
  return new Promise((resolve) => {
    const opts = tabId != null ? { targetTabId: tabId } : {};
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

// ------------------------------------------------------------------ start/stop

// A "drained" message arriving while a new start is mid-flight must not close
// the document out from under it.
let startsInFlight = 0;

async function handleStart(msg) {
  startsInFlight += 1;
  try {
    const cfg = (msg && msg.config) || {};
    const deviceId = await getDeviceId();

    // Mint the stream id BEFORE creating the offscreen document: a common
    // failure (the active tab is a chrome:// / extension page, not capturable)
    // must not leave an empty offscreen document lingering (skeptic round).
    const r = await getTabStreamId(cfg.tabId);
    if (!r.streamId) {
      return {
        ok: false,
        error:
          "could not start tab capture: " + r.error +
          " — open the popup on an ordinary web page tab (not a chrome:// or " +
          "extension page) and press Record.",
      };
    }

    await ensureOffscreen();
    let reply;
    try {
      reply = await chrome.runtime.sendMessage({
        target: "offscreen",
        type: "start",
        tabStreamId: r.streamId,
        video: cfg.video !== false, // default: capture video + audio
        config: { baseUrl: cfg.baseUrl, userId: cfg.userId, deviceId },
      });
    } catch (err) {
      return { ok: false, error: "offscreen start failed: " + errText(err) };
    }
    return reply && reply.ok
      ? { ok: true, session: reply.session }
      : { ok: false, error: (reply && reply.error) || "capture did not start" };
  } finally {
    startsInFlight -= 1;
  }
}

async function handleStop() {
  if (!(await hasOffscreen())) return { ok: true, idle: true };
  try {
    // Offscreen replies once capture has stopped; the upload drain continues
    // there in the background, ending with a "drained" message that closes the
    // document (below).
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
    case "abandon":
      // Popup's Discard: a drain that can never finish (e.g. a wrong server URL,
      // uploads retrying forever) must not hold the extension hostage. Killing
      // the capture document drops unsent segments (its pagehide still
      // best-effort-beacons end markers); whatever already reached the server
      // stays in the ledger.
      sendResponse({ ok: true });
      closeOffscreen();
      return false;
    case "drained":
      // Offscreen: the session posted its end marker and the report poll is
      // terminal. Close the document unless a new start is mid-flight (it needs
      // the document; its own start resets the offscreen state) or the document
      // is still busy — asked authoritatively of the document itself.
      sendResponse({ ok: true });
      (async () => {
        if (startsInFlight > 0) return;
        let st = null;
        try {
          st = await chrome.runtime.sendMessage({ target: "offscreen", type: "status" });
        } catch {
          return; // no receiver: the document is already gone
        }
        // Re-check AFTER the async status round-trip: a new start could have
        // begun during it, and it needs the document (skeptic round).
        if (startsInFlight > 0) return;
        const s = st && st.ok ? st.session : null;
        const busy = s && (s.active || !s.ended);
        if (!busy) closeOffscreen();
      })().catch((err) => console.warn("drained-close check failed:", err));
      return false;
    default:
      sendResponse({ ok: false, error: "unknown message type: " + msg.type });
      return false;
  }
});
