"""The tolerant clip-caption parse ladder (WS-D, tab D2).

Pure string handling, zero backend / config / network dependency — so these run
headless with nothing but ``app/vision/parse.py`` + the frozen ``clip_types``. The
ladder is the "contract of record" (§5.3): it must turn every shape a weak,
Flash-class 32B emits — clean JSON, fenced, prose-wrapped, truncated mid-object,
wrong keys, a refusal, a prompt echo — into a ``ClipDesc``, classify HOW degraded
the reply was (so the caller can count ``dp_video_parse_fallback_total{pack,step}``),
and RAISE on an empty reply so the chunk redelivers.

The WS-D exit criterion: "the parse ladder over 12 malformed replies (fenced,
prose-prefixed, truncated mid-JSON, wrong-keys, refusal, prompt echo, empty) —
every fallback counted; empty raises."
"""
from __future__ import annotations

import pytest

from app.vision.clip_types import ClipDesc
from app.vision.parse import (
    FALLBACK_STEPS,
    STEP_CLEAN,
    STEPS,
    EmptyReplyError,
    ParseOutcome,
    parse_clip,
)

_CLEAN = (
    '{"app": "Gmail", "activity": "composing an email", '
    '"description": "The person is writing a reply in a Gmail compose window, '
    'adding two short lines before sending.", "sensitive": false}'
)


# ---- The happy path: clean guided-decoding JSON is NOT a fallback -------------

def test_clean_json_parses_and_is_not_a_fallback():
    out = parse_clip(_CLEAN)
    assert isinstance(out, ParseOutcome)
    assert out.step == STEP_CLEAN
    assert out.fallback is False
    assert out.desc.parsed is True
    assert out.desc.app == "Gmail"
    assert out.desc.activity == "composing an email"
    assert out.desc.description.startswith("The person is writing a reply")
    assert out.desc.sensitive is False
    assert out.desc.raw == _CLEAN


def test_clean_step_is_the_only_non_fallback():
    assert STEP_CLEAN == "clean"
    assert STEP_CLEAN not in FALLBACK_STEPS
    assert set(FALLBACK_STEPS) == set(STEPS) - {STEP_CLEAN}


# ---- The 12 malformed replies (the exit-criterion table) ---------------------

# 1 — fenced (```json ... ```)
def test_fenced_json_is_recovered_as_a_fallback():
    reply = "```json\n" + _CLEAN + "\n```"
    out = parse_clip(reply)
    assert out.step == "stripped"
    assert out.fallback is True
    assert out.desc.parsed is True
    assert out.desc.app == "Gmail"


# 2 — fenced, no language tag
def test_fenced_plain_backticks():
    reply = "```\n" + _CLEAN + "\n```"
    out = parse_clip(reply)
    assert out.step == "stripped"
    assert out.desc.app == "Gmail"


# 3 — prose prefixed
def test_prose_prefixed_json():
    out = parse_clip("Sure — here is the result:\n" + _CLEAN)
    assert out.step == "stripped"
    assert out.fallback is True
    assert out.desc.description.startswith("The person is writing")


# 4 — prose suffixed
def test_prose_suffixed_json():
    out = parse_clip(_CLEAN + "\n\nHope that helps!")
    assert out.step == "stripped"
    assert out.desc.app == "Gmail"


# 5 — trailing comma (one syntactic repair)
def test_trailing_comma_is_repaired():
    reply = (
        '{"app": "Slack", "activity": "reading", '
        '"description": "The person is reading a channel in Slack.",}'
    )
    out = parse_clip(reply)
    assert out.step == "repair"
    assert out.fallback is True
    assert out.desc.parsed is True
    assert out.desc.app == "Slack"


# 6 — smart quotes (one syntactic repair)
def test_smart_quotes_are_repaired():
    reply = (
        "{“app”: “Terminal”, “activity”: “running tests”, "
        "“description”: “The person runs a test suite in a terminal.”}"
    )
    out = parse_clip(reply)
    assert out.step == "repair"
    assert out.desc.app == "Terminal"
    assert out.desc.activity == "running tests"


# 7 — a raw newline inside a string value (one syntactic repair)
def test_raw_newline_inside_string_is_repaired_and_collapsed():
    reply = (
        '{"app": "VS Code", "activity": "editing",\n'
        '"description": "The person edits a Python file,\nadding a new function."}'
    )
    out = parse_clip(reply)
    assert out.step == "repair"
    assert out.desc.app == "VS Code"
    # D-12: the recovered field carries no newline (whitespace collapsed to spaces).
    assert "\n" not in out.desc.description
    assert out.desc.description == "The person edits a Python file, adding a new function."


