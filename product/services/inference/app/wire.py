"""C9 wire-format primitives.

The C9 response stream is NOT a single JSON document. Its body is:

    <utf-8 answer text chunks...> <U+001E> <one JSON end frame> EOF

To parse: everything before the first U+001E is the answer; JSON-parse
everything after it as the end frame. Mid-turn frames are reserved (not emitted)
in v0.
"""
from __future__ import annotations

# U+001E RECORD SEPARATOR — the single byte that divides answer text from the
# end frame. In UTF-8 this encodes to exactly one byte (0x1e).
RECORD_SEPARATOR = "\x1e"
RECORD_SEPARATOR_BYTES = RECORD_SEPARATOR.encode("utf-8")


def split_stream(body: bytes) -> tuple[bytes, bytes]:
    """Split a full C9 body into (answer_bytes, end_frame_bytes).

    Splits on the FIRST separator only, so an answer that (pathologically)
    contained the separator would not corrupt the end frame parse. Raises if no
    separator is present.
    """
    idx = body.find(RECORD_SEPARATOR_BYTES)
    if idx == -1:
        raise ValueError("C9 body has no U+001E separator")
    return body[:idx], body[idx + len(RECORD_SEPARATOR_BYTES):]
