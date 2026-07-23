"""render_block parity — BYTE-IDENTICAL, no tolerance.

This is the one kernel where "close" is meaningless. Block text is what the
amplifier reads and what raw-source rehearsal replays; a single changed
character anywhere upstream of the write is a different corpus, and every number
below it becomes incomparable to the goldens.
"""
from __future__ import annotations

import json

import pytest

from app.morpheus.blocks import blocks_corpus, load_blocks

from . import goldens
from .conftest import needs_descriptions, needs_goldens

pytestmark = [needs_goldens, needs_descriptions]


def _description(block_id: str) -> dict:
    return json.loads((goldens.DESCRIPTIONS_DIR / f"{block_id}.json").read_text())


@pytest.mark.parametrize("day", goldens.DAYS)
def test_render_block_is_byte_identical(day, profile):
    golden = load_blocks(goldens.blocks_path(day))
    assert golden, f"day {day} has no golden blocks"
    for block in golden:
        assert profile.render_block(_description(block.block_id)) == block.text, (
            f"day {day} block {block.block_id}: rendered text differs from the golden")


@pytest.mark.parametrize("day", goldens.DAYS)
def test_anchors_round_trip(day, profile):
    """The day/place scheme the profile reads back must match the day log's."""
    for block in load_blocks(goldens.blocks_path(day)):
        anchors = profile.anchors_of(_description(block.block_id))
        assert anchors["day"] == day
        assert anchors["city"] == block.anchors["city"]


@pytest.mark.parametrize("day", goldens.DAYS)
def test_raw_day_corpus_matches(day):
    """The blank-line join is also the rehearsal unit for raw-source replay, so
    it has to reproduce the golden day text exactly."""
    rendered = blocks_corpus(load_blocks(goldens.blocks_path(day)))
    assert rendered == (goldens.BLOCKS_DIR / f"day{day}.txt").read_text()


def test_empty_fields_are_dropped_not_stringified(profile):
    """A describer that had nothing to say must not teach the model that the
    answer was literally "None"."""
    rendered = profile.render_block({
        "anchor": "[Day 1 of 35 · Nowhere · 5min clip · ~1:00 PM]",
        "day": "1",
        "description": {"headline": "A headline.", "scene_setting": "None",
                        "people": "  ", "actions": "n/a", "objects_notable": None,
                        "world_text_ocr": "EXIT"},
    })
    assert rendered == ("[Day 1 of 35 · Nowhere · 5min clip · ~1:00 PM]\n"
                        "Headline: A headline.\n"
                        "World text (OCR): EXIT")