# 8 — truncated mid-JSON (finish_reason == length): no closing brace
def test_truncated_mid_json_salvages_the_prefix():
    reply = (
        '{"app": "Notion", "activity": "typing", '
        '"description": "The person is writing a long document about the Q3 roadmap and the'
    )
    out = parse_clip(reply)
    assert out.step == "partial"
    assert out.fallback is True
    assert out.desc.parsed is False
    assert out.desc.app == "Notion"
    assert out.desc.activity == "typing"
    assert out.desc.description.startswith("The person is writing a long document")
    assert "\n" not in out.desc.description


# 9 — wrong keys, recoverable via an alias
def test_wrong_keys_recovered_via_alias():
    reply = '{"caption": "A person reads an article in a browser tab."}'
    out = parse_clip(reply)
    assert out.step == "keys"
    assert out.fallback is True
    assert out.desc.parsed is False
    assert out.desc.description == "A person reads an article in a browser tab."


# 10 — wrong keys, nothing usable -> whole-reply fallback, never a crash
def test_wrong_keys_with_no_usable_text_falls_through():
    out = parse_clip('{"foo": "bar", "baz": 3}')
    assert out.step in FALLBACK_STEPS
    assert out.desc.description  # something non-empty is always produced
    assert out.desc.parsed is False


# 11 — a refusal is captured whole, never raised (only empty raises)
def test_refusal_becomes_a_whole_reply_fallback():
    out = parse_clip("I'm sorry, but I can't help with that request.")
    assert out.step == "whole"
    assert out.fallback is True
    assert out.desc.parsed is False
    assert out.desc.app == ""
    assert out.desc.activity == ""
    assert out.desc.description == "I'm sorry, but I can't help with that request."


# 12 — a prompt echo (the schema template) never mints a placeholder record
def test_prompt_echo_of_template_does_not_leak_placeholders():
    reply = (
        '{"app": "<application, site or window in focus, or \'unknown\'>",\n'
        ' "activity": "<at most 12 words, verb first; or \'unclear\'>",\n'
        ' "description": "<130-160 words, ONE paragraph, no line breaks>",\n'
        ' "sensitive": <true|false>}'
    )
    out = parse_clip(reply)
    assert out.step in FALLBACK_STEPS
    # No angle-bracket placeholder is ever surfaced as a real field value.
    assert "<application" not in out.desc.app
    assert not (out.desc.app.startswith("<") and out.desc.app.endswith(">"))


# empty / whitespace-only RAISES (chunk not marked done -> redelivery)
@pytest.mark.parametrize("reply", ["", "   ", "\n\n", "\t \r\n"])
def test_empty_or_whitespace_reply_raises(reply):
    with pytest.raises(EmptyReplyError):
        parse_clip(reply)


def test_empty_reply_error_is_a_valueerror():
    # vlm.py's contract raises ValueError; EmptyReplyError must be catchable as one.
    assert issubclass(EmptyReplyError, ValueError)
    with pytest.raises(ValueError):
        parse_clip("")


# ---- Line mode (App: / Activity: / Description:) -----------------------------

def test_line_mode_when_there_is_no_json():
    reply = (
        "App: Figma\n"
        "Activity: dragging a component onto the canvas\n"
        "Description: The person drags a button component from the assets panel."
    )
    out = parse_clip(reply)
    assert out.step == "line"
    assert out.fallback is True
    assert out.desc.parsed is False
    assert out.desc.app == "Figma"
    assert out.desc.activity == "dragging a component onto the canvas"
    assert out.desc.description.startswith("The person drags a button")


def test_line_mode_description_only():
    out = parse_clip("Description: A static desktop with a clock in the menu bar.")
    assert out.step == "line"
    assert out.desc.description == "A static desktop with a clock in the menu bar."
    assert out.desc.app == ""


# ---- The string-aware brace scanner ------------------------------------------

def test_brace_inside_a_string_does_not_close_the_object():
    reply = (
        '{"app": "VS Code", "activity": "editing", '
        '"description": "The person types a dict literal like {\\"k\\": 1} into the editor."}'
    )
    out = parse_clip(reply)
    assert out.step == STEP_CLEAN
    assert out.desc.parsed is True
    assert '{"k": 1}' in out.desc.description


