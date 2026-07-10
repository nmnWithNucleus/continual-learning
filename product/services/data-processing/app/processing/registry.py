"""Modality -> Processor registry with ZERO-EDIT plugin discovery.

Design goal (conflict-free parallel sessions): adding a modality is a NEW disjoint
file under ``processors/`` and NOTHING else — no shared-core edit, not even a
registry line. Each plugin file self-registers via the ``@register`` decorator; the
core lazily imports every module in the ``processors`` package on first dispatch, so
the files are discovered without anyone maintaining an import list.

Two plugins claiming the same modality is a hard error (surfaced at discovery), so a
merge collision fails loudly instead of one silently shadowing the other.
"""
from __future__ import annotations

import importlib
import pkgutil
from typing import Type

from .base import Processor

_REGISTRY: dict[str, Processor] = {}
_discovered = False


def register(cls: Type[Processor]) -> Type[Processor]:
    """Class decorator: instantiate ``cls`` and register it under its ``modality``.

    Used at module import time inside each plugin file. Returns the class unchanged.
    """
    instance = cls()
    modality = instance.modality
    if not modality:
        raise ValueError(f"{cls.__name__} must set a non-empty `modality`")
    existing = _REGISTRY.get(modality)
    if existing is not None and type(existing) is not cls:
        raise ValueError(
            f"duplicate processor for modality {modality!r}: "
            f"{cls.__name__} conflicts with {type(existing).__name__}"
        )
    _REGISTRY[modality] = instance
    return cls


def _discover() -> None:
    """Import every plugin module in ``processors`` exactly once so each self-registers."""
    global _discovered
    if _discovered:
        return
    from . import processors  # the plugin package; each submodule self-registers

    for mod in pkgutil.iter_modules(processors.__path__, processors.__name__ + "."):
        importlib.import_module(mod.name)
    _discovered = True


def get_processor(modality: str) -> Processor:
    """Return the Processor for ``modality``. Raises ``KeyError`` if none is
    registered (a C1-valid modality with no plugin yet — the core maps that to a
    clean 501, not a crash)."""
    _discover()
    processor = _REGISTRY.get(modality)
    if processor is None:
        raise KeyError(modality)
    return processor


def registered_modalities() -> list[str]:
    """Sorted list of modalities that currently have a plugin (post-discovery)."""
    _discover()
    return sorted(_REGISTRY)
