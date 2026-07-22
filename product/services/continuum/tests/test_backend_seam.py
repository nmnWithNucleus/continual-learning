import pytest

from app.backends import get_backend
from app.config import get_settings


def test_mock_is_default(var_dir):
    assert get_settings().trainer_backend == "mock"
    assert get_backend("mock").name == "mock"


def test_engram_stub_fails_loudly_with_pointer(small_recipe):
    backend = get_backend("engram")
    with pytest.raises(NotImplementedError, match="ws-engram-port"):
        backend.amplify([], small_recipe, seed=0)


def test_unknown_backend_env_falls_back_loudly(var_dir, monkeypatch):
    monkeypatch.setenv("TRAINER_BACKEND", "gpt5")
    assert get_settings().trainer_backend == "mock"


def test_unknown_backend_name_raises():
    with pytest.raises(ValueError):
        get_backend("nope")
