"""ULID-like id minting (no external dependency).

C1 requires both ``stream_id`` and ``chunk_id`` to be globally-unique, client-minted,
lexicographically-sortable ids (the contract calls out "e.g. a ULID"). A ULID is a
128-bit value — 48-bit millisecond timestamp + 80-bit randomness — rendered as 26
Crockford-base32 chars. Time-ordered prefix makes a stream's chunk ids sort in
capture order, which is convenient for debugging; ordering/gaps are still carried
authoritatively by ``sequence``, never by the id.
"""
from __future__ import annotations

import os
import time

# Crockford base32 (no I, L, O, U) — the ULID alphabet.
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode(value: int, length: int) -> str:
    chars = [""] * length
    for i in range(length - 1, -1, -1):
        chars[i] = _CROCKFORD[value & 0x1F]
        value >>= 5
    return "".join(chars)


def new_ulid() -> str:
    """A fresh 26-char ULID-like id: 48-bit ms timestamp + 80-bit randomness."""
    ts_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand = int.from_bytes(os.urandom(10), "big")  # 80 bits
    return _encode(ts_ms, 10) + _encode(rand, 16)
