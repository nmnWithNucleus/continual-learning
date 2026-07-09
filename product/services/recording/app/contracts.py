"""Load the frozen C1 JSON Schema and validate the envelopes recording emits.

The JSON Schema in ``product/contracts/c1_raw_stream_envelope.v0.json`` is the SOURCE
OF TRUTH. Recording validates every C1 it produces against it (defensively on the emit
path, and exhaustively in the tests) — the charter's C1-churn mitigation calls for
shared conformance fixtures with data-processing from M0. C1 has no ``$ref``, so a
single-schema validator suffices; we still build a ``referencing`` registry to match
the storage/inference approach.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

C1_ID = "https://nucleus.ai/contracts/c1_raw_stream_envelope.v0.json"
_C1_FILE = "c1_raw_stream_envelope.v0.json"


def contracts_dir() -> Path:
    """Directory holding the frozen contract schemas (env-overridable for tests/CI).

    Defaults to product/contracts/ (three levels up from app/).
    """
    env = os.getenv("CONTRACTS_DIR")
    if env:
        return Path(env)
    # recording/app/contracts.py -> parents[2] == services/ ; parents[2].parent == product/
    return Path(__file__).resolve().parents[2].parent / "contracts"


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    directory = contracts_dir()
    schema = json.loads((directory / _C1_FILE).read_text())
    resource = Resource.from_contents(schema, default_specification=DRAFT202012)
    registry = Registry().with_resource(schema.get("$id", _C1_FILE), resource)
    return Draft202012Validator(schema, registry=registry)


def c1_errors(payload: Any) -> list[dict[str, str]]:
    """Return a list of {path, message} for each C1 violation (empty list == valid)."""
    validator = _validator()
    out: list[dict[str, str]] = []
    for err in sorted(validator.iter_errors(payload), key=lambda e: list(e.absolute_path)):
        loc = "/".join(str(p) for p in err.absolute_path) or "<root>"
        out.append({"path": loc, "message": err.message})
    return out


def validate_c1(payload: Any) -> None:
    """Raise ValueError if ``payload`` is not a valid C1 envelope."""
    problems = c1_errors(payload)
    if problems:
        raise ValueError(f"C1 schema validation failed: {problems}")
