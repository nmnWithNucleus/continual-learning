"""The tolerant clip-caption parse ladder — a PURE function of the VLM reply.

This ladder is the **contract of record** for what a reply may look like: guided
decoding (``response_format: json_schema``) is the primary discipline lever, but a
self-hosted 32B is Flash-class, and the POC measured the same rule block scoring
4.10 on Pro / 3.00 on Flash — a weak model ignores instructions. So the served
model may return clean JSON, JSON wrapped in ``` fences or prose, JSON truncated
mid-object by the token budget, JSON with the wrong keys, a refusal, an echo of the
prompt template, or nothing at all — and this ladder turns any of it into a
``ClipDesc`` (or RAISES on an empty reply), classifying HOW degraded the reply was
so the caller can count it.

**Purity is the design** (§5.3: "a pure function of the reply"). No metrics side
effect here, no network, no config, no clock. The caller (the ``clipcap`` backend)
reads the returned ``ParseOutcome.step`` and increments
``dp_video_parse_fallback_total{pack,step}`` itself — it holds the ``pack`` label
and the metrics handle; the ladder holds only the string. This keeps the ladder
trivially testable and keeps "which rung fired" a value, not a side effect. There
is deliberately **NO repair call** (§5.3): a retry costs a whole inference; a 0.5 %
fallback rate costs nothing and a 10 % rate is a prompt bug the offline harness
must catch.

The rungs — first that yields a usable ``description`` wins:

  ``clean``    the whole reply IS one JSON object; parses; has ``description``.
  ``stripped`` a JSON object recovered from surrounding ``` fences / prose (the
               model wrapped its answer); parses; has ``description``.
  ``repair``   the balanced object needed ONE syntactic repair (trailing commas,
               smart quotes, a raw newline inside a string) before it parsed.
  ``keys``     a JSON object parsed but the description came from an ALIAS key
               (``caption`` / ``desc`` / ``summary`` / ``text``), not ``description``.
  ``partial``  no balanced object closed (truncated mid-JSON): the string values
               are salvaged by regex, taking the last (possibly unterminated) one.
  ``line``     no JSON at all: ``App:`` / ``Activity:`` / ``Description:`` lines.
  ``whole``    nothing structured: the whole (bounded) reply becomes the
               description, ``app`` / ``activity`` empty.

Only ``clean`` is NOT a fallback. An empty (or whitespace-only) reply RAISES
``EmptyReplyError`` — a ``ValueError`` subclass, matching ``vlm.py``'s no-choices
contract — so the chunk is not marked done and at-least-once redelivery reprocesses
it, rather than a hollow record landing in ``/context`` (§5.3, D-05).

Budget truncation and the final single-line render (``{app} — {activity}.
{description}``) are NOT here — they live in ``emit.py`` with ``caption_cap`` — so
this module has ZERO dependency on the D1 config / budget / version modules and is
a pure function of the reply string alone. Every field it emits is already
newline-free and whitespace-collapsed (D-12), so a downstream ``\\n`` is impossible
at the source.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .clip_types import ClipDesc

# --- step taxonomy -----------------------------------------------------------
#: The only non-fallback rung.
STEP_CLEAN = "clean"
#: Every rung the ladder can report, best (least degraded) first.
STEPS = ("clean", "stripped", "repair", "keys", "partial", "line", "whole")
#: The rungs that count as a fallback (everything but a clean bare object).
FALLBACK_STEPS = tuple(s for s in STEPS if s != STEP_CLEAN)

# Field aliases: the schema key first, then synonyms a weak / off-schema model
# emits. The description aliases are what turns a "wrong-keys" reply from a lost
# record into a counted ``keys`` fallback.
_APP_KEYS = ("app", "application", "surface", "site", "window", "focus")
_ACTIVITY_KEYS = ("activity", "action")
_DESCRIPTION_KEYS = ("description", "caption", "desc", "summary", "text", "body")
_CANONICAL_DESCRIPTION = "description"

# Defensive bound on the whole-reply fallback's description. The real per-record
# cap is ``caption_cap(span, vs)``, applied later in ``emit.py``; this only stops a
# pathological multi-KB reply (a model dumping its chain-of-thought) from sitting
# in the slot before that truncation runs.
WHOLE_REPLY_CHAR_CAP = 4000

# One syntactic-repair pass (§5.3 step 3): smart quotes -> straight, a raw control
# char inside a string -> its escape, a trailing comma before } or ] removed.
_SMART_QUOTES = {
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
}
_SMART_QUOTE_TABLE = str.maketrans(_SMART_QUOTES)
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")

# A prompt-echo returns the schema template, so a field value looks like
# ``<application, site or window in focus, or 'unknown'>``. An angle-bracket
# placeholder is treated as empty so an echoed template never becomes a record.
_PLACEHOLDER_RE = re.compile(r"^<.*>$", re.DOTALL)

# Regex salvage for a truncated / unbalanced object: ``"key": "value"`` where the
# value may be unterminated (the closing quote is optional). ``(?:[^"\\]|\\.)*``
# stops at an unescaped quote or end-of-string, so a mid-string truncation keeps
# the prefix the model did produce.
_FIELD_RE = re.compile(
    r'"(app|application|surface|site|window|focus|activity|action|'
    r'description|caption|desc|summary|text|body)"\s*:\s*"((?:[^"\\]|\\.)*)"?',
    re.IGNORECASE | re.DOTALL,
)
_SENSITIVE_RE = re.compile(r'"sensitive"\s*:\s*(true|false)', re.IGNORECASE)

# Line mode (§5.3 step 4): ``App:`` / ``Activity:`` / ``Description:`` (or a dash).
_LINE_RE = re.compile(
    r"^\s*(app|application|activity|action|description|caption)\s*[:\-]\s*(.*)$",
    re.IGNORECASE,
)


class EmptyReplyError(ValueError):
    """An empty / whitespace-only VLM reply.

    Subclasses ``ValueError`` so the existing ``vlm.py`` contract (raise -> the
    chunk is not marked done -> at-least-once redelivery) is preserved and any
    caller catching ``ValueError`` still catches it (§5.3, matching
    ``vlm.py:119-121``).
    """


@dataclass(frozen=True)
class ParseOutcome:
    """What the ladder returns: the recovered ``ClipDesc``, the rung that produced
    it, and whether that rung was a fallback (anything but ``clean``). ``pack`` is
    stamped by the CALLER that knows which pack rendered the prompt (the ladder
    stays pure); the pair feeds ``dp_video_parse_fallback_total{pack,step}``."""

    desc: ClipDesc
    step: str
    fallback: bool
    pack: str = ""


# --- cleaning / coercion helpers ---------------------------------------------

def _clean(value: object) -> str:
    """Coerce to ``str`` and collapse EVERY whitespace run (spaces, tabs, and —
    crucially for D-12 — newlines) to a single space, stripping the ends. A field
    that leaves this function can never carry a ``\\n`` into ``content.text``."""
    if value is None:
        return ""
    return " ".join(str(value).split())


def _stringify(value: object) -> str:
    """A caption field is text: pass strings through, render scalars, and drop a
    list/dict value (a model that nested a structure under ``description`` gave us
    nothing usable as prose)."""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def _as_bool(value: object) -> bool:
    """Coerce a ``sensitive`` value to bool, tolerating a stringified ``"true"``."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1")
    if isinstance(value, (int, float)):
        return bool(value)
    return False


