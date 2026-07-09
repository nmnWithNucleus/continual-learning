/* =====================================================================
   VENDORED COPY — canonical source: services/output/app/static/c9_reader.js
   (WS-C owns it). Copied here by the integrator so the input surface can
   import it same-origin from :8081 without a cross-origin ES-module fetch to
   output :8082 (which would need CORS). Keep in lock-step with the source; if
   output's reader changes, re-copy this file. See services/README.md.
   =====================================================================

   Nucleus Output Service — browser-side C9 reader  (serve-loop MVP v0.0)

   The actual "delivery to the computer surface": given a fetch() Response
   whose body is the C9 wire format, stream-read it, split the answer text
   from the JSON end frame, render the answer as SAFE markdown, and surface
   the end-frame usage.

   C9 wire format (see product/contracts/c9_response_stream.v0.json):
     <UTF-8 answer text chunks…>  U+001E  <one JSON end frame>  EOF
   To parse: everything before the first U+001E is the answer; everything
   after it JSON-parses to the end frame
     { contract:"C9", version:"0", turn_id, model_id, adapter:"base",
       usage:{prompt_tokens, output_tokens}, finished:true }   (or {…error}).
   No mid-turn frames in v0.

   Dependency-free ES module. No external libraries. The markdown renderer
   is a tiny, purpose-built, SAFE subset (headings / bold / italic / inline
   code / fenced code blocks / paragraphs / lists) that ESCAPES ALL HTML
   FIRST, so model output can never inject markup (no XSS).

   Primary export the input surface imports:  renderC9Stream(response, el)
   ===================================================================== */

'use strict';

// U+001E RECORD SEPARATOR — the single byte between answer text and end frame.
export const RECORD_SEPARATOR = '\u001e';

// Private-use sentinels used to shield inline-code spans from *emphasis*
// processing. Chosen from the Unicode private-use area; they never appear in
// real answer text in practice, and even if they did it is purely cosmetic —
// not a security concern (all HTML is already escaped).
const CODE_OPEN = '\ue000';
const CODE_CLOSE = '\ue001';

/* ---------------------------------------------------------------------
   HTML escaping — the security foundation. Run on the WHOLE answer before
   any markup is introduced, so no substring the model emits can become a
   live tag/attribute. Our own tags are added afterwards from a fixed set.
   --------------------------------------------------------------------- */
export function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/* ---------------------------------------------------------------------
   Inline formatting, applied to ALREADY-ESCAPED text:
     `code`   **bold**   __bold__   *italic*   _italic_
   Inline code is stashed first so markers inside it are left literal;
   bold runs before italic so `**` is not eaten by single-`*` italic.
   The `_`/`__` forms require non-word boundaries so snake_case is safe.
   --------------------------------------------------------------------- */
function renderInline(escaped) {
  const codes = [];
  let s = escaped.replace(/`([^`\n]+)`/g, (_m, code) => {
    codes.push(code);
    return CODE_OPEN + (codes.length - 1) + CODE_CLOSE;
  });

  s = s.replace(/\*\*([^*]+?)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/(^|[^A-Za-z0-9_])__([^_\n]+?)__(?=[^A-Za-z0-9_]|$)/g, '$1<strong>$2</strong>');
  s = s.replace(/\*([^*\n]+?)\*/g, '<em>$1</em>');
  s = s.replace(/(^|[^A-Za-z0-9_])_([^_\n]+?)_(?=[^A-Za-z0-9_]|$)/g, '$1<em>$2</em>');

  s = s.replace(new RegExp(CODE_OPEN + '(\\d+)' + CODE_CLOSE, 'g'),
    (_m, i) => '<code>' + codes[Number(i)] + '</code>');
  return s;
}

/* ---------------------------------------------------------------------
   Block-level markdown -> HTML. Supports headings (#..######), fenced code
   blocks (```), unordered lists (-,*,+), ordered lists (1.), and
   paragraphs. Everything is escaped up front; blocks only ever wrap that
   escaped text in a fixed set of tags. Returns an HTML string.
   --------------------------------------------------------------------- */
export function renderMarkdown(md) {
  const text = escapeHtml(md);
  const lines = text.split('\n');
  const out = [];
  let para = [];
  let ul = [];
  let ol = [];

  const flush = () => {
    if (para.length) {
      out.push('<p>' + renderInline(para.join(' ')) + '</p>');
      para = [];
    }
    if (ul.length) {
      out.push('<ul>' + ul.map((t) => '<li>' + renderInline(t) + '</li>').join('') + '</ul>');
      ul = [];
    }
    if (ol.length) {
      out.push('<ol>' + ol.map((t) => '<li>' + renderInline(t) + '</li>').join('') + '</ol>');
      ol = [];
    }
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const stripped = line.trim();

    // Fenced code block: ``` [lang] … ``` (language ignored). Inner text is
    // already escaped; emit verbatim inside <pre><code>, no inline pass.
    if (stripped.startsWith('```')) {
      flush();
      const code = [];
      i++;
      while (i < lines.length && lines[i].trim() !== '```') {
        code.push(lines[i]);
        i++;
      }
      // (i now points at the closing fence or past EOF; loop's i++ steps over it.)
      out.push('<pre><code>' + code.join('\n') + '</code></pre>');
      continue;
    }

    if (stripped === '') { flush(); continue; }

    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      flush();
      const level = h[1].length;
      out.push('<h' + level + '>' + renderInline(h[2].trim()) + '</h' + level + '>');
      continue;
    }

    const uli = line.match(/^\s*[-*+]\s+(.*)$/);
    if (uli) {
      if (para.length || ol.length) flush();
      ul.push(uli[1].trim());
      continue;
    }

    const oli = line.match(/^\s*\d+\.\s+(.*)$/);
    if (oli) {
      if (para.length || ul.length) flush();
      ol.push(oli[1].trim());
      continue;
    }

    if (ul.length || ol.length) flush();
    para.push(stripped);
  }

  flush();
  return out.join('\n');
}

