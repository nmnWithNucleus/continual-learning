"""C9 wire-format parser — Python mirror of ``static/c9_reader.js``.

The shipped delivery client is the JavaScript module; this mirror exists so the
same splitting logic can be exercised by pytest without a browser (permitted by
the WS-C row: "a Python mirror is fine for pytest"). Keep the two in lock-step:
the split rule is identical.

C9 wire format (see ../../../contracts/c9_response_stream.v0.json):

    <UTF-8 answer text chunks>  U+001E  <one JSON end frame>  EOF

To parse: everything before the first U+001E is the answer text; everything
after it JSON-parses to the end frame. No mid-turn frames in v0.
"""

from __future__ import annotations

import codecs
import json
from typing import Any, Dict, Iterable, Tuple

# U+001E RECORD SEPARATOR — the single byte between answer text and end frame.
RECORD_SEPARATOR = "\u001e"


def _parse_end_frame(raw: str, saw_separator: bool) -> Dict[str, Any]:
    """Turn the post-separator bytes into an end-frame dict.

    On anything malformed we synthesise an ``{"error": ...}`` frame rather than
    raising, mirroring the JS client so callers can always branch on ``error``.
    """
    s = (raw or "").strip()
    if not saw_separator or not s:
        return {"error": "missing C9 end frame"}
    try:
        obj = json.loads(s)
    except Exception as exc:  # noqa: BLE001 - surface any JSON error as a frame
        return {"error": f"malformed C9 end frame: {exc}"}
    if not isinstance(obj, dict):
        return {"error": "C9 end frame is not a JSON object"}
    return obj


def parse_c9_chunks(chunks: Iterable[bytes]) -> Tuple[str, Dict[str, Any]]:
    """Parse a C9 body delivered as an iterable of byte chunks.

    Decodes UTF-8 incrementally so a multibyte character (or the separator's
    surrounding bytes) split across a chunk boundary is handled correctly —
    the same guarantee ``TextDecoder({stream:true})`` gives the JS client.

    Returns ``(answer_text, end_frame_dict)``.
    """
    decoder = codecs.getincrementaldecoder("utf-8")()
    answer_parts: list[str] = []
    tail_parts: list[str] = []
    saw_sep = False

    def consume(text: str) -> None:
        nonlocal saw_sep
        if not text:
            return
        if saw_sep:
            tail_parts.append(text)
            return
        idx = text.find(RECORD_SEPARATOR)
        if idx == -1:
            answer_parts.append(text)
        else:
            answer_parts.append(text[:idx])
            saw_sep = True
            tail_parts.append(text[idx + 1:])

    for chunk in chunks:
        consume(decoder.decode(chunk))
    # Flush any buffered multibyte remainder.
    consume(decoder.decode(b"", final=True))

    answer = "".join(answer_parts)
    end_frame = _parse_end_frame("".join(tail_parts), saw_sep)
    return answer, end_frame


def parse_c9_bytes(data: bytes) -> Tuple[str, Dict[str, Any]]:
    """Convenience wrapper: parse a whole C9 body given as a single bytes blob."""
    return parse_c9_chunks([data])


def build_c9_stream(answer: str, end_frame: Dict[str, Any]) -> bytes:
    """Encode an answer + end frame into the C9 wire format (test/self-test helper)."""
    return (answer + RECORD_SEPARATOR + json.dumps(end_frame)).encode("utf-8")
