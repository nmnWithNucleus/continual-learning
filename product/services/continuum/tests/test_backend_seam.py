import pytest

from app.backends import get_backend
from app.config import get_settings


def test_mock_is_default(var_dir):
    assert get_settings().trainer_backend == "mock"
    assert get_backend("mock").name == "mock"


def test_morpheus_backend_resolves(var_dir, monkeypatch):
    """TRAINER_BACKEND=morpheus must select the real core — and selecting it must
    not import torch or vLLM, so an orchestration test stays GPU-free."""
    monkeypatch.setenv("TRAINER_BACKEND", "morpheus")
    assert get_settings().trainer_backend == "morpheus"
    assert get_backend("morpheus").name == "morpheus"


def test_morpheus_rejects_a_day_log_its_profile_cannot_read(var_dir, small_recipe):
    """The Speed profile needs a numeric `day` anchor. A product-shaped day log
    has none, and finding that out mid-generation would waste the night."""
    from app.backends.morpheus import MorpheusBackend
    from app.daylog import Block

    blocks = [Block(block_id="b0", seg_ids=["s0"], text="something happened",
                    anchors={"date": "2026-07-20", "place": None})]
    with pytest.raises(ValueError, match="one new file"):
        MorpheusBackend().amplify(blocks, small_recipe, seed=0)


def test_unknown_backend_env_falls_back_loudly(var_dir, monkeypatch):
    monkeypatch.setenv("TRAINER_BACKEND", "gpt5")
    assert get_settings().trainer_backend == "mock"


def test_unknown_backend_name_raises():
    with pytest.raises(ValueError):
        get_backend("nope")
