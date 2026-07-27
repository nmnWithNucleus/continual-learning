"""Identifier hygiene for ids that become FILESYSTEM PATH COMPONENTS.

LIFTED verbatim in substance from ``services/continuum/app/ids.py`` — same regex, same
``".."`` rule — because it is the same job on the same values, and the two services now
hand these ids to each other. A ``user_id`` names a reservoir directory; a ``recipe_id``
/ ``policy_id`` resolves to ``<dir>/<id>.json``; a ``window_id`` names a corpus file.
None of them may escape the root they are joined to, and none of them may collide across
users.

Two validators live in this service and they are NOT the same gate, so keep them apart:

  ``ids.validate_path_id``      the WIDE gate — "could this string safely be one path
                                component?". Applies to ids other systems mint (user_id,
                                recipe_id, policy_id).
  ``window_id.validate_window_id``  the NARROW gate — "did OUR minter shape this?".
                                Applies only to window ids, which storage alone mints.

Every value that passes the narrow gate also passes this one (pinned by a test): the
minted format ``w<YYYYMMDD>T<HHMMSS>Z`` is alphanumeric throughout. The reverse is
emphatically not true, which is why the close/day-log/reservoir surfaces run the narrow
one — a raw RFC3339 instant is path-hostile (colons) and a bare word is not a window.
"""
from __future__ import annotations

import re

# Continuum's `ids.py:8` regex, restated rather than imported: services do not import each
# other's code, and a copy that a test pins against the original is more honest than a
# dependency. Leading char alphanumeric (so no leading dot, no hidden file), then
# alnum/dot/underscore/hyphen, 128 chars max. No slashes, no backslashes, no spaces.
PATH_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
_PATH_ID_RE = re.compile(PATH_ID_PATTERN)


def validate_path_id(value: str) -> bool:
    """True iff ``value`` is safe to use as a single filesystem path component.

    ``".."`` is rejected explicitly as well as by the pattern: the pattern already
    excludes a bare ``".."`` (it cannot start with a dot), but ``"a..b"`` matches it, and
    while that is harmless on its own it is the shape every traversal attempt is built
    from. Rejecting it outright costs nothing and removes the question.
    """
    if not isinstance(value, str) or ".." in value:
        return False
    # fullmatch, not match — Python's `$` also matches before a trailing newline, and
    # "recipe\n" is a different filename from "recipe".
    return bool(_PATH_ID_RE.fullmatch(value))
