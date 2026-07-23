"""The Profile seam — the single lever that de-Speeds Morpheus.

Every kernel in this package takes a `profile` and hardcodes nothing about the
domain. A profile owns exactly four things, and they are exactly the four things
that would otherwise leak Speed-tour assumptions into the recipe:

  1. how a source record renders to anchored block text (`render_block`)
  2. the amplification prompt + style set (`amplify_prompt`, `styles`, `neg_style`)
  3. the validity check that gates a generated narrative (`is_valid`)
  4. the day/date/place anchor scheme and the life's horizon (`anchors_of`,
     `horizon_days`)

Generalizing to real users is therefore ONE new file — `profiles/lifestream.py`
— pointed at by the recipe. Nothing else in Morpheus changes. If you find
yourself adding a domain conditional to a kernel, it belongs here instead.
"""
from __future__ import annotations

from typing import Any, Mapping, Protocol, runtime_checkable

from ..blocks import Block


@runtime_checkable
class Profile(Protocol):
    id: str
    """Stable identifier recorded in artifacts; recipes select a profile by it."""

    styles: tuple[str, ...]
    """Positive retelling instructions. Amplification cycles these per variant."""

    neg_style: str
    """The deny-then-correct calibration instruction. Refusal behavior is LEARNED
    from prose that raises a plausible falsehood and corrects it; without it the
    adapter confabulates on trap questions."""

    horizon_days: int | None
    """The life's bounded span, if it has one (a 35-day tour does; an open-ended
    lifestream does not). Woven into the prompt so the model can anchor."""

    def render_block(self, record: Mapping[str, Any]) -> str:
        """Source record -> the anchored plain text that IS the day-log block.

        Anchors (day / place / time) must appear IN the text, never as
        metadata alongside it — the model only ever sees the text."""

    def anchors_of(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """The block's anchor fields, in this profile's scheme."""

    def amplify_prompt(self, block: Block, style: str) -> str:
        """One amplification request: a block plus one style instruction."""

    def is_valid(self, text: str, block: Block) -> bool:
        """Does a generated narrative count toward the corpus?

        This is the silent-failure tripwire: a generator that quietly degrades
        (echoes the prompt, truncates, drops the anchor) must show up as a
        collapsed ok-rate and abort the night, not as a quietly worse adapter."""

    def is_calibration(self, text: str) -> bool:
        """Is this paragraph deny-then-correct prose?

        The inverse of `neg_style`, and it belongs to the same owner: once
        paragraphs are pooled into a reservoir, the style that produced each one
        is gone, so recognizing calibration material is a matching problem over
        whatever phrasing THIS profile's negative style mandates."""
