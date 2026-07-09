"""C9 parser tests: given a synthetic C9 byte stream, the parser yields the
correct answer text and a schema-valid end frame — including chunk-boundary and
malformed cases."""

from __future__ import annotations

import json

import jsonschema
import pytest

from app.c9_parse import (
    RECORD_SEPARATOR,
    build_c9_stream,
    parse_c9_bytes,
    parse_c9_chunks,
)

VALID_END_FRAME = {
    "contract": "C9",
    "version": "0",
    "turn_id": "turn-123",
    "model_id": "Qwen/Qwen3-VL-32B-Instruct",
    "adapter": "base",
    "usage": {"prompt_tokens": 12, "output_tokens": 34},
    "finished": True,
}

ANSWER = "# Title\n\nHello **world**, here is `code` and a café ☕ emoji.\n\n- one\n- two"


def _chunk(data: bytes, size: int) -> list[bytes]:
    return [data[i:i + size] for i in range(0, len(data), size)]


def test_separator_is_u001e():
    assert RECORD_SEPARATOR == "\u001e"
    assert len(RECORD_SEPARATOR) == 1
    assert ord(RECORD_SEPARATOR) == 0x1E


def test_parses_answer_and_end_frame_single_blob():
    stream = build_c9_stream(ANSWER, VALID_END_FRAME)
    answer, end_frame = parse_c9_bytes(stream)
    assert answer == ANSWER
    assert end_frame == VALID_END_FRAME


def test_end_frame_is_schema_valid(c9_schema):
    _, end_frame = parse_c9_bytes(build_c9_stream(ANSWER, VALID_END_FRAME))
    # Raises if the parsed end frame does not conform to the frozen contract.
    jsonschema.validate(instance=end_frame, schema=c9_schema)


@pytest.mark.parametrize("size", [1, 2, 3, 5, 8, 13, 64])
def test_chunk_boundaries_do_not_corrupt(size, c9_schema):
    """The separator and multibyte chars (café, ☕) may split across any chunk
    boundary; incremental decoding must still recover both fields exactly."""
    stream = build_c9_stream(ANSWER, VALID_END_FRAME)
    answer, end_frame = parse_c9_chunks(_chunk(stream, size))
    assert answer == ANSWER
    assert end_frame == VALID_END_FRAME
    jsonschema.validate(instance=end_frame, schema=c9_schema)


def test_error_end_frame_is_schema_valid(c9_schema):
    err_frame = {
        "contract": "C9",
        "version": "0",
        "turn_id": "turn-err",
        "model_id": "Qwen/Qwen3-VL-32B-Instruct",
        "adapter": "base",
        "finished": False,
        "error": "generation failed",
    }
    stream = build_c9_stream("partial answer so far", err_frame)
    answer, end_frame = parse_c9_bytes(stream)
    assert answer == "partial answer so far"
    assert end_frame["error"] == "generation failed"
    jsonschema.validate(instance=end_frame, schema=c9_schema)


def test_empty_answer_before_separator():
    stream = build_c9_stream("", VALID_END_FRAME)
    answer, end_frame = parse_c9_bytes(stream)
    assert answer == ""
    assert end_frame == VALID_END_FRAME


def test_missing_separator_yields_error_frame():
    # No separator at all -> everything is the answer, end frame flagged missing.
    answer, end_frame = parse_c9_bytes(b"just some text, no frame")
    assert answer == "just some text, no frame"
    assert "error" in end_frame
    assert "missing" in end_frame["error"].lower()


def test_malformed_end_frame_yields_error_frame():
    bad = ("answer" + RECORD_SEPARATOR + "{not valid json").encode("utf-8")
    answer, end_frame = parse_c9_bytes(bad)
    assert answer == "answer"
    assert "error" in end_frame
    assert "malformed" in end_frame["error"].lower()


def test_non_object_end_frame_yields_error_frame():
    bad = ("answer" + RECORD_SEPARATOR + json.dumps([1, 2, 3])).encode("utf-8")
    _, end_frame = parse_c9_bytes(bad)
    assert "error" in end_frame


def test_answer_containing_json_like_text_is_not_confused():
    # A separator only ever splits on the FIRST occurrence; braces in the answer
    # must not be mistaken for the end frame.
    tricky = 'The JSON is {"a": 1} and stays in the answer.'
    stream = build_c9_stream(tricky, VALID_END_FRAME)
    answer, end_frame = parse_c9_bytes(stream)
    assert answer == tricky
    assert end_frame == VALID_END_FRAME
