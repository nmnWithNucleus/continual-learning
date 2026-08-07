"""TranslateStage: built + tested but NOT registered.

The ratified §2 example dialect excludes translation, prior beta fleet ran it
off, and C2 v1 has no ``translation`` sub-schema yet — so the stage exists as
code (semantics pinned by these tests) and joins a dialect only when its
contract slot lands additively. Tests therefore drive it through EXPLICIT
resolve() stage sets, never the registry.

Semantics under test (v0 parity + plan): no server call when the ASR value is
empty or already English (a fake that raises-if-called proves it); otherwise
whisper task=translate with the source auto-detected and the result language
hardcoded "en" (the prior design point — the server does this too).
"""
from __future__ import annotations

import pytest

from app.schemas import validate_c2
from app.pipeline import build_c2
from app.stagegraph.stage import stages_for
from app.stages.audio.asr import AsrStage
from app.stages.audio.translate import TranslateStage

from .test_audio_stages import (
    B64,
    C1_SYN,
    GOLDEN_TRANSCRIBE,
    FakeModelClient,
    execute,
)


class _RaisesOnTranslate(FakeModelClient):
    """Serves task=transcribe from the canned result; a task=translate call is
 a test failure — proving the no-call paths never touch the server."""

    async def infer(self, payload: dict) -> dict:
        if payload["params"].get("task") == "translate":
            raise AssertionError("translate must not call the server here")
        return await super().infer(payload)


class _Dispatch(FakeModelClient):
    """Routes transcribe vs translate to two canned results."""

    def __init__(self, transcribe: dict, translate: dict):
        super().__init__(transcribe)
        self.translate_result = translate

    async def infer(self, payload: dict) -> dict:
        self.calls.append(payload)
        if payload["params"]["task"] == "translate":
            return dict(self.translate_result)
        return dict(self.result)


SPANISH_ASR = {
    "text": "hola mundo", "language": "es",
    "segments": [{"start_s": 0.0, "end_s": 2.0, "text": "hola mundo"}],
}
ENGLISH_TRANSLATION = {
    "text": "hello world", "language": "en",
    "segments": [{"start_s": 0.0, "end_s": 2.0, "text": "hello world"}],
}


def test_translate_is_not_registered():
    assert "translate" not in [s.name for s in stages_for("audio")]
    assert "translation" not in [s.slot_name for s in stages_for("audio")]


def test_no_server_call_when_asr_is_already_english():
    fake = _RaisesOnTranslate(GOLDEN_TRANSCRIBE)  # language "en"
    _, result = execute([AsrStage(), TranslateStage()], {"whisper": fake})
    assert result.slots["translation"] == {
        "version": "translate.v1-fw.v1", "value": ""}
    assert result.statuses["translate"] == "ok"


def test_no_server_call_when_asr_value_is_empty():
    fake = _RaisesOnTranslate({"text": "", "language": "es", "segments": []})
    _, result = execute([AsrStage(), TranslateStage()], {"whisper": fake})
    assert result.slots["translation"] == {
        "version": "translate.v1-fw.v1", "value": ""}


def test_non_english_speech_translates_with_the_pinned_params():
    fake = _Dispatch(SPANISH_ASR, ENGLISH_TRANSLATION)
    _, result = execute([AsrStage(), TranslateStage()], {"whisper": fake})
    assert result.slots["translation"] == {
        "version": "translate.v1-fw.v1",
        "language": "en",
        "value": "hello world",
        "splits": [{"t_start": "2026-08-06T12:00:00+00:00",
                    "t_end": "2026-08-06T12:00:02+00:00",
                    "value": "hello world"}],
    }
    # Second call is the translate one: source auto-detected (language None),
    # same beam/vad pins as ASR, the chunk bytes + codec ride again.
    translate_call = fake.calls[1]
    assert translate_call["params"] == {
        "task": "translate", "beam_size": 1, "language": None, "vad": True}
    assert translate_call["input_b64"] == B64
    assert translate_call["codec"] == "audio/webm"


def test_empty_translation_result_is_an_honest_empty_claim():
    fake = _Dispatch(SPANISH_ASR, {"text": "", "language": "en", "segments": []})
    _, result = execute([AsrStage(), TranslateStage()], {"whisper": fake})
    assert result.slots["translation"] == {
        "version": "translate.v1-fw.v1", "language": "en", "value": ""}


def test_translate_missing_client_is_a_loud_runtime_error():
    """A missing "whisper" pool fails loudly (never catch-and-fake). Driven
 directly (both stages share the pool, so a graph run would fail at asr
 first — that path is covered by test_audio_stages)."""
    import asyncio

    from app.stagegraph.stage import StageContext, StageOutput

    stage = TranslateStage()
    ctx = StageContext(
        c1=C1_SYN, blob=b"b", span_seconds=6.496,
        inputs={"asr": StageOutput(value={"language": "es", "value": "hola"})},
        clients={},
    )
    with pytest.raises(RuntimeError, match="whisper"):
        asyncio.run(stage.run_async(ctx))


def test_translation_slot_has_no_contract_home_yet():
    """WHY the stage is unregistered: an emitted translation slot fails the C2
 v1 gate today — the sub-schema lands additively when this producer ships
 into a dialect (additionalProperties:false fails unknown slots closed)."""
    fake = _Dispatch(SPANISH_ASR, ENGLISH_TRANSLATION)
    resolved_stages = [AsrStage(), TranslateStage()]
    resolved, result = execute(resolved_stages, {"whisper": fake})
    record = build_c2(C1_SYN, result.slots, resolved.pipeline_version)
    problems = validate_c2(record)
    assert problems != []
    assert any("translation" in p["message"] for p in problems)