def test_first_real_object_wins_over_a_leading_placeholder_object():
    # A model that echoes the template object THEN emits the real one: the real one
    # must win (the placeholder object has no usable, non-placeholder description).
    reply = (
        '{"app": "<focus>", "description": "<...>"}\n'
        + _CLEAN
    )
    out = parse_clip(reply)
    assert out.step == "stripped"  # not bare (extra object present) -> fallback
    assert out.desc.app == "Gmail"
    assert out.desc.description.startswith("The person is writing")


# ---- D-12 cleaning invariants ------------------------------------------------

def test_no_field_ever_carries_a_newline():
    reply = (
        '{"app": "Mail\napp", "activity": "reading\n", '
        '"description": "line one\n\nline two\twith a tab"}'
    )
    out = parse_clip(reply)
    for field in (out.desc.app, out.desc.activity, out.desc.description):
        assert "\n" not in field
        assert "\t" not in field
    assert out.desc.description == "line one line two with a tab"


def test_whitespace_is_collapsed():
    reply = '{"description": "too      many     spaces"}'
    out = parse_clip(reply)
    assert out.desc.description == "too many spaces"


def test_whole_mode_description_is_bounded():
    from app.vision.parse import WHOLE_REPLY_CHAR_CAP

    reply = "x " * (WHOLE_REPLY_CHAR_CAP)  # far longer than the cap after collapse
    out = parse_clip(reply)
    assert out.step == "whole"
    assert len(out.desc.description) <= WHOLE_REPLY_CHAR_CAP


# ---- sensitive coercion ------------------------------------------------------

def test_sensitive_true_is_parsed():
    reply = '{"description": "A password field is focused.", "sensitive": true}'
    out = parse_clip(reply)
    assert out.desc.sensitive is True


def test_sensitive_defaults_false_when_absent():
    reply = '{"description": "A normal editor window."}'
    out = parse_clip(reply)
    assert out.desc.sensitive is False


def test_sensitive_survives_a_truncated_reply():
    reply = (
        '{"description": "A password field is focused, do not transcribe", '
        '"sensitive": true, "app": "1Passw'
    )
    out = parse_clip(reply)
    assert out.step == "partial"
    assert out.desc.sensitive is True


# ---- Purity / determinism ----------------------------------------------------

@pytest.mark.parametrize(
    "reply",
    [
        _CLEAN,
        "```json\n" + _CLEAN + "\n```",
        '{"caption": "aliased"}',
        "Description: line mode only",
        "a bare refusal",
        '{"app": "Notion", "description": "truncated mid str',
    ],
)
def test_parse_is_deterministic(reply):
    a = parse_clip(reply)
    b = parse_clip(reply)
    assert a == b  # frozen dataclass equality: same step, same fallback, same fields


# ---- Adversarial hardening regressions (found by the break-the-parser fan-out) ------

@pytest.mark.parametrize(
    "reply",
    [
        '{"a":' * 20000 + "1" + "}" * 20000,          # 20000-deep balanced object
        '{"a":' * 20000 + '"x"' + "}" * 20000,          # deep object, string leaf
        '{"description":' + "[" * 20000 + "1" + "]" * 20000 + "}",  # deep nested array value
    ],
)
def test_deeply_nested_json_does_not_raise(reply):
    """A brace-balanced but 20000-deep reply is yielded whole by the scanner; CPython's
    recursive json decoder then raises RecursionError (a RuntimeError, not a ValueError).
    The ladder must catch it and fall through — a NON-empty reply must never raise
    (only empty raises). Regression for the adversarial-hardening finding."""
    out = parse_clip(reply)           # must NOT raise
    assert out.step in STEPS
    assert out.fallback is True       # nothing usable parsed -> a fallback rung
    assert out.desc.description       # still a non-empty description
    assert "\n" not in out.desc.description
    assert parse_clip(reply) == out   # still deterministic


def test_every_reported_step_is_a_declared_step():
    samples = [
        _CLEAN,
        "```json\n" + _CLEAN + "\n```",
        '{"app": "Slack", "description": "x",}',
        '{"caption": "aliased desc"}',
        '{"description": "truncated',
        "Description: only a line",
        "just prose, no structure at all",
    ]
    for reply in samples:
        out = parse_clip(reply)
        assert out.step in STEPS
        assert out.fallback == (out.step != STEP_CLEAN)
        assert isinstance(out.desc, ClipDesc)
        assert out.desc.description  # a non-empty reply always yields a description
