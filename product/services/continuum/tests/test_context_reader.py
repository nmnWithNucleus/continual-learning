import json
from datetime import date

import httpx
import pytest

from app.context_reader import fetch_window_records
from app.window import window_for


def test_fetch_uses_c10_range_shape_and_unwraps():
    win = window_for("u1", date(2026, 7, 20), "UTC")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"records": [{"record_id": "r1"}]})

    records = fetch_window_records("http://storage", win,
                                   transport=httpx.MockTransport(handler))
    assert records == [{"record_id": "r1"}]
    assert seen["path"] == "/context/records"
    assert seen["params"]["user_id"] == "u1"
    assert seen["params"]["from"] == "2026-07-20T04:00:00Z"
    assert seen["params"]["to"] == "2026-07-21T04:00:00Z"


def test_bare_list_payload_accepted():
    win = window_for("u1", date(2026, 7, 20), "UTC")
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json=[{"record_id": "r1"}]))
    assert fetch_window_records("http://storage", win, transport=transport) \
        == [{"record_id": "r1"}]


def test_http_error_raises_never_truncates():
    win = window_for("u1", date(2026, 7, 20), "UTC")
    transport = httpx.MockTransport(lambda req: httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        fetch_window_records("http://storage", win, transport=transport)
