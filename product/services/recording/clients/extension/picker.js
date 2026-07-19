// @ts-nocheck
/* global chrome */
/*
 * WS-E — screen-share picker (runs inside picker.html, an extension page).
 *
 * Why this page exists (alpha finding, 2026-07-19, real Chrome):
 * chrome.desktopCapture.chooseDesktopMedia REFUSES a service-worker caller
 * with no targetTab — "A target tab is required when called from a service
 * worker context" — and binding a targetTab would make the returned stream
 * consumable only by frames of THAT tab's origin, never by our offscreen
 * capture document. An extension PAGE caller has neither restriction: no
 * targetTab needed, and the stream id is consumable extension-wide. So the
 * worker opens this page in a tiny popup window; it runs the native share
 * dialog, posts the result back ({target:"background", type:"picker-result"}),
 * and closes itself. Every ending sends exactly one result: chosen -> streamId,
 * Chrome-dialog Cancel -> cancelled, API failure -> error. The worker treats
 * the user closing THIS window (windows.onRemoved) as cancelled.
 *
 * Passive posture unchanged (D-E1): this page touches no web content — it is
 * extension chrome, shown only for the duration of the share dialog.
 */

let sent = false;
function send(result) {
  if (sent) return; // exactly one result per picker window
  sent = true;
  // The result carries THIS window's id so the worker can drop a superseded
  // picker's late result instead of letting it consume a newer pending start.
  const deliver = (windowId) => {
    try {
      chrome.runtime
        .sendMessage(
          Object.assign({ target: "background", type: "picker-result", windowId }, result),
        )
        .catch(() => {})
        .finally(() => window.close());
    } catch {
      window.close(); // worker gone — closing still resolves via onRemoved
    }
  };
  try {
    chrome.windows.getCurrent().then(
      (win) => deliver(win && win.id != null ? win.id : null),
      () => deliver(null),
    );
  } catch {
    deliver(null);
  }
}

try {
  chrome.desktopCapture.chooseDesktopMedia(["screen", "window", "tab"], (streamId) => {
    if (chrome.runtime.lastError) send({ error: chrome.runtime.lastError.message });
    else if (!streamId) send({ cancelled: true }); // user hit Cancel in the dialog
    else send({ streamId });
  });
} catch (err) {
  send({ error: String((err && err.message) || err) });
}
