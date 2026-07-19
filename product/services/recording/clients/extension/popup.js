// @ts-nocheck
/* global chrome */
/*
 * WS-E — Nucleus Capture popup: UI ONLY (D-E5). Record/stop, settings, and a
 * ~1s status pull from the offscreen capture engine — the single active-tab
 * session's counters, verdict badge, and report lines.
 *
 * - Settings (server URL, user id, video toggle) live in chrome.storage.local.
 *   Save is the USER GESTURE that runs chrome.permissions.request for exactly
 *   the configured origin (D-E1 — runtime grant, no static hosts); Save PERSISTS
 *   FIRST because the permission prompt can close the popup mid-call. Record
 *   re-requests the grant if missing (still a gesture).
 * - D-E7: the extension records the ACTIVE TAB (video + audio in one stream) via
 *   tabCapture — no screen picker. The popup pins the active tab's id at Record.
 * - The popup owns NO capture state: it sends {target:"background"} start/stop
 *   and pulls {target:"offscreen", type:"status"} snapshots. A no-receiver reply
 *   means the offscreen document doesn't exist -> render idle.
 * - device_id is minted and stored by background (single home); popup displays it.
 */
(() => {
  "use strict";

  const STATUS_POLL_MS = 1000;
  const SETTINGS_KEY = "nucleus.ext.settings";
  const DEFAULTS = {
    baseUrl: "http://localhost:8084",
    userId: "beta-user",
    video: true, // capture tab video + audio; unchecked -> audio only
  };

  const $ = (id) => document.getElementById(id);
  const el = {
    banner: $("error-banner"),
    statePill: $("state-pill"),
    serverUrl: $("server-url"),
    userId: $("user-id"),
    capVideo: $("cap-video"),
    saveBtn: $("save-btn"),
    saveNote: $("save-note"),
    recordBtn: $("record-btn"),
    stopBtn: $("stop-btn"),
    discardBtn: $("discard-btn"),
    deviceId: $("device-id"),
    sources: $("sources"),
  };

  const errText = (err) => String((err && err.message) || err);

  function showBanner(msg) {
    el.banner.textContent = msg;
    el.banner.hidden = !msg;
  }

  function note(msg) {
    el.saveNote.textContent = msg;
  }

  // ------------------------------------------------------------- settings
  function readForm() {
    const raw = el.serverUrl.value.trim() || DEFAULTS.baseUrl;
    let url;
    try {
      url = new URL(raw);
    } catch {
      return { error: "server URL is not a valid URL: " + raw };
    }
    if (url.protocol !== "http:" && url.protocol !== "https:") {
      return { error: "server URL must be http(s)" };
    }
    // Normalized to the ORIGIN — the server mounts at the root, and the host
    // permission grant is per-origin. The host-permission MATCH PATTERN drops
    // the port (Chrome match patterns ignore ports; "http://localhost:8084/*"
    // is invalid) — a host grant covers every port on that host, which is what
    // we want anyway.
    const hostPattern = url.protocol + "//" + url.hostname + "/*";
    const settings = {
      baseUrl: url.origin,
      userId: el.userId.value.trim() || DEFAULTS.userId,
      video: el.capVideo.checked,
    };
    return { settings, origin: url.origin, hostPattern };
  }

  function fillForm(settings) {
    el.serverUrl.value = settings.baseUrl;
    el.userId.value = settings.userId;
    el.capVideo.checked = settings.video !== false;
  }

  async function loadSettings() {
    try {
      const stored = await chrome.storage.local.get(SETTINGS_KEY);
      return Object.assign({}, DEFAULTS, stored[SETTINGS_KEY] || {});
    } catch {
      return Object.assign({}, DEFAULTS);
    }
  }

  async function saveSettings() {
    showBanner("");
    const { settings, origin, hostPattern, error } = readForm();
    if (error) return note(error);
    fillForm(settings); // reflect normalization (origin) back into the input
    // PERSIST FIRST. The permission prompt can steal focus and CLOSE this popup,
    // killing everything after that await — with the write second, Save appeared
    // to do nothing (alpha finding). The grant survives the popup's death.
    try {
      await chrome.storage.local.set({ [SETTINGS_KEY]: settings });
    } catch (err) {
      return note("could not save settings: " + errText(err));
    }
    note("saved — requesting access to " + origin + "…");
    // This click IS the user gesture chrome.permissions.request needs.
    let granted = false;
    try {
      granted = await chrome.permissions.request({ origins: [hostPattern] });
    } catch (err) {
      return note("saved, but the permission request failed: " + errText(err));
    }
    if (granted) note("saved — " + origin + " access granted");
    else note("saved — but " + origin + " access DENIED (uploads will fail)");
  }

  // ------------------------------------------------------------ record/stop
  async function record() {
    showBanner("");
    const { settings, origin, hostPattern, error } = readForm();
    if (error) return showBanner(error);
    // Disable Record immediately — BEFORE any await — so a double-click during
    // the permission prompt can't fire two concurrent starts (skeptic round).
    el.recordBtn.disabled = true;
    try {
      await chrome.storage.local.set({ [SETTINGS_KEY]: settings });
    } catch {
      /* recording still works this session */
    }
    // The upload origin must be granted before capture starts — this click is
    // still a gesture, so a missing grant can be requested right here.
    let has = false;
    try {
      has = await chrome.permissions.contains({ origins: [hostPattern] });
    } catch {
      has = false;
    }
    if (!has) {
      try {
        has = await chrome.permissions.request({ origins: [hostPattern] });
      } catch {
        has = false;
      }
      if (!has) {
        el.recordBtn.disabled = false;
        return showBanner("no permission for " + origin + " — uploads would fail; Save grants it");
      }
    }
    // Pin the active tab NOW: this popup is anchored over exactly the tab the
    // user means. tab IDs are readable without the "tabs" permission (only
    // url/title/favIconUrl are gated), so the passive posture is unchanged.
    let tabId = null;
    try {
      const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
      tabId = tabs && tabs[0] && tabs[0].id != null ? tabs[0].id : null;
    } catch {
      tabId = null; // background falls back to active-tab semantics
    }
    let reply;
    try {
      reply = await chrome.runtime.sendMessage({
        target: "background",
        type: "start",
        config: {
          baseUrl: settings.baseUrl,
          userId: settings.userId,
          tabId,
          video: settings.video,
        },
      });
    } catch (err) {
      reply = { ok: false, error: errText(err) };
    }
    el.recordBtn.disabled = false;
    if (!reply || !reply.ok) showBanner((reply && reply.error) || "start failed");
    tick();
  }

  async function stop() {
    el.stopBtn.disabled = true;
    try {
      const reply = await chrome.runtime.sendMessage({ target: "background", type: "stop" });
      if (reply && reply.ok === false) showBanner("stop failed: " + (reply.error || "?"));
    } catch (err) {
      showBanner("stop failed: " + errText(err));
    }
    el.stopBtn.disabled = false;
    tick();
  }

  // Escape hatch for a drain that can never finish (e.g. the server URL is wrong
  // — the uploader retries forever BY DESIGN, and settings are locked while
  // draining). Discard kills the capture document: unsent segments are lost
  // (stated on the button), the ledger keeps what already arrived, settings
  // unlock.
  async function discard() {
    el.discardBtn.disabled = true;
    try {
      await chrome.runtime.sendMessage({ target: "background", type: "abandon" });
    } catch {
      /* no receiver = already gone */
    }
    el.discardBtn.disabled = false;
    tick();
  }

  // ---------------------------------------------------------------- status
  function row(key, valueNode) {
    const div = document.createElement("div");
    div.className = "row";
    const k = document.createElement("span");
    k.className = "k";
    k.textContent = key;
    div.append(k, valueNode);
    return div;
  }

  function span(className, text) {
    const s = document.createElement("span");
    s.className = className;
    s.textContent = text; // textContent only: server strings never parsed as HTML
    return s;
  }

  function sessionBlock(snap) {
    const wrap = document.createElement("div");
    wrap.className = "source";

    const verdictText = snap.verdict || (snap.active ? "recording" : "waiting for report…");
    const known = ["clean", "gaps", "recording"].includes(snap.verdict);
    const head = row(
      snap.audioOnly ? "this tab (audio)" : "this tab (video + audio)",
      span("v " + (known ? snap.verdict : "none"), verdictText),
    );
    head.querySelector(".k").className = "k source-name";
    wrap.appendChild(head);

    wrap.appendChild(row("session", span("v mono", snap.sessionId)));

    const u = snap.uploader || {};
    const counters = span(
      "v",
      (u.captured ?? 0) + " captured · " + (u.uploaded ?? 0) + " uploaded · " +
        (u.queued ?? 0) + " queued",
    );
    wrap.appendChild(row("segments", counters));

    if (u.dropped > 0) {
      wrap.appendChild(
        row(
          "dropped",
          span("v err", u.dropped + " segment(s) refused by the server — that data is lost; the report shows it"),
        ),
      );
    }
    const lastErr = u.lastError || snap.lastCaptureError;
    if (lastErr) wrap.appendChild(row("last error", span("v err", lastErr)));

    for (const text of snap.reportLines || []) {
      const line = document.createElement("div");
      line.className = "stream-line";
      line.textContent = text;
      wrap.appendChild(line);
    }
    return wrap;
  }

  function render(status) {
    const live = status && status.ok ? status : null;
    const snap = live ? live.session : null;
    // A start that failed (getUserMedia refusal) has no session but must not
    // vanish: the start reply may have been lost, so this is the surface that
    // tells the user why capture never began.
    const startErr = live && live.startError;
    const active = !!(live && live.active);
    const draining = !!(live && !active && snap && !snap.ended);
    const state = active ? "recording" : draining ? "draining" : "idle";
    document.body.dataset.state = state;
    el.statePill.textContent = state;
    el.recordBtn.hidden = active || draining;
    el.stopBtn.hidden = !active;
    el.discardBtn.hidden = state !== "draining";
    const locked = active || draining;
    el.serverUrl.disabled = locked;
    el.userId.disabled = locked;
    el.capVideo.disabled = locked;
    el.saveBtn.disabled = locked;

    el.sources.textContent = "";
    if (snap) {
      el.sources.appendChild(sessionBlock(snap));
    } else if (startErr) {
      const wrap = document.createElement("div");
      wrap.className = "source";
      const head = row("this tab", span("v err", "did not start: " + startErr));
      head.querySelector(".k").className = "k source-name";
      wrap.appendChild(head);
      el.sources.appendChild(wrap);
    } else {
      const hint = document.createElement("div");
      hint.className = "idle-hint";
      hint.textContent = "no active capture — press Record to capture this tab";
      el.sources.appendChild(hint);
    }
  }

  async function tick() {
    let status = null;
    try {
      // No receiver (offscreen document doesn't exist) rejects — or resolves
      // undefined when only non-matching listeners saw it. Both mean idle.
      status = await chrome.runtime.sendMessage({ target: "offscreen", type: "status" });
    } catch {
      status = null;
    }
    render(status && status.ok ? status : null);
  }

  // ------------------------------------------------------------------ init
  async function init() {
    fillForm(await loadSettings());
    try {
      const reply = await chrome.runtime.sendMessage({ target: "background", type: "device-id" });
      if (reply && reply.ok) {
        el.deviceId.textContent = reply.deviceId;
        el.deviceId.title = reply.deviceId;
      }
    } catch {
      /* worker unavailable — the id renders on the next popup open */
    }
    el.saveBtn.addEventListener("click", saveSettings);
    el.recordBtn.addEventListener("click", record);
    el.stopBtn.addEventListener("click", stop);
    el.discardBtn.addEventListener("click", discard);
    setInterval(tick, STATUS_POLL_MS);
    tick();
  }

  init();
})();
