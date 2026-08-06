"""Contract-schema loading + validation.

The JSON Schemas in ``product/contracts/*.json`` are the SOURCE OF TRUTH. This module
loads them into a ``referencing`` registry (so C4's ``$ref`` to C3 resolves) and exposes
``validate_c4`` / ``validate_c6`` helpers that return a list of human-readable errors
(empty list == valid). We validate the payloads storage produces/consumes against these
in both the request path and the tests.
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

# product/services/storage/app/schemas.py -> parents[3] == product/
_DEFAULT_CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "contracts"

# C2 is v1 (the DP rebuild, D24): the branch validates v1 EXCLUSIVELY (founder ruling
# R1, 2026-08-06). The v0 file stays in contracts/ — it is the running wire on the live
# worktree service until the Stage F cutover, and the OD-2 wipe means no stored v0
# record survives into this code's world.
C2_ID = "https://nucleus.ai/contracts/c2_processed_record.v1.json"
C3_ID = "https://nucleus.ai/contracts/c3_userprompt.v0.json"
C4_ID = "https://nucleus.ai/contracts/c4_turn_record.v0.json"
C6_ID = "https://nucleus.ai/contracts/c6_resolve.v0.json"
# C10 is v1, not v0: it EVOLVED in place (D18) from a raw C2 range read into the day-log
# fetch, keeping its number because its direction and peers are unchanged.
#
# C10 is TWO schemas for the same reason C13 is: a contract is a family of OPERATIONS,
# and the day-log body and the training-window ledger row are different bodies on
# different endpoints. One file could only carry both behind a `oneOf` that hides exactly
# the distinction the contract is about, and `c10_daylog.v1.json`'s $id names the day-log
# specifically — a consumer validating "a C10 day-log" against a root that had quietly
# become a union would be checking nothing.
C10_ID = "https://nucleus.ai/contracts/c10_daylog.v1.json"
C10_WINDOW_ID = "https://nucleus.ai/contracts/c10_training_window.v1.json"
C12_ID = "https://nucleus.ai/contracts/c12_user_profile.v0.json"
# C13 is TWO schemas, not one, and that is the contract rather than a filing choice: the
# training recipe and the gate policy are separate artifacts with separate ids and separate
# lifecycles, because only `recipe_id` may enter a cycle stage key.
C13_RECIPE_ID = "https://nucleus.ai/contracts/c13_recipe.v0.json"
C13_POLICY_ID = "https://nucleus.ai/contracts/c13_gate_policy.v0.json"
C14_ID = "https://nucleus.ai/contracts/c14_reservoir_ledger.v0.json"


def contracts_dir() -> Path:
    """Directory holding the frozen contract schemas (env-overridable for tests/CI)."""
    return Path(os.environ.get("CONTRACTS_DIR", str(_DEFAULT_CONTRACTS_DIR)))


@lru_cache(maxsize=None)
def _load() -> tuple[Registry, dict[str, dict[str, Any]]]:
    directory = contracts_dir()
    registry = Registry()
    schemas: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.glob("*.json")):
        schema = json.loads(path.read_text())
        schema_id = schema.get("$id", path.name)
        registry = registry.with_resource(schema_id, Resource.from_contents(schema))
        schemas[schema_id] = schema
    return registry, schemas


def _validator(schema_id: str) -> Draft202012Validator:
    registry, schemas = _load()
    if schema_id not in schemas:
        raise FileNotFoundError(
            f"Contract schema {schema_id!r} not found in {contracts_dir()}"
        )
    return Draft202012Validator(schemas[schema_id], registry=registry)


def errors(schema_id: str, payload: Any) -> list[dict[str, str]]:
    """Return a list of {path, message} for each schema violation (empty == valid)."""
    validator = _validator(schema_id)
    out: list[dict[str, str]] = []
    for err in sorted(validator.iter_errors(payload), key=lambda e: list(e.absolute_path)):
        loc = "/".join(str(p) for p in err.absolute_path) or "<root>"
        out.append({"path": loc, "message": err.message})
    return out


def validate_c2(payload: Any) -> list[dict[str, str]]:
    """C2 v1, plus the fullmatch the contract itself demands of enforcing
    implementations.

    ``pipeline_version`` and each slot's ``version`` are variable-width, so the
    fixed-length trap closure used on ``record_id`` (and on ``window_id``) is
    unavailable — and in the Python validator these schemas are enforced by, ``$``
    also matches just before a trailing newline, so the schema's ``pattern`` alone
    would admit ``"asr.v1-mock.v1\\n"``. The patterns are read from the loaded schema
    (the source of truth), never restated here; the fullmatch runs only on a payload
    the schema already accepted, so the shape reads below cannot miss.
    """
    out = errors(C2_ID, payload)
    if out:
        return out
    _registry, schemas_by_id = _load()
    schema = schemas_by_id[C2_ID]
    pv_pattern = schema["properties"]["pipeline_version"]["pattern"]
    if not re.fullmatch(pv_pattern, payload["pipeline_version"]):
        out.append({
            "path": "pipeline_version",
            "message": "must fullmatch the contract pattern (Python's `$` admits a "
                       "trailing newline; the contract requires fullmatch)",
        })
    slot_pattern = schema["$defs"]["slot_version"]["pattern"]
    for name, slot in payload["content"]["slots"].items():
        if not re.fullmatch(slot_pattern, slot["version"]):
            out.append({
                "path": f"content/slots/{name}/version",
                "message": "must fullmatch the contract slot_version pattern",
            })
    return out


def validate_c4(payload: Any) -> list[dict[str, str]]:
    return errors(C4_ID, payload)


def validate_c6(payload: Any) -> list[dict[str, str]]:
    return errors(C6_ID, payload)


def validate_c10(payload: Any) -> list[dict[str, str]]:
    """C10 day-log fetch (v1). Storage PRODUCES this one, so it is a self-check on the
    read path (the same shape ``/model-directory/resolve`` applies to C6): a violation is
    a 500, not a 422 — nobody sent us a bad request, we rendered a bad body."""
    return errors(C10_ID, payload)


def validate_c10_window(payload: Any) -> list[dict[str, str]]:
    """C10 training-window ledger ROW (v1) — the body of the open, the close, and every
    element of the enumeration.

    Storage mints every field of it, so this is a read-path self-check with the same
    posture as ``validate_c10``: a violation is a 500, not a 422.

    It is a STRICTLY STRONGER gate than the ``TrainingWindow`` pydantic mirror, and that
    is the reason it exists rather than being redundant with it. The mirror can say
    ``state: Literal["open","consolidated"]`` and ``outcome: WindowOutcome | None``, but it
    cannot say that the two AGREE — a consolidated row carrying a null outcome sails
    through the model and is a night whose training status is unanswerable, because
    ``last_trained_t`` is derived by selecting rows whose outcome is ``published``. The
    schema's ``allOf``/``if`` pair is where that invariant lives.

    ``GET /training/windows`` returns a bare JSON array with no envelope, so callers
    validate it ELEMENT-WISE against this schema; there is deliberately no list wrapper
    contract, because an envelope stamped on every row would put it in the wrong place.
    """
    return errors(C10_WINDOW_ID, payload)


def validate_c12(payload: Any) -> list[dict[str, str]]:
    """C12 user profile.

    NOTE what this does NOT check: ``home_tz``'s pattern is a cheap SHAPE gate (it
    structurally excludes abbreviations like ``PST`` by requiring a region/city form)
    and it happily admits ids that do not exist, e.g. ``Not/AZone``. Only tzdata
    resolution — ``zoneinfo.ZoneInfo(value)`` — can say whether a zone is real, and the
    write path MUST perform it. Likewise ``format: date-time`` is decorative here: the
    validator is built without a ``format_checker``, as it is for every other contract
    in this service.
    """
    return errors(C12_ID, payload)


def validate_c13_recipe(payload: Any) -> list[dict[str, str]]:
    """C13 training recipe. Storage SERVES this one verbatim off disk, so it is a
    self-check on the read path: a violation means an operator filed a malformed artifact,
    which is a 500 (our registry's contents are wrong) rather than a 422.

    NOTE what these two C13 schemas do differently from every other contract here: they
    ALLOW additional properties. Recipe and policy artifacts carry human provenance prose
    (``source``, ``note``, ``traps_note``) and a registry that rejected a recipe for
    documenting itself would push that documentation out of the artifact. A mistyped knob
    is still caught, because every knob that matters is ``required``.
    """
    return errors(C13_RECIPE_ID, payload)


def validate_c13_policy(payload: Any) -> list[dict[str, str]]:
    """C13 gate policy — a SEPARATE artifact from the recipe, with its own id and its own
    schema. Same self-check posture as ``validate_c13_recipe``."""
    return errors(C13_POLICY_ID, payload)


def validate_c14(payload: Any) -> list[dict[str, str]]:
    """C14 reservoir ledger. Storage PRODUCES this body, so it is another read-path
    self-check (500 on violation)."""
    return errors(C14_ID, payload)