def _deplaceholder(text: str) -> str:
    """An echoed schema placeholder (``<...>``) carries no information — treat it
    as empty so a prompt-echo falls through instead of minting a record whose
    fields are the instructions."""
    return "" if _PLACEHOLDER_RE.match(text.strip()) else text


def _pick(obj: dict, keys: tuple[str, ...]) -> tuple[str, str | None]:
    """First present, non-empty (after placeholder-stripping) value among ``keys``.
    Returns ``(value, matched_key)`` or ``("", None)``."""
    for key in keys:
        if key in obj:
            value = _deplaceholder(_stringify(obj[key]))
            if value.strip():
                return value, key
    return "", None


def _first(found: dict[str, str], keys: tuple[str, ...]) -> str:
    for key in keys:
        if found.get(key, "").strip():
            return found[key]
    return ""


# --- rung: JSON object -------------------------------------------------------

def _iter_objects(text: str):
    """Yield each top-level balanced ``{...}`` substring in order, **string-aware**:
    a brace inside a JSON string never opens/closes structure and ``\\`` escapes the
    next character — so a ``description`` containing ``}`` (or ``{``) cannot
    prematurely close the object (§5.3 step 2). A ``{`` that never closes before
    end-of-text (a mid-object truncation) yields nothing for that run, which routes
    the reply to the ``partial`` salvage rung."""
    i, n = 0, len(text)
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        depth = 0
        in_str = False
        esc = False
        j = i
        while j < n:
            c = text[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    yield text[i:j + 1]
                    break
            j += 1
        else:
            return  # ran off the end without closing -> truncated; stop scanning
        i = j + 1


def _loads(src: str) -> dict | None:
    """``json.loads`` guarded to a dict; ``None`` on ANY failure or non-dict.

    The guard is deliberately broad (``Exception``, not just ``JSONDecodeError`` /
    ``ValueError``): a deeply-nested but brace-balanced reply — ``{"a":{"a":{…}}}`` — is
    yielded whole by the string-aware scanner, and CPython's recursive JSON decoder then
    raises ``RecursionError`` (a ``RuntimeError``, NOT a ``ValueError``) before it finishes.
    An adversarial ~40 KB reply is enough. The ladder's contract is that a non-empty reply
    NEVER raises and never blows up on adversarial input (§5.3), so *any* decode failure
    here means "not a usable object at this rung" → ``None`` → fall through to the partial /
    line / whole rungs. ``BaseException`` (KeyboardInterrupt / SystemExit) still propagates.
    A caught ``RecursionError`` unwinds the stack fully, so the ladder continues at normal
    depth. (Regression: a 20 000-deep object/array must reach the ``whole`` rung, not raise.)
    """
    try:
        obj = json.loads(src)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _escape_raw_controls_in_strings(src: str) -> str:
    """Escape a raw newline / carriage-return / tab that appears INSIDE a JSON
    string (a model that hard-wrapped a long ``description``). Outside strings the
    characters are structural whitespace and are left alone."""
    out: list[str] = []
    in_str = False
    esc = False
    for c in src:
        if in_str:
            if esc:
                out.append(c)
                esc = False
                continue
            if c == "\\":
                out.append(c)
                esc = True
                continue
            if c == '"':
                in_str = False
                out.append(c)
                continue
            if c == "\n":
                out.append("\\n")
                continue
            if c == "\r":
                out.append("\\r")
                continue
            if c == "\t":
                out.append("\\t")
                continue
            out.append(c)
        else:
            if c == '"':
                in_str = True
            out.append(c)
    return "".join(out)


def _repair(src: str) -> str:
    """The single tolerated syntactic-repair pass (§5.3 step 3)."""
    fixed = src.translate(_SMART_QUOTE_TABLE)
    fixed = _escape_raw_controls_in_strings(fixed)
    fixed = _TRAILING_COMMA_RE.sub(r"\1", fixed)
    return fixed


def _from_object(obj: dict, raw: str, step: str) -> tuple[ClipDesc, str] | None:
    """Build a ``ClipDesc`` from a parsed dict. ``None`` when no usable description
    is present (wrong keys with no alias) so the ladder falls through. A description
    recovered via an ALIAS downgrades the step to ``keys`` and marks ``parsed=False``
    (the schema was not followed)."""
    app, _ = _pick(obj, _APP_KEYS)
    activity, _ = _pick(obj, _ACTIVITY_KEYS)
    description, desc_key = _pick(obj, _DESCRIPTION_KEYS)
    if not description:
        return None
    resolved = step
    parsed = step in ("clean", "stripped", "repair")
    if parsed and desc_key != _CANONICAL_DESCRIPTION:
        resolved = "keys"
        parsed = False
    desc = ClipDesc(
        app=_clean(app),
        activity=_clean(activity),
        description=_clean(description),
        sensitive=_as_bool(obj.get("sensitive")),
        raw=raw,
        parsed=parsed,
    )
    return desc, resolved


# --- rung: partial salvage / line / whole ------------------------------------

def _salvage_partial(text: str, raw: str) -> ClipDesc | None:
    """Regex-salvage the string fields of a truncated / unbalanced object. Returns
    ``None`` if no description-bearing field survives, so the ladder falls to line
    mode."""
    found: dict[str, str] = {}
    for m in _FIELD_RE.finditer(text):
        key = m.group(1).lower()
        if key in found:
            continue  # first occurrence per key wins (deterministic)
        value = (
            m.group(2)
            .replace("\\n", " ")
            .replace("\\r", " ")
            .replace("\\t", " ")
            .replace('\\"', '"')
            .replace("\\\\", "\\")
        )
        found[key] = _deplaceholder(value)
    description = _first(found, _DESCRIPTION_KEYS)
    if not description.strip():
        return None
    sm = _SENSITIVE_RE.search(text)
    return ClipDesc(
        app=_clean(_first(found, _APP_KEYS)),
        activity=_clean(_first(found, _ACTIVITY_KEYS)),
        description=_clean(description),
        sensitive=bool(sm) and sm.group(1).lower() == "true",
        raw=raw,
        parsed=False,
    )


def _line_mode(text: str, raw: str) -> ClipDesc | None:
    """``App:`` / ``Activity:`` / ``Description:`` lines (§5.3 step 4). ``None`` when
    no description line is present."""
    fields: dict[str, str] = {}
    for line in text.splitlines():
        m = _LINE_RE.match(line)
        if m:
            fields.setdefault(m.group(1).lower(), m.group(2).strip())
    description = _first(fields, ("description", "caption"))
    if not description.strip():
        return None
    return ClipDesc(
        app=_clean(_first(fields, ("app", "application"))),
        activity=_clean(_first(fields, ("activity", "action"))),
        description=_clean(description),
        sensitive=False,
        raw=raw,
        parsed=False,
    )


def _whole_mode(text: str, raw: str) -> ClipDesc:
    """The last rung (§5.3 step 5): the whole reply IS the description, ``app`` /
    ``activity`` empty. Bounded defensively; ``emit.py`` applies the real budget."""
    return ClipDesc(
        app="",
        activity="",
        description=_clean(text)[:WHOLE_REPLY_CHAR_CAP],
        sensitive=False,
        raw=raw,
        parsed=False,
    )


# --- the ladder --------------------------------------------------------------

def parse_clip(reply: str) -> ParseOutcome:
    """Turn any VLM reply into a ``ParseOutcome`` (or raise ``EmptyReplyError`` on
    an empty / whitespace-only reply). Pure: same input -> same output, no side
    effects. See the module docstring for the rung semantics."""
    raw = reply if isinstance(reply, str) else ("" if reply is None else str(reply))
    text = raw.strip()
    if not text:
        raise EmptyReplyError(
            "empty VLM reply — chunk not marked done, at-least-once redelivery reprocesses it"
        )

    objects = list(_iter_objects(text))
    # "clean" is reserved for a reply that IS exactly one JSON object with nothing
    # around it; a wrapper (fence/prose) or trailing junk is a ``stripped`` fallback.
    bare = len(objects) == 1 and objects[0].strip() == text

    for idx, src in enumerate(objects):
        obj = _loads(src)
        step = "clean" if (bare and idx == 0) else "stripped"
        if obj is None:
            obj = _loads(_repair(src))
            if obj is not None:
                step = "repair"
        if obj is not None:
            built = _from_object(obj, raw, step)
            if built is not None:
                desc, resolved = built
                return ParseOutcome(desc=desc, step=resolved, fallback=(resolved != STEP_CLEAN))

    partial = _salvage_partial(text, raw)
    if partial is not None:
        return ParseOutcome(desc=partial, step="partial", fallback=True)

    lined = _line_mode(text, raw)
    if lined is not None:
        return ParseOutcome(desc=lined, step="line", fallback=True)

    return ParseOutcome(desc=_whole_mode(text, raw), step="whole", fallback=True)
