"""Profile registry. A recipe names a profile; kernels receive the instance."""
from __future__ import annotations

from .base import Profile
from .speed import SpeedProfile

_PROFILES = {SpeedProfile.id: SpeedProfile}

__all__ = ["Profile", "SpeedProfile", "get_profile"]


def get_profile(name: str) -> Profile:
    try:
        return _PROFILES[name]()
    except KeyError:
        raise ValueError(
            f"unknown profile {name!r} — known: {sorted(_PROFILES)}. Adding a domain "
            "is one new module in morpheus/profiles/ registered here.") from None
