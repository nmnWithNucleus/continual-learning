"""DP-boot VLM identity probe (the Stage C carry, built at Stage F).

clipcap's endpoint sits OUTSIDE servers/manifest.json identity verification — it
speaks the OpenAI wire, not the fleet /infer envelope (Stage C ruling) — so the
pinned model NAME is the only identity DP can assert about it. These tests pin
that the assertion happens at boot, in the deploy configuration (DP_SUPERVISOR=1,
the same opt-in that makes the service own the fleet), and loudly: a service
pointed at the wrong endpoint must refuse to serve, not caption a corpus with an
unpinned model. Unit-test app construction (no DP_SUPERVISOR) must never probe —
the suite has no VLM and must not want one.
"""
import httpx
import pytest
from fastapi.testclient import TestClient

from app.vision.clipcap import vlm
from tests.conftest import install_mock_audio_registry


def _models_endpoint(monkeypatch, model_ids, *, calls: list | None = None):
    """Patch vlm.make_async_client with a fake OpenAI /v1/models endpoint."""
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        assert request.url.path == "/v1/models"
        return httpx.Response(200, json={
            "object": "list",
            "data": [{"id": m, "object": "model"} for m in model_ids],
        })

    monkeypatch.setattr(
        vlm, "make_async_client",
        lambda timeout_s: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _deploy_shaped_app(monkeypatch, tmp_path):
    """An app in the deploy shape: supervisor opt-in ON, but the manifest pointed
    at a path that does not exist so no real fleet ever spawns under pytest."""
    monkeypatch.setenv("STORAGE_URL", "http://storage.test")
    monkeypatch.setenv("DP_VAR_DIR", str(tmp_path / "var"))
    monkeypatch.setenv("DP_SUPERVISOR", "1")
    monkeypatch.setenv("DP_MANIFEST", str(tmp_path / "no-such-manifest.json"))
    monkeypatch.setenv("VLM_URL", "http://vlm.test:8161")
    install_mock_audio_registry(monkeypatch)
    from app.main import create_app

    return create_app()


def test_boot_refuses_when_v1_models_lacks_the_pinned_model(monkeypatch, tmp_path):
    """The wrong endpoint on the right port must fail the boot, naming both what
    it found and what clipcap is pinned to — the operator's next move is in the
    message, not in a log dig."""
    from app.stages.video.clipcap import MODEL

    _models_endpoint(monkeypatch, ["someone-elses/model-7b"])
    app = _deploy_shaped_app(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError, match="VLM identity probe") as exc:
        with TestClient(app):
            pass
    assert MODEL in str(exc.value)
    assert "someone-elses/model-7b" in str(exc.value)


def test_boot_proceeds_when_v1_models_lists_the_pin(monkeypatch, tmp_path):
    from app.stages.video.clipcap import MODEL

    _models_endpoint(monkeypatch, ["someone-elses/model-7b", MODEL])
    app = _deploy_shaped_app(monkeypatch, tmp_path)
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200


def test_unit_shaped_boot_never_probes(monkeypatch, tmp_path, client):
    """The conftest `client` fixture is the unit shape (no DP_SUPERVISOR): its
    construction and teardown must not have touched the VLM factory at all."""
    calls: list = []
    _models_endpoint(monkeypatch, [], calls=calls)
    assert client.get("/health").status_code == 200
    assert calls == []