// Alias — some callers prefer this name.
export const markdownToHtml = renderMarkdown;

/* ---------------------------------------------------------------------
   Turn a raw end-frame string into an object. On anything malformed we
   synthesize an {error} frame rather than throwing, so a delivery surface
   can always show *something* and the caller can branch on `.error`.
   --------------------------------------------------------------------- */
function parseEndFrame(raw, sawSeparator) {
  const s = (raw || '').trim();
  if (!sawSeparator || !s) return { error: 'missing C9 end frame' };
  try {
    const obj = JSON.parse(s);
    if (!obj || typeof obj !== 'object' || Array.isArray(obj)) {
      return { error: 'C9 end frame is not a JSON object' };
    }
    return obj;
  } catch (err) {
    return { error: 'malformed C9 end frame: ' + (err && err.message ? err.message : err) };
  }
}

/* ---------------------------------------------------------------------
   readC9Stream(response, { onText })  -> { answer, endFrame }

   Low-level: consumes the C9 body from a fetch() Response, splitting the
   answer from the end frame on the first U+001E. `onText(answerSoFar, delta)`
   fires as answer chunks arrive (delta may be '' when only the tail grows).
   Robust to the separator or a multibyte UTF-8 char landing across a chunk
   boundary. Falls back to response.text() where streaming isn't available.
   --------------------------------------------------------------------- */
export async function readC9Stream(response, opts = {}) {
  const onText = typeof opts.onText === 'function' ? opts.onText : null;

  let answer = '';
  let tail = '';
  let sawSep = false;

  const consume = (chunk) => {
    if (!chunk) return;
    if (sawSep) { tail += chunk; return; }
    const idx = chunk.indexOf(RECORD_SEPARATOR);
    if (idx === -1) {
      answer += chunk;
      if (onText) onText(answer, chunk);
    } else {
      const head = chunk.slice(0, idx);
      answer += head;
      sawSep = true;
      tail += chunk.slice(idx + 1);
      if (onText) onText(answer, head);
    }
  };

  const hasReader = response && response.body && typeof response.body.getReader === 'function';
  if (!hasReader) {
    // Non-streaming fallback (older engines / mocked responses).
    const full = await response.text();
    const idx = full.indexOf(RECORD_SEPARATOR);
    if (idx === -1) {
      answer = full;
      if (onText) onText(answer, full);
    } else {
      answer = full.slice(0, idx);
      sawSep = true;
      tail = full.slice(idx + 1);
      if (onText) onText(answer, answer);
    }
    return { answer, endFrame: parseEndFrame(tail, sawSep) };
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8');
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    if (value) consume(decoder.decode(value, { stream: true }));
  }
  const flushTail = decoder.decode();
  if (flushTail) consume(flushTail);

  return { answer, endFrame: parseEndFrame(tail, sawSep) };
}

/* ---------------------------------------------------------------------
   renderC9Stream(response, target, { onText, onEndFrame })
       -> { answer, endFrame, usage }

   The clean function the input surface imports. Streams the C9 body and
   renders the answer as SAFE markdown into `target` (a DOM element or a
   selector string) live as it arrives, coalescing DOM writes with
   requestAnimationFrame. On completion it exposes the end frame + usage.
   `target` may be omitted to just collect the result.
   --------------------------------------------------------------------- */
export async function renderC9Stream(response, target, opts = {}) {
  const el = typeof target === 'string'
    ? (typeof document !== 'undefined' ? document.querySelector(target) : null)
    : (target || null);
  const onText = typeof opts.onText === 'function' ? opts.onText : null;
  const onEndFrame = typeof opts.onEndFrame === 'function' ? opts.onEndFrame : null;

  const canRAF = typeof requestAnimationFrame === 'function';
  let pending = false;
  let latest = '';
  const paint = () => {
    pending = false;
    if (el) el.innerHTML = renderMarkdown(latest);
  };
  const schedule = (md) => {
    latest = md;
    if (!el) return;
    if (!canRAF) { paint(); return; }
    if (pending) return;
    pending = true;
    requestAnimationFrame(paint);
  };

  const { answer, endFrame } = await readC9Stream(response, {
    onText: (soFar, delta) => {
      schedule(soFar);
      if (onText) onText(soFar, delta);
    },
  });

  // Final, complete render (also covers the rAF-coalesced tail).
  latest = answer;
  if (el) {
    el.innerHTML = renderMarkdown(answer);
    if (endFrame && endFrame.error) el.classList.add('c9-error');
    else el.classList.remove('c9-error');
  }

  const usage = (endFrame && endFrame.usage) || null;
  if (onEndFrame) onEndFrame(endFrame, usage);
  return { answer, endFrame, usage };
}

export default renderC9Stream;
