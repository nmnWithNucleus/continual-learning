"""The prompt pack — a git-tree registry whose content digest IS the dialect (D-13).

One ``.prompt.md`` file per prompt (front-matter + ``[system]`` / ``[user]``), plus
``routes.json`` (scenario → pack id, family defaults, scenario labels), ``schemas.json``
(the guided-decoding JSON Schemas), and ``LOCK.json`` (the hand-bumped ``PACK_VERSION`` +
the last-locked digest). This module loads, validates, resolves and hashes them.

Identity under the DP rebuild (L4): ``PACK_DIGEST`` — sha8 over the NORMALISED specs
(id + role + decode params + output schema + system + user, with line endings normalised
and trailing whitespace stripped per line) plus the canonical ``routes.json``. A reflow /
CRLF checkout / trailing newline does NOT move it; every model-facing byte, decode
parameter and the schema DO. The ``clipcap`` stage pins the expected digest as a code
constant and registration FAILS LOUD on a mismatch — so a ``.prompt.md`` edit without a
backend-version (vB) bump is impossible to ship silently. The v0 ``version_tag`` suffix
machinery is gone: identity is the stage's ``<stage>.v<S>-<backend>.v<B>`` segment, and a
prompt change is a vB bump (enforced by the digest pin), never a self-composing tag.

**Loading discipline (D-13 TOCTOU note).** Packs are read ONCE per process at import and
never re-stat'd. Prompts are baked into the image; the v0 ``VIDEO_PROMPT_DIR`` override
and the ``VIDEO_CLIP_PROMPT`` pack override are DEAD (output-affecting env knobs, L4) —
the registry loads from this package directory, full stop. Experiments construct an
experimental stage in code (``experiment=`` → ``.exp-<code>`` in the dialect).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("data-processing.vision.prompts")

_PACKAGE_DIR = Path(__file__).resolve().parent
_PLACEHOLDER_RE = re.compile(r"\[\[(\w+)\]\]")
_SYSTEM_RE = re.compile(r"(?m)^\[system\]\s*$")
_USER_RE = re.compile(r"(?m)^\[user\]\s*$")
_NO_SCHEMA = ("", "none", "null", "-")


class PromptPackError(Exception):
    """A malformed pack file / registry — raised at import so a bad drop-in fails loud."""


@dataclass(frozen=True)
class PromptSpec:
    id: str
    role: str                       # clip | ocr | legacy
    scenarios: tuple[str, ...]      # front-matter declaration (informational; routes.json rules)
    max_tokens: int
    temperature: float
    schema_name: str                # '' when the pack takes no guided-decoding schema
    schema: dict[str, Any]          # the resolved JSON Schema ({} when none) — in the digest
    system: str
    user: str


@dataclass(frozen=True)
class RenderedPrompt:
    system: str
    user: str


# --------------------------------------------------------------------------------------
# Parsing + normalisation (pure — the digest is a pure function of the parsed spec)
# --------------------------------------------------------------------------------------
def _lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def parse_prompt(text: str, schemas: dict[str, Any]) -> PromptSpec:
    """Parse one ``.prompt.md`` (front-matter ``---`` block, then ``[system]`` / ``[user]``
    sections) into a :class:`PromptSpec`, resolving its ``schema:`` name against ``schemas``."""
    text = _lf(text)
    fm: dict[str, str] = {}
    body = text
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end == -1:
            raise PromptPackError("front-matter block is not closed with a '---' line")
        for line in text[4:end].split("\n"):
            line = line.strip()
            if line and ":" in line:
                k, v = line.split(":", 1)
                fm[k.strip()] = v.strip()
        body = text[end + 5:]

    sm, um = _SYSTEM_RE.search(body), _USER_RE.search(body)
    if not sm or not um or um.start() < sm.end():
        raise PromptPackError("a pack needs a [system] section then a [user] section, in order")
    system = body[sm.end():um.start()].strip("\n")
    user = body[um.end():].strip("\n")

    pid = fm.get("id", "").strip()
    role = fm.get("role", "").strip()
    if not pid or not role:
        raise PromptPackError(f"pack is missing id/role in front-matter (got {fm!r})")
    schema_name = fm.get("schema", "none").strip()
    if schema_name.lower() in _NO_SCHEMA:
        schema_name, schema = "", {}
    elif schema_name in schemas:
        schema = schemas[schema_name]
    else:
        raise PromptPackError(f"pack {pid!r}: unknown schema {schema_name!r}")
    scenarios = tuple(
        s.strip() for s in fm.get("scenario", "").split(",") if s.strip()
    )
    try:
        max_tokens = int(fm.get("max_tokens", "512"))
        temperature = float(fm.get("temperature", "0"))
    except ValueError as e:  # typed error — a bad drop-in fails loud AND catchably
        raise PromptPackError(f"pack {pid!r}: non-numeric max_tokens/temperature ({e})")
    return PromptSpec(
        id=pid,
        role=role,
        scenarios=scenarios,
        max_tokens=max_tokens,
        temperature=temperature,
        schema_name=schema_name,
        schema=schema,
        system=system,
        user=user,
    )


def _norm_text(t: str) -> str:
    """Line endings → LF, trailing whitespace stripped per line, trailing blank lines
    dropped. This is what makes a whitespace-only edit a NO-OP for the digest."""
    lines = [ln.rstrip() for ln in _lf(t).split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def normalise(spec: PromptSpec) -> bytes:
    """The canonical, model-facing bytes of a spec: every byte the model sees, every
    decode parameter, and the output schema — nothing else (NOT the ``scenarios``
    routing attribute, which is folded via ``routes.json``)."""
    schema_json = json.dumps(spec.schema, sort_keys=True, separators=(",", ":"))
    parts = [
        f"id={spec.id}",
        f"role={spec.role}",
        f"max_tokens={spec.max_tokens}",
        f"temperature={float(spec.temperature)!r}",
        f"schema={schema_json}",
        f"system=\n{_norm_text(spec.system)}",
        f"user=\n{_norm_text(spec.user)}",
    ]
    return "\x1e".join(parts).encode("utf-8")


def compute_digest(packs: dict[str, PromptSpec], routes: dict[str, Any]) -> str:
    """sha8 over the normalised specs (sorted by id) + the canonical routes.json. Editing
    a prompt's text, a decode param, its schema, a route or a scenario label forks it; a
    whitespace-only edit does not."""
    blob = b"\0".join(normalise(s) for s in sorted(packs.values(), key=lambda s: s.id))
    routes_json = json.dumps(routes, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob + b"\0ROUTES\0" + routes_json).hexdigest()[:8]


def pack_digest(spec: PromptSpec) -> str:
    """Per-pack sha8 (for LOCK.json / audit)."""
    return hashlib.sha256(normalise(spec)).hexdigest()[:8]


def _validate_routes(packs: dict[str, PromptSpec], routes: dict[str, Any], source_dir: Path) -> None:
    """Every pack id ``routes.json`` names (scenarios + family defaults) MUST be a loaded
    pack, and the ``clip`` family default MUST be a loaded ``role=='clip'`` pack. Enforced
    at import so a self-inconsistent drop-in (e.g. a VIDEO_PROMPT_DIR override that omits the
    default pack while ``routes.json`` still references it) fails LOUD here — never as a
    ``select()``/``version_tag()`` divergence that crashes a worker mid-run (an unvalidated
    default would let ``version_tag`` stamp a dialect whose pack ``select`` cannot load)."""
    refs = {f"scenarios[{s}]": pid for s, pid in routes.get("scenarios", {}).items()}
    refs.update({f"family_defaults[{f}]": pid for f, pid in routes.get("family_defaults", {}).items()})
    missing = {ref: pid for ref, pid in refs.items() if pid not in packs}
    if missing:
        raise PromptPackError(
            f"routes.json in {source_dir} references packs not loaded from that dir: {missing} "
            f"(loaded: {sorted(packs)}) — fix routes.json or add the pack file"
        )
    clip_default = routes.get("family_defaults", {}).get("clip", "")
    if clip_default not in packs or packs[clip_default].role != "clip":
        raise PromptPackError(
            f"routes.json family_defaults.clip must name a loaded role=='clip' pack "
            f"(got {clip_default!r}) — the clip primary's last-resort default must be loadable"
        )


def load_registry(source_dir: Path) -> tuple[dict[str, PromptSpec], dict[str, Any], dict[str, Any]]:
    """Load every ``*.prompt.md`` + ``routes.json`` + ``schemas.json`` from ``source_dir``,
    and validate that ``routes.json`` references only loaded packs. Pure w.r.t. the directory
    (exposed so ``relock`` / tests can load an alternate tree)."""
    try:
        schemas = json.loads((source_dir / "schemas.json").read_text("utf-8"))
        routes = json.loads((source_dir / "routes.json").read_text("utf-8"))
    except FileNotFoundError as e:  # pragma: no cover - config error
        raise PromptPackError(f"prompt source {source_dir} missing routes.json/schemas.json: {e}")
    packs: dict[str, PromptSpec] = {}
    for f in sorted(source_dir.glob("*.prompt.md")):
        spec = parse_prompt(f.read_text("utf-8"), schemas)
        if spec.id in packs:
            raise PromptPackError(f"duplicate pack id {spec.id!r} ({f.name})")
        packs[spec.id] = spec
    if not packs:
        raise PromptPackError(f"no *.prompt.md packs found in {source_dir}")
    _validate_routes(packs, routes, source_dir)
    return packs, routes, schemas


def _load_lock_version(source_dir: Path) -> str:
    try:
        return str(json.loads((source_dir / "LOCK.json").read_text("utf-8"))["pack_version"])
    except (FileNotFoundError, KeyError, ValueError):
        return "1"


# --------------------------------------------------------------------------------------
# Load ONCE at import (never re-stat'd — the D-13 TOCTOU discipline). Packaged dir ONLY:
# the v0 VIDEO_PROMPT_DIR override was an output-affecting env knob and is dead (L4).
# --------------------------------------------------------------------------------------
_SOURCE_DIR = _PACKAGE_DIR
PROMPT_SOURCE = "packaged"

_PACKS, _ROUTES, _SCHEMAS = load_registry(_SOURCE_DIR)
PACK_VERSION: str = _load_lock_version(_SOURCE_DIR)
PACK_DIGEST: str = compute_digest(_PACKS, _ROUTES)


# --------------------------------------------------------------------------------------
# Resolution + rendering (public API — the clip captioner backend imports these)
# --------------------------------------------------------------------------------------
def family_default(role: str) -> str:
    """The pinned default pack id for a role — GUARANTEED to be a loaded pack (so
    ``select`` and ``version_tag`` can never disagree on a real, renderable pack).
    ``routes.json`` is validated at import, so the first branch always holds for the clip
    family; the loaded-``role=='clip'`` fallback is belt-and-braces for any other role."""
    pid = _ROUTES.get("family_defaults", {}).get(role, "")
    if pid in _PACKS:
        return pid
    for cand in sorted(_PACKS):  # deterministic last resort: some loaded clip pack
        if _PACKS[cand].role == "clip":
            return cand
    raise PromptPackError(f"no role=='clip' pack loaded to serve family default {role!r}")


def _is_clip(pack_id: str) -> bool:
    """A pack the clip captioner may render (never an ``ocr`` pack, never the ``legacy``
    fingerprint record). Idle/single variants are role ``clip`` and remain selectable."""
    return pack_id in _PACKS and _PACKS[pack_id].role == "clip"


def _resolve_pack_id(scenario: str) -> str:
    """``routes[scenario]`` → pinned family default. ``scenario`` is a CODE PIN in the
    ``clipcap`` stage file (the v0 ``VIDEO_SCENARIO``/``VIDEO_CLIP_PROMPT`` env knobs are
    dead, L4). An unknown scenario — or a route to a non-clip pack — resolves to the
    default: never render the wrong family, never crash on a route gap."""
    by_scenario = _ROUTES.get("scenarios", {}).get(scenario, "")
    if _is_clip(by_scenario):
        return by_scenario
    return family_default("clip")


def select(scenario: str) -> PromptSpec:
    """The active clip pack for a code-pinned scenario."""
    return _PACKS[_resolve_pack_id(scenario)]


def get(pack_id: str) -> PromptSpec:
    """Direct lookup by id (e.g. the idle / single runtime variant). Raises ``KeyError``
    for a truly-unknown id — a code bug, loud."""
    return _PACKS[pack_id]


def scenario_label(scenario: str) -> str:
    """The human phrase rendered into ``[[scenario_label]]`` (``"a macOS desktop"``, …)."""
    return _ROUTES.get("scenario_labels", {}).get(scenario) or "a computer screen"


def all_packs() -> dict[str, PromptSpec]:
    return dict(_PACKS)


def routes() -> dict[str, Any]:
    return json.loads(json.dumps(_ROUTES))  # a defensive deep copy


def render(spec: PromptSpec, /, **context: Any) -> RenderedPrompt:
    """Fill every ``[[token]]`` in the spec's system + user text from ``context``. A
    placeholder with no matching context key RAISES (catches a caller/template mismatch);
    unused context keys are ignored (different roles fill different subsets)."""
    def fill(text: str) -> str:
        def repl(m: "re.Match[str]") -> str:
            key = m.group(1)
            if key not in context:
                raise KeyError(f"prompt {spec.id!r}: missing render context {key!r}")
            return str(context[key])
        return _PLACEHOLDER_RE.sub(repl, text)

    return RenderedPrompt(system=fill(spec.system), user=fill(spec.user))
