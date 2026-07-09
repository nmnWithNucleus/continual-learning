"""Safe markdown -> HTML — Python mirror of the renderer in ``static/c9_reader.js``.

Same rules as the JS client so the pytest suite proves the algorithm (incl. the
security property: **all HTML is escaped first**, so model output can never
inject markup). Supported subset: headings, bold, italic, inline code, fenced
code blocks, paragraphs, unordered/ordered lists. Deliberately minimal.
"""

from __future__ import annotations

import re

# Private-use sentinels shielding inline-code spans from *emphasis* processing.
_CODE_OPEN = "\ue000"
_CODE_CLOSE = "\ue001"

_H_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_UL_RE = re.compile(r"^\s*[-*+]\s+(.*)$")
_OL_RE = re.compile(r"^\s*\d+\.\s+(.*)$")

_CODE_SPAN_RE = re.compile(r"`([^`\n]+)`")
_BOLD_STAR_RE = re.compile(r"\*\*([^*]+?)\*\*")
_BOLD_US_RE = re.compile(r"(^|[^A-Za-z0-9_])__([^_\n]+?)__(?=[^A-Za-z0-9_]|$)")
_ITAL_STAR_RE = re.compile(r"\*([^*\n]+?)\*")
_ITAL_US_RE = re.compile(r"(^|[^A-Za-z0-9_])_([^_\n]+?)_(?=[^A-Za-z0-9_]|$)")
_RESTORE_RE = re.compile(re.escape(_CODE_OPEN) + r"(\d+)" + re.escape(_CODE_CLOSE))


def escape_html(s: str) -> str:
    """Escape HTML special chars. The security foundation: run before any markup."""
    if s is None:
        s = ""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _render_inline(escaped: str) -> str:
    """Inline formatting on already-escaped text: code, bold, italic."""
    codes: list[str] = []

    def stash(m: "re.Match[str]") -> str:
        codes.append(m.group(1))
        return _CODE_OPEN + str(len(codes) - 1) + _CODE_CLOSE

    s = _CODE_SPAN_RE.sub(stash, escaped)
    s = _BOLD_STAR_RE.sub(r"<strong>\1</strong>", s)
    s = _BOLD_US_RE.sub(r"\1<strong>\2</strong>", s)
    s = _ITAL_STAR_RE.sub(r"<em>\1</em>", s)
    s = _ITAL_US_RE.sub(r"\1<em>\2</em>", s)

    def restore(m: "re.Match[str]") -> str:
        return "<code>" + codes[int(m.group(1))] + "</code>"

    return _RESTORE_RE.sub(restore, s)


def render_markdown(md: str) -> str:
    """Render a safe markdown subset to an HTML string (mirror of the JS client)."""
    text = escape_html(md)
    lines = text.split("\n")
    out: list[str] = []
    para: list[str] = []
    ul: list[str] = []
    ol: list[str] = []

    def flush() -> None:
        nonlocal para, ul, ol
        if para:
            out.append("<p>" + _render_inline(" ".join(para)) + "</p>")
            para = []
        if ul:
            out.append("<ul>" + "".join("<li>" + _render_inline(t) + "</li>" for t in ul) + "</ul>")
            ul = []
        if ol:
            out.append("<ol>" + "".join("<li>" + _render_inline(t) + "</li>" for t in ol) + "</ol>")
            ol = []

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()

        # Fenced code block: ``` [lang] ... ``` (language ignored). Inner text is
        # already escaped; emit verbatim inside <pre><code>, no inline pass.
        if stripped.startswith("```"):
            flush()
            code: list[str] = []
            i += 1
            while i < n and lines[i].strip() != "```":
                code.append(lines[i])
                i += 1
            if i < n:  # step over the closing fence
                i += 1
            out.append("<pre><code>" + "\n".join(code) + "</code></pre>")
            continue

        if stripped == "":
            flush()
            i += 1
            continue

        h = _H_RE.match(line)
        if h:
            flush()
            level = len(h.group(1))
            out.append(f"<h{level}>" + _render_inline(h.group(2).strip()) + f"</h{level}>")
            i += 1
            continue

        uli = _UL_RE.match(line)
        if uli:
            if para or ol:
                flush()
            ul.append(uli.group(1).strip())
            i += 1
            continue

        oli = _OL_RE.match(line)
        if oli:
            if para or ul:
                flush()
            ol.append(oli.group(1).strip())
            i += 1
            continue

        if ul or ol:
            flush()
        para.append(stripped)
        i += 1

    flush()
    return "\n".join(out)
