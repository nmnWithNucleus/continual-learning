// Nucleus input surface — serve-loop MVP v0.0 (computer text surface only).
//
// Sends a turn to POST /api/turn and reads the streamed C9 response. The C9 wire
// format is: answer text chunks, then ONE U+001E separator byte, then a single
// JSON end frame.
//
// -------------------------------------------------------------------------------
// MARKDOWN RENDER SEAM (owned by WS-C / output) — WIRED IN BY THE INTEGRATOR.
// C9 parsing + safe markdown rendering is owned by output's browser client,
// services/output/app/static/c9_reader.js (vendored here as ./c9_reader.js so the
// import is same-origin). We import renderC9Stream and hand it the fetch Response;
// it streams the answer, renders SAFE markdown live into #answer, and returns the
// end frame + usage. This file no longer parses the C9 body itself.
// -------------------------------------------------------------------------------

import { renderC9Stream } from "./c9_reader.js";

const form = document.getElementById("composer");
const textEl = document.getElementById("text");
const sendBtn = document.getElementById("send");
const answerEl = document.getElementById("answer");
const metaEl = document.getElementById("meta");

// Session persists across turns within this page load. Minted server-side on the
// first turn and echoed back via the X-Session-Id response header.
let sessionId = null;

function setMeta(text, isError) {
  metaEl.textContent = text;
  metaEl.className = isError ? "meta err" : "meta";
}

function showEndFrame(frame, usage) {
  if (!frame || frame.error) {
    setMeta("error: " + ((frame && frame.error) || "no end frame"), true);
    return;
  }
  const u = usage || frame.usage || {};
  const parts = [];
  if (frame.model_id) parts.push(frame.model_id);
  if (frame.adapter) parts.push("adapter=" + frame.adapter);
  if (u.prompt_tokens != null || u.output_tokens != null) {
    parts.push("tokens " + (u.prompt_tokens ?? "?") + "→" + (u.output_tokens ?? "?"));
  }
  setMeta(parts.join(" · "), false);
}

async function sendTurn(text) {
  answerEl.innerHTML = "";
  setMeta("…", false);

  const body = { text };
  if (sessionId) body.session_id = sessionId;

  let resp;
  try {
    resp = await fetch("/api/turn", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (e) {
    setMeta("network error: " + e, true);
    return;
  }

  const sid = resp.headers.get("X-Session-Id");
  if (sid) sessionId = sid;

  if (!resp.ok || !resp.body) {
    setMeta("request failed: HTTP " + resp.status, true);
    return;
  }

  // Hand the C9 stream to output's reader: it splits answer/end-frame, renders
  // safe markdown live into #answer, and reports the end frame + usage.
  try {
    await renderC9Stream(resp, answerEl, {
      onEndFrame: (frame, usage) => showEndFrame(frame, usage),
    });
  } catch (e) {
    setMeta("stream error: " + e, true);
  }
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = textEl.value.trim();
  if (!text) return;
  sendBtn.disabled = true;
  try {
    await sendTurn(text);
  } finally {
    sendBtn.disabled = false;
    textEl.focus();
  }
});

// Enter to send, Shift+Enter for newline.
textEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    form.requestSubmit();
  }
});
