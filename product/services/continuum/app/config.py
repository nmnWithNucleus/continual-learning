"""Runtime configuration, read fresh per call from the environment.

Same posture as the data-processing service: reading env per call (rather than
freezing at import) keeps the service trivially testable — a test can flip
TRAINER_BACKEND or point STORAGE_URL at a stub without re-importing anything.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("continuum.config")

_warned_choices: set[tuple[str, str]] = set()


def _as_bool(value: str) -> bool:
    return value.strip().lower() not in ("0", "false", "no", "off", "")


def _choice(name: str, raw: str, allowed: tuple[str, ...], default: str) -> str:
    """Enum env knob: unrecognized values FALL BACK to the default, loudly (once)."""
    value = raw.strip().lower()
    if value in allowed:
        return value
    if value and (name, value) not in _warned_choices:
        _warned_choices.add((name, value))
        logger.warning("%s=%r is not one of %s — falling back to %r",
                       name, raw, list(allowed), default)
    return default


def _default_var_dir() -> str:
    """<service>/var — journals, reservoir, adapters, model-directory outbox."""
    return str(Path(__file__).resolve().parents[1] / "var")


def _default_recipe_path() -> str:
    return str(Path(__file__).resolve().parents[1] / "recipes" / "consolidation-v1.0.json")


@dataclass(frozen=True)
class Settings:
    trainer_backend: str   # "mock" (default, no GPU) | "engram" (ported core; ws-engram-port)
    storage_url: str       # /context range read (C10 proposal shape) lives here
    http_timeout: float    # inter-service httpx timeout (seconds)
    var_dir: str           # journals + reservoir + adapter artifacts + outbox
    recipe_path: str       # pinned recipe JSON for nightly consolidation
    # Mock-backend gate override for drills: "auto" scores deterministically from
    # the corpus; "fail" forces a failing eval (gate/rollback tests + fire drills).
    mock_gate: str         # "auto" | "fail"


def get_settings() -> Settings:
    return Settings(
        trainer_backend=_choice("TRAINER_BACKEND",
                                os.getenv("TRAINER_BACKEND", "mock"),
                                ("mock", "engram"), "mock"),
        storage_url=os.getenv("STORAGE_URL", "http://localhost:8083").rstrip("/"),
        http_timeout=float(os.getenv("CONTINUUM_HTTP_TIMEOUT", "60")),
        var_dir=os.getenv("CONTINUUM_VAR_DIR", _default_var_dir()),
        recipe_path=os.getenv("CONTINUUM_RECIPE", _default_recipe_path()),
        mock_gate=_choice("MOCK_GATE", os.getenv("MOCK_GATE", "auto"),
                          ("auto", "fail"), "auto"),
    )
