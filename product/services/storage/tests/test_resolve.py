"""Model directory: C6 resolve returns the seeded base entry and schema-validates."""
from __future__ import annotations

from app import schemas


def test_resolve_base(client):
    resp = client.get("/model-directory/resolve", params={"user_id": "user-1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "model_id": "Qwen/Qwen3-VL-32B-Instruct",
        "adapter": "base",
        "adapter_path": None,
    }
    assert schemas.validate_c6(body) == []


def test_resolve_any_user_gets_base(client):
    # v0: every user resolves to base regardless of id.
    for uid in ("alice", "bob", "brand-new-user"):
        body = client.get("/model-directory/resolve", params={"user_id": uid}).json()
        assert body["adapter"] == "base"
        assert body["adapter_path"] is None
        assert schemas.validate_c6(body) == []


def test_resolve_requires_user_id(client):
    # user_id is a required query param.
    assert client.get("/model-directory/resolve").status_code == 422
