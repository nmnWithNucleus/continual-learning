// @ts-nocheck
/* global chrome */
/*
 * WS-E — Nucleus Capture popup: UI ONLY (D-E5). Record/stop, settings, and a
 * ~1s status pull from the offscreen capture engine — mirrors the phone
 * client's status panel (clients/web/): per-source captured/uploaded/queued
 * counters, verdict badge, report lines, last error.
 *
 * - Settings (server URL, user id, source toggles) live in chrome.storage
 *   .local. Save is the USER GESTURE that runs chrome.permissions.request for
 *   exactly the configured origin (D-E1 — runtime grant, no static hosts);
 *   Record re-requests if the grant is missing (still a gesture).
 * - The popup owns NO capture state: it sends {target:"background"} start/stop
 *   and pulls {target:"offscreen", type:"status"} snapshots. A no-receiver
 *   reply (undefined/rejection) simply means the offscreen document doesn't
 *   exist -> render idle. The popup dying (e.g. when Chrome's screen picker
 *   takes focus) loses nothing — reopen it and the status pull resumes.
 * - device_id is minted and stored by background (single home); the popup only
 *   displays it.
 */
(() => {
  "use strict";

  const STATUS_POLL_MS = 1000;
  const SETTINGS_KEY = "nucleus.ext.settings";
  const DEFAULTS = {
    baseUrl: "http://localhost:8084",
    userId: "beta-user",
    screen: true,
    tabAudio: true,
  };
  const SOURCE_LABELS = { screen: "screen", tabAudio: "tab audio" };

  const $ = (id) => document.getElementById(id);
  const el = {
    banner: $("error-banner"),
    statePill: $("state-pill"),
    serverUrl: $("server-url"),
    userId: $("user-id"),
    srcScreen: $("src-screen"),
    srcTabAudio: $("src-tab-audio"),
    saveBtn: $("save-btn"),
    saveNote: $("save-note"),
    recordBtn: $("record-btn"),
    stopBtn: $("stop-btn"),
    startOutcome: $("start-outcome"),
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
    // permission grant is per-origin.
    const settings = {
      baseUrl: url.origin,
      userId: el.userId.value.trim() || DEFAULTS.userId,
      screen: el.srcScreen.checked,
      tabAudio: el.srcTabAudio.checked,
    };
    return { settings, origin: url.origin };
  }

  function fillForm(settings) {
    el.serverUrl.value = settings.baseUrl;
    el.userId.value = settings.userId;
    el.srcScreen.checked = !!settings.screen;
    el.srcTabAudio.checked = !!settings.tabAudio;
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
    const { settings, origin, error } = readForm();
    if (error) return note(error);
    fillForm(settings); // reflect normalization (origin) back into the input
    // This click IS the user gesture chrome.permissions.request needs.
    let granted = false;
    try {
      granted = await chrome.permissions.request({ origins: [origin + "/*"] });
    } catch (err) {
      note("permission request failed: " + errText(err));
      granted = false;
    }
    try {
      await chrome.storage.local.set({ [SETTINGS_KEY]: settings });
    } catch (err) {
      return note("could not save settings: " + errText(err));
    }
    if (granted) note("saved — " + origin + " access granted");
    else note("saved — but " + origin + " access DENIED (uploads will fail)");
  }

  // ------------------------------------------------------------ record/stop
  async function record() {
    showBanner("");
    el.startOutcome.hidden = true;
    const { settings, origin, error } = readForm();
    if (error) return showBanner(error);
    if (!settings.screen && !settings.tabAudio) {
      return showBanner("pick at least one source (screen / tab audio)");
    }
    try {
      await chrome.storage.local.set({ [SETTINGS_KEY]: settings });
    } catch {
      /* recording still works this session */
    }
    // The upload origin must be granted before capture starts — this click is
    // still a gesture, so a missing grant can be requested right here.
    let has = false;
    try {
      has = await chrome.permissions.contains({ origins: [origin + "/*"] });
    } catch {
      has = false;
    }
    if (!has) {
      try {
        has = await chrome.permissions.request({ origins: [origin + "/*"] });
      } catch {
        has = false;
      }
      if (!has) {
        return showBanner("no permission for " + origin + " — uploads would fail; Save grants it");
      }
    }
    el.recordBtn.disabled = true;
    let reply;
    try {
      reply = await chrome.runtime.sendMessage({
        target: "background",
        type: "start",
        config: {
          baseUrl: settings.baseUrl,
          userId: settings.userId,
          sources: { screen: settings.screen, tabAudio: settings.tabAudio },
        },
      });
    } catch (err) {
      reply = { ok: false, error: errText(err) };
    }
    el.recordBtn.disabled = false;
    renderStartOutcome(reply);
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

  function renderStartOutcome(reply) {
    const parts = [];
    const srcs = (reply && reply.sources) || {};
    for (const kind of ["screen", "tabAudio"]) {
      if (srcs[kind]) parts.push(SOURCE_LABELS[kind] + ": " + srcs[kind]);
    }
    if (!reply || !reply.ok) {
      showBanner(
        ((reply && reply.error) || "start failed") + (parts.length ? " — " + parts.join(" · ") : ""),
      );
      return;
    }
    el.startOutcome.textContent = parts.join(" · ");
    el.startOutcome.hidden = parts.length === 0;
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

  function sourceBlock(kind, snap) {
    const wrap = document.createElement("div");
    wrap.className = "source";

    const verdictText = snap.verdict || (snap.active ? "recording" : "waiting for report…");
    const known = ["clean", "gaps", "recording"].includes(snap.verdict);
    const head = row(
      SOURCE_LABELS[kind],
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
    const snaps = live ? live.sources : { screen: null, tabAudio: null };
    // A source that FAILED to start has no snapshot but must not vanish: the
    // start reply is lost whenever the screen picker killed the popup, so this
    // is the only surface that can tell the user (e.g.) tab audio never began.
    const startErrs = (live && live.startErrors) || {};
    const any = !!(snaps.screen || snaps.tabAudio || startErrs.screen || startErrs.tabAudio);
    const active = !!(live && live.active);
    const draining = !!(
      live && !active &&
      ["screen", "tabAudio"].some((k) => snaps[k] && !snaps[k].ended)
    );
    const state = active ? "recording" : draining ? "draining" : "idle";
    document.body.dataset.state = state;
    el.statePill.textContent = state;
    el.recordBtn.hidden = active || draining;
    el.stopBtn.hidden = !active;
    const locked = active || draining;
    el.serverUrl.disabled = locked;
    el.userId.disabled = locked;
    el.srcScreen.disabled = locked;
    el.srcTabAudio.disabled = locked;
    el.saveBtn.disabled = locked;

    el.sources.textContent = "";
    if (!any) {
      const hint = document.createElement("div");
      hint.className = "idle-hint";
      hint.textContent = "no active capture — press Record to start";
      el.sources.appendChild(hint);
      return;
    }
    for (const kind of ["screen", "tabAudio"]) {
      if (snaps[kind]) {
        el.sources.appendChild(sourceBlock(kind, snaps[kind]));
      } else if (startErrs[kind]) {
        const wrap = document.createElement("div");
        wrap.className = "source";
        const head = row(
          SOURCE_LABELS[kind],
          span("v err", "did not start: " + startErrs[kind]),
        );
        head.querySelector(".k").className = "k source-name";
        wrap.appendChild(head);
        el.sources.appendChild(wrap);
      }
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
    setInterval(tick, STATUS_POLL_MS);
    tick();
  }

  init();
})();
