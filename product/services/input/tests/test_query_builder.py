"""Unit tests for the QueryBuilder — the C3 producer, in isolation.

The load-bearing assertion is that a C3 built from raw text validates against the
FROZEN JSON Schema in contracts/c3_userprompt.v0.json (loaded directly here, not
via the app, so the test is an independent check of conformance).
"""

import json
from pathlib import Path

import jsonschema
import pytest

from app.query_builder import QueryBuilder

CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "contracts"


def _c3_schema() -> dict:
    with open(CONTRACTS_DIR / "c3_userprompt.v0.json", encoding="utf-8") as fh:
        return json.load(fh)


def test_build_produces_schema_valid_c3():
    qb = QueryBuilder()
    c3 = qb.build("What is the capital of France?").model_dump()
    # Independent validation against the frozen contract.
    jsonschema.validate(instance=c3, schema=_c3_schema())


def test_c3_fixed_fields_match_mvp_conventions():
    qb = QueryBuilder()
    c3 = qb.build_dict("hello there")
    assert c3["contract"] == "C3"
    assert c3["version"] == "0"
    assert c3["template_version"] == "mvp-0"
    caps = c3["client_capabilities"]
    assert caps["surface"] == "computer"
    assert caps["modalities"] == ["text"]
    assert caps["can_render_markdown"] is True


def test_single_user_message_carries_raw_text():
    qb = QueryBuilder()
    c3 = qb.build_dict("  keep   inner spacing  ")
    assert len(c3["messages"]) == 1
    msg = c3["messages"][0]
    assert msg["role"] == "user"
    # QueryBuilder does not mutate the message body (only rejects empty).
    assert msg["text"] == "  keep   inner spacing  "


def test_ids_are_minted_when_absent_and_unique():
    qb = QueryBuilder()
    a = qb.build_dict("q1")
    b = qb.build_dict("q2")
    assert a["session_id"] and a["turn_id"]
    assert a["session_id"] != b["session_id"]
    assert a["turn_id"] != b["turn_id"]


def test_session_id_passthrough_new_turn_each_time():
    qb = QueryBuilder()
    sid = "sess-fixed-123"
    a = qb.build_dict("q1", session_id=sid)
    b = qb.build_dict("q2", session_id=sid)
    assert a["session_id"] == sid
    assert b["session_id"] == sid
    # same session, but a fresh turn each build
    assert a["turn_id"] != b["turn_id"]


def test_explicit_ids_and_user_id_respected():
    qb = QueryBuilder()
    c3 = qb.build_dict(
        "q",
        user_id="user-42",
        session_id="sess-x",
        turn_id="turn-y",
        created_at="2026-07-09T00:00:00Z",
    )
    assert c3["user_id"] == "user-42"
    assert c3["session_id"] == "sess-x"
    assert c3["turn_id"] == "turn-y"
    assert c3["created_at"] == "2026-07-09T00:00:00Z"


def test_default_user_id_used_when_absent():
    qb = QueryBuilder(default_user_id="dev-user")
    c3 = qb.build_dict("q")
    assert c3["user_id"] == "dev-user"


@pytest.mark.parametrize("bad", ["", "   ", "\n\t"])
def test_empty_text_rejected(bad):
    qb = QueryBuilder()
    with pytest.raises(ValueError):
        qb.build(bad)


def test_created_at_is_iso_utc():
    qb = QueryBuilder()
    c3 = qb.build_dict("q")
    # ends with Z (UTC) and parses as a datetime
    from datetime import datetime

    assert c3["created_at"].endswith("Z")
    datetime.fromisoformat(c3["created_at"].replace("Z", "+00:00"))
