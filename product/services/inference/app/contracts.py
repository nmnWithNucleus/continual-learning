"""Load the frozen JSON Schemas and validate payloads against them.

The schemas live in product/contracts/ (source of truth). c4 references c3 via a
relative "$ref": "c3_userprompt.v0.json", so we register every schema by its
"$id" in a referencing Registry and let the ref resolve.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError  # re-exported for callers
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

__all__ = ["validate_contract", "ValidationError", "contracts_dir"]

# Logical name -> schema filename.
_SCHEMA_FILES = {
    "c3": "c3_userprompt.v0.json",
    "c4": "c4_turn_record.v0.json",
    "c6": "c6_resolve.v0.json",
    "c9": "c9_response_stream.v0.json",
}


def contracts_dir() -> Path:
    """Directory holding the *.json schemas.

    Defaults to product/contracts/ (three levels up from app/), overridable via
    the CONTRACTS_DIR env var.
    """
    env = os.getenv("CONTRACTS_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2].parent / "contracts"


@lru_cache(maxsize=1)
def _registry() -> Registry:
    resources = []
    directory = contracts_dir()
    for filename in _SCHEMA_FILES.values():
        data = json.loads((directory / filename).read_text())
        resource = Resource.from_contents(data, default_specification=DRAFT202012)
        # Register under the schema's $id so relative $refs between schemas
        # (c4 -> c3) resolve, and under the bare filename as a convenience.
        uri = data.get("$id", filename)
        resources.append((uri, resource))
        resources.append((filename, resource))
    return Registry().with_resources(resources)


@lru_cache(maxsize=len(_SCHEMA_FILES))
def _validator(name: str) -> Draft202012Validator:
    directory = contracts_dir()
    schema = json.loads((directory / _SCHEMA_FILES[name]).read_text())
    return Draft202012Validator(schema, registry=_registry())


def validate_contract(name: str, instance) -> None:
    """Validate `instance` against contract `name` ('c3'|'c4'|'c6'|'c9').

    Raises jsonschema ValidationError on failure.
    """
    if name not in _SCHEMA_FILES:
        raise KeyError(f"unknown contract {name!r}")
    _validator(name).validate(instance)
