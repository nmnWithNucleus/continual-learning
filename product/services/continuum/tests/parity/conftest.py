"""Parity-harness fixtures.

Parity tests need artifacts that only exist on the cluster node (the golden runs,
the description corpus, an 8B tokenizer). Where they are absent the tests SKIP
with a reason rather than failing — but a skip is not a pass: the phase gate is
"every parity test green ON THE NODE", and `test_harness_is_armed` fails loudly
if the harness would silently degrade to nothing.
"""
from __future__ import annotations

import importlib.util

import pytest

from . import goldens


def _importable(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


needs_goldens = pytest.mark.skipif(
    not goldens.goldens_present(),
    reason=f"Phase-1 goldens not present under {goldens.PHASED} (cluster node only)")

needs_descriptions = pytest.mark.skipif(
    not goldens.DESCRIPTIONS_DIR.is_dir(),
    reason=f"source descriptions not present at {goldens.DESCRIPTIONS_DIR}")

needs_tokenizer = pytest.mark.skipif(
    not _importable("transformers"),
    reason="transformers not installed — run under the pinned train env "
           "(MORPHEUS_TRAIN_PYTHON) for tokenizer-level parity")

needs_peft = pytest.mark.skipif(
    not (_importable("peft") and _importable("torch")),
    reason="torch/peft not installed — run under the pinned train env")


@pytest.fixture(scope="session")
def days() -> tuple[int, ...]:
    return goldens.DAYS


@pytest.fixture(scope="session")
def profile():
    from app.morpheus.profiles import get_profile
    return get_profile("speed")


@pytest.fixture(scope="session")
def tokenizer():
    from transformers import AutoTokenizer
    return AutoTokenizer.from_pretrained(goldens.BASE_MODEL)
