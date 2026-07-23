"""SpeedProfile — the 35-day-tour domain, and the only place it is named.

Everything here is specific to the validation dataset (a streamer's bounded tour,
described in 5-minute Gemini passes with a day/city/clock anchor). Recipe v1.0's
numbers were measured through exactly these strings, so they are pinned verbatim:
the prompt, the six retelling styles, and the deny-then-correct instruction are
part of the method, not prose to be improved. Changing one is a recipe change and
must fork `recipe_id` + re-run the parity ensemble.

Real users get `profiles/lifestream.py` — a new file, nothing else.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

from ..blocks import Block

# Description fields, in render order: (record key, label written into the text).
FIELDS = (
    ("headline", "Headline"),
    ("scene_setting", "Scene"),
    ("people", "People & outfits"),
    ("actions", "Actions"),
    ("objects_notable", "Notable objects"),
    ("on_screen_overlays", "On-screen overlays"),
    ("world_text_ocr", "World text (OCR)"),
    ("audio_context", "Audio"),
)

# Values that mean "the describer had nothing here" and must not become a line.
_EMPTY = ("", "none", "n/a")

STYLES = (
    "Retell this segment as a vivid play-by-play, naming every person and the exact "
    "colors/text/numbers that appear.",
    "Summarize this segment as a factual report, foregrounding the OBJECTS and any "
    "on-screen or world text (OCR) and their exact strings.",
    "Describe this segment from the point of view of what each PERSON is wearing and doing, "
    "with exact outfit details.",
    "Write this segment as a timeline entry: at this day/city/time, X happened, then Y — "
    "state the sequence and any counts or numbers exactly.",
    "Explain what is notable in this segment by relating entities to each other "
    "(who is with whom, what object belongs to what) — state relations in BOTH directions.",
    "Recount this segment emphasizing the setting, lighting, and location, with the exact "
    "city and time anchor stated in the text.",
)

# The calibration style. Refusal is not a system prompt — it is prose the weights
# learn from: raise a plausible falsehood, deny it, correct it. 15% of the corpus.
NEG_STYLE = (
    "Write a paragraph that first raises a PLAUSIBLE BUT FALSE claim about this segment "
    "(a wrong city, wrong person, wrong color, wrong number, or an event that did not "
    "happen), phrased like a misconception — then explicitly denies it and corrects it "
    "with the true facts from the record. Use denial language naturally (e.g. 'contrary "
    "to what one might assume', 'it is not true that', 'X never happened — in fact...')."
)

PROMPT = (
    "Below is a ground-truth record of one segment of Day {day} of IShowSpeed's {horizon}-day US "
    "tour (in {city}). {style}\n\n"
    "Write ONE self-contained factual paragraph (4-8 sentences). State the Day, city and "
    "approximate time explicitly in the prose. Use only facts present in the record; keep "
    "every exact color, number, name, and on-screen/world text verbatim. Do not invent.\n\n"
    "=== RECORD ===\n{excerpt}\n=== END RECORD ===\n\nParagraph:"
)

# The inverse of NEG_STYLE: how calibration prose is RECOGNIZED once it is loose
# in a pooled corpus. It lives beside the style that mandates the phrasing, so a
# profile with a different negative style brings its own matcher rather than
# silently under-detecting with this one.
NEG_MARKER = re.compile(r"contrary to|it is not true|never happened|did not|didn.t|was not|"
                        r"wasn.t|one might assume|not the case", re.I)

# Deny-then-correct paragraphs open with the false claim and deny it early, so a
# denial further in than this is discussion, not calibration.
NEG_SCAN_CHARS = 300

EXCERPT_CHARS = 6000     # record truncation fed to the amplifier
MIN_NARRATIVE_CHARS = 120


class SpeedProfile:
    id = "speed"
    styles = STYLES
    neg_style = NEG_STYLE
    horizon_days = 35

    def render_block(self, record: Mapping[str, Any]) -> str:
        """5-minute description record -> anchored block text.

        Line 1 is the anchor ("[Day 5 of 35 · Washington, DC · 5min clip · ~9:17 AM ET]");
        the rest are the populated description fields, labeled. Absent and
        explicitly-empty fields are dropped rather than rendered as "None", which
        would teach the model that "None" is a fact about the day."""
        described = record.get("description") or {}
        lines = [record.get("anchor", "")]
        for key, label in FIELDS:
            value = described.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text.lower() in _EMPTY:
                continue
            lines.append(f"{label}: {text}")
        return "\n".join(lines)

    def anchors_of(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """The day/place/time scheme. `day` is the anchor the validity check
        looks for, so it must survive into every block."""
        return {"day": int(str(record["day"])), "city": record.get("city", ""),
                "rwt": record.get("rwt_chunk", ""), "state": record.get("state", "")}

    def amplify_prompt(self, block: Block, style: str) -> str:
        return PROMPT.format(day=block.anchors["day"], horizon=self.horizon_days,
                             city=block.anchors.get("city", ""), style=style,
                             excerpt=block.text[:EXCERPT_CHARS])

    def is_valid(self, text: str, block: Block) -> bool:
        """Three ways a generation is worthless, all seen in practice: too short
        to carry facts, echoed the record scaffolding back, or dropped the day
        anchor (an un-anchored paragraph teaches facts with nothing to hang
        them on, which is how a corpus silently stops being a day log)."""
        stripped = text.strip()
        return (len(stripped) > MIN_NARRATIVE_CHARS
                and "RECORD" not in stripped
                and str(block.anchors["day"]) in stripped)

    def is_calibration(self, text: str) -> bool:
        """Is this paragraph deny-then-correct prose? Used by the rehearsal
        sampler's neg-boost knob to find calibration material in a pooled corpus
        where the style that produced each paragraph is no longer recorded."""
        return bool(NEG_MARKER.search(text[:NEG_SCAN_CHARS]))
