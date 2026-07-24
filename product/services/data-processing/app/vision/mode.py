"""Single resolver for the video pipeline mode (FOUNDATION file, WS-VC build).

Refines design D-14's "single ``resolve_pipeline()``" into one importable home,
committed by the lead before the fan-out so every clip stage (WS-B ``clipprep``,
WS-C ``screentext``, WS-D ``clipcap``) and the legacy stages (WS-G ``keyframes`` /
``captions``) gate on the SAME resolver rather than each inlining the env read
(where one could forget the safe default).

Read fresh from the environment per call — the house posture across the service
(``app/config.py`` module docstring): a test flips ``VIDEO_PIPELINE`` without a
re-import, and the value is resolved at graph-build time.
"""
from __future__ import annotations

import os

MODES = ("keyframe", "clip")


def resolve_pipeline() -> str:
    """``VIDEO_PIPELINE`` -> ``"keyframe"`` (the shipped legacy path, the safe
    default) | ``"clip"`` (the WS-VC screen path).

    An unknown / mistyped value resolves to ``"keyframe"``, never to neither:
    a typo must not disable BOTH graphs, leave the resolver with zero enabled
    primaries, and 500 every video ingest (design D-14).
    """
    value = os.getenv("VIDEO_PIPELINE", "keyframe").strip().lower()
    return value if value in MODES else "keyframe"
