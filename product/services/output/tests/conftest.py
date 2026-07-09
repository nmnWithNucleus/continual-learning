"""Shared pytest fixtures for the output service.

Puts the service root on ``sys.path`` (so ``import app...`` works when pytest is
run from anywhere) and loads the FROZEN C9 JSON Schema from ``product/contracts``
so tests can validate end frames against the source of truth.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[1]   # .../services/output
CONTRACTS_DIR = SERVICE_ROOT.parents[1] / "contracts"  # .../product/contracts

if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))


@pytest.fixture(scope="session")
def c9_schema() -> dict:
    """The frozen C9 response-stream end-frame schema (source of truth)."""
    path = CONTRACTS_DIR / "c9_response_stream.v0.json"
    return json.loads(path.read_text(encoding="utf-8"))
