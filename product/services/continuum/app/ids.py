"""Identifier hygiene: user_id and window_id become filesystem path components
(journal, state, reservoir, adapters, model directory) and shutil.rmtree targets
— they must never be able to escape var_dir or collide across users."""
from __future__ import annotations

import re

_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def validate_id(value: str, what: str = "id") -> str:
    if not isinstance(value, str) or not _SAFE.match(value) or ".." in value:
        raise ValueError(
            f"unsafe {what} {value!r}: must match [A-Za-z0-9][A-Za-z0-9._-]* "
            "(no slashes, no '..') — it becomes a filesystem path component")
    return value
