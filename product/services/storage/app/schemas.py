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
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

# product/services/storage/app/schemas.py -> parents[3] == product/
_DEFAULT_CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "contracts"

C3_ID = "https://nucleus.ai/contracts/c3_userprompt.v0.json"
C4_ID = "https://nucleus.ai/contracts/c4_turn_record.v0.json"
C6_ID = "https://nucleus.ai/contracts/c6_resolve.v0.json"


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


def validate_c4(payload: Any) -> list[dict[str, str]]:
    return errors(C4_ID, payload)


def validate_c6(payload: Any) -> list[dict[str, str]]:
    return errors(C6_ID, payload)
