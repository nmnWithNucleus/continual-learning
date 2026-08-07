"""The four REGISTERED audio stages as thin clients over the model fleet.

Golden-driven: the fake model clients replay the REAL server golden payloads
(servers/*/tests/fixtures/golden_*.json — both the synthetic 6.496 s fixture and
the 17.808 s real-speech one), and the tests PIN the exact slot dicts each stage
must produce from them (byte-precise strings, exact dict equality). These pins
are the reference the C6 real-fleet e2e must reproduce.

Also covered: the wire envelope each stage sends (params pinned in code, L4),
clamp + absolute-time mapping, speaker-label normalization, v0 acoustic
selection semantics, speaker_align join semantics (max-overlap, lexicographic
tie-break, None on no overlap), L7 error paths through the executor, and C2 v1
contract validation of every registered stage's emitted slot.
"""
from __future__ import annotations

import asyncio
import base64
import copy
import json
from pathlib import Path

import pytest

from app.model_client import ModelCallError
from app.pipeline import build_c2
from app.schemas import validate_c2
from app.stagegraph.executor import emitted_slot_bytes, resolve, run_graph
from app.stagegraph.stage import StageContext, StageOutput, stages_for
from app.stages.audio.acoustic import AcousticStage
from app.stages.audio.asr import AsrStage
from app.stages.audio.diarize import DiarizeStage
from app.stages.audio.speaker_align import SpeakerAlignStage

_SERVERS = Path(__file__).resolve().parents[1] / "servers"


def _golden(server: str, name: str) -> dict:
    return json.loads((_SERVERS / server / "tests" / "fixtures" / name).read_text())


GOLDEN_TRANSCRIBE = _golden("whisper", "golden_transcribe.json")
GOLDEN_TRANSCRIBE_REAL = _golden("whisper", "golden_transcribe_real.json")
GOLDEN_DIARIZE = _golden("pyannote", "golden_diarize.json")
GOLDEN_DIARIZE_REAL = _golden("pyannote", "golden_diarize_real.json")
GOLDEN_TAGS = _golden("ast", "golden_tags.json")
GOLDEN_TAGS_REAL = _golden("ast", "golden_tags_real.json")

BLOB = b"\x1a\x45\xdf\xa3 not-really-webm; the fakes never decode it"
B64 = base64.b64encode(BLOB).decode("ascii")

# The synthetic golden input is 6.496 s, the real-speech one 17.808 s (see the
# fixtures' PROVENANCE.md); the C1 spans match so nothing clamps by accident.
def _c1(t_end: str) -> dict:
    return {
        "contract": "C1", "version": "0", "user_id": "nmn",
        "device_id": "dev-mic", "stream_id": "stream-1", "sequence": 0,
        "chunk_id": "chunk-audio-1", "modality": "audio", "codec": "audio/webm",
        "t_start": "2026-08-06T12:00:00Z", "t_end": t_end,
        "blob_ref": "raw/aa/bb", "blob_sha256": "0" * 64, "blob_bytes": len(BLOB),
    }


C1_SYN = _c1("2026-08-06T12:00:06.496Z")
SPAN_SYN = 6.496
C1_REAL = _c1("2026-08-06T12:00:17.808Z")
SPAN_REAL = 17.808


class FakeModelClient:
    """Client-level fake: replays a canned server result (or raises), recording
    every /infer payload so the tests can pin the exact wire envelope."""

    def __init__(self, result=None, *, error: Exception | None = None):
        self.result = result
        self.error = error
        self.calls: list[dict] = []

    async def aclose(self) -> None:  # the app's client-shutdown path calls this
        pass

    async def infer(self, payload: dict) -> dict:
        self.calls.append(copy.deepcopy(payload))
        if self.error is not None:
            raise self.error
        result = self.result(payload) if callable(self.result) else self.result
        return copy.deepcopy(result)


def execute(stages, clients, *, c1=C1_SYN, span=SPAN_SYN):
    resolved = resolve("audio", stages)
    result = asyncio.run(run_graph(
        resolved, c1=c1, blob=BLOB, span_seconds=span, clients=clients))
    return resolved, result


# ---------------------------------------------------------------------------
# The pinned slot values (derived from the goldens + the ratified mapping
# semantics; C6's real-fleet e2e must reproduce these exact dicts).
# ---------------------------------------------------------------------------

ASR_SLOT_SYN = {
    "version": "asr.v1-fw.v1",
    "language": "en",
    "value": "The quick brown fox jumps over the lazy dog. Machine learning models run as long-lived server processes.",
    "splits": [
        {"t_start": "2026-08-06T12:00:00+00:00",
         "t_end": "2026-08-06T12:00:02.340000+00:00",
         "value": "The quick brown fox jumps over the lazy dog."},
        {"t_start": "2026-08-06T12:00:03.260000+00:00",
         "t_end": "2026-08-06T12:00:06.140000+00:00",
         "value": "Machine learning models run as long-lived server processes."},
    ],
}

_REAL_TEXT = ("you're rich enough scrooge having no better answer ready on the "
              "spur of the moment said bah again and followed it up with humbug "
              "don't be cross uncle said the nephew what else can i be returned "
              "the uncle")

ASR_SLOT_REAL = {
    "version": "asr.v1-fw.v1",
    "language": "en",
    "value": _REAL_TEXT,
    "splits": [
        {"t_start": "2026-08-06T12:00:00+00:00",
         "t_end": "2026-08-06T12:00:17.800000+00:00",
         "value": _REAL_TEXT},
    ],
}

DIARIZATION_SLOT_SYN = {
    "version": "diarize.v1-pyannote.v1",
    "splits": [
        {"t_start": "2026-08-06T12:00:00.030969+00:00",
         "t_end": "2026-08-06T12:00:02.393469+00:00", "speaker": "speaker-0"},
        {"t_start": "2026-08-06T12:00:03.203469+00:00",
         "t_end": "2026-08-06T12:00:06.375969+00:00", "speaker": "speaker-1"},
    ],
}

DIARIZATION_SLOT_REAL = {
    "version": "diarize.v1-pyannote.v1",
    "splits": [
        {"t_start": "2026-08-06T12:00:00.030969+00:00",
         "t_end": "2026-08-06T12:00:01.094094+00:00", "speaker": "speaker-0"},
        {"t_start": "2026-08-06T12:00:01.769094+00:00",
         "t_end": "2026-08-06T12:00:05.532219+00:00", "speaker": "speaker-0"},
        {"t_start": "2026-08-06T12:00:06.122844+00:00",
         "t_end": "2026-08-06T12:00:06.662844+00:00", "speaker": "speaker-0"},
        {"t_start": "2026-08-06T12:00:07.118469+00:00",
         "t_end": "2026-08-06T12:00:09.008469+00:00", "speaker": "speaker-0"},
        {"t_start": "2026-08-06T12:00:09.548469+00:00",
         "t_end": "2026-08-06T12:00:09.565344+00:00", "speaker": "speaker-1"},
        {"t_start": "2026-08-06T12:00:09.565344+00:00",
         "t_end": "2026-08-06T12:00:10.004094+00:00", "speaker": "speaker-2"},
        {"t_start": "2026-08-06T12:00:10.746594+00:00",
         "t_end": "2026-08-06T12:00:12.484719+00:00", "speaker": "speaker-0"},
        {"t_start": "2026-08-06T12:00:13.075344+00:00",
         "t_end": "2026-08-06T12:00:13.952844+00:00", "speaker": "speaker-0"},
        {"t_start": "2026-08-06T12:00:14.425344+00:00",
         "t_end": "2026-08-06T12:00:15.842844+00:00", "speaker": "speaker-2"},
        {"t_start": "2026-08-06T12:00:16.129719+00:00",
         "t_end": "2026-08-06T12:00:17.462844+00:00", "speaker": "speaker-0"},
    ],
}

# Both golden inputs are speech-only clips: the v0 selection drops the AudioSet
# speech family and nothing non-speech reaches the 0.1 threshold, so the pinned
# acoustic slot is the honest empty claim (values [], no confidence key).
ACOUSTIC_SLOT_GOLDEN = {"version": "acoustic.v1-ast.v1", "values": []}

TRANSCRIPT_SLOT_SYN = {
    "version": "speaker_align.v1-builtin.v1",
    "splits": [
        {"t_start": "2026-08-06T12:00:00+00:00",
         "t_end": "2026-08-06T12:00:02.340000+00:00",
         "value": "The quick brown fox jumps over the lazy dog.",
         "speaker": "speaker-0"},
        {"t_start": "2026-08-06T12:00:03.260000+00:00",
         "t_end": "2026-08-06T12:00:06.140000+00:00",
         "value": "Machine learning models run as long-lived server processes.",
         "speaker": "speaker-1"},
    ],
}

TRANSCRIPT_SLOT_REAL = {
    "version": "speaker_align.v1-builtin.v1",
    "splits": [
        {"t_start": "2026-08-06T12:00:00+00:00",
         "t_end": "2026-08-06T12:00:17.800000+00:00",
         "value": _REAL_TEXT,
         "speaker": "speaker-0"},
    ],
}


# ---------------------------------------------------------------------------
# Registry: the audio dialect is exactly these four stages
# ---------------------------------------------------------------------------

def test_registered_audio_stages_are_exactly_the_v1_dialect():
    names = [s.name for s in stages_for("audio")]
    assert names == ["acoustic", "asr", "diarize", "speaker_align"]
    # Built-but-unregistered producers stay OUT of the dialect (translate,
    # injected_caption) until a contract slot + a graph composition adds them.
    assert "translate" not in names and "injected_caption" not in names


def test_registered_graph_resolves_with_the_expected_pipeline_version():
    resolved = resolve("audio", list(stages_for("audio")))
    assert resolved.pipeline_version == (
        "acoustic.v1-ast.v1+asr.v1-fw.v1+diarize.v1-pyannote.v1"
        "+speaker_align.v1-builtin.v1"
    )


# ---------------------------------------------------------------------------
# asr — thin client over servers/whisper
# ---------------------------------------------------------------------------

def test_asr_pins_the_synthetic_golden_slot_and_the_wire_envelope():
    fake = FakeModelClient(GOLDEN_TRANSCRIBE)
    _, result = execute([AsrStage()], {"whisper": fake})
    assert result.slots["asr"] == ASR_SLOT_SYN
    assert result.statuses == {"asr": "ok"}
    # The exact envelope + whisper golden params, pinned in code (L4).
    assert fake.calls == [{
        "input_b64": B64,
        "codec": "audio/webm",
        "params": {"task": "transcribe", "beam_size": 1, "language": "en",
                   "vad": True},
    }]


def test_asr_pins_the_real_speech_golden_slot():
    fake = FakeModelClient(GOLDEN_TRANSCRIBE_REAL)
    _, result = execute([AsrStage()], {"whisper": fake}, c1=C1_REAL, span=SPAN_REAL)
    assert result.slots["asr"] == ASR_SLOT_REAL


def test_asr_vad_silence_is_the_honest_empty_claim():
    fake = FakeModelClient({"text": "", "language": "en", "segments": []})
    _, result = execute([AsrStage()], {"whisper": fake})
    # value "" kept, splits omitted (no segments), language kept (truthy).
    assert result.slots["asr"] == {
        "version": "asr.v1-fw.v1", "language": "en", "value": ""}


def test_asr_omits_language_only_when_falsy():
    fake = FakeModelClient({"text": "hi", "language": "", "segments": []})
    _, result = execute([AsrStage()], {"whisper": fake})
    assert result.slots["asr"] == {"version": "asr.v1-fw.v1", "value": "hi"}


def test_asr_clamps_split_offsets_into_the_span():
    fake = FakeModelClient({
        "text": "spill", "language": "en",
        "segments": [{"start_s": -1.5, "end_s": 99.0, "text": "spill"}],
    })
    _, result = execute([AsrStage()], {"whisper": fake})
    assert result.slots["asr"]["splits"] == [{
        "t_start": "2026-08-06T12:00:00+00:00",
        "t_end": "2026-08-06T12:00:06.496000+00:00",
        "value": "spill",
    }]


def test_asr_missing_client_is_a_loud_runtime_error():
    with pytest.raises(RuntimeError, match="whisper"):
        execute([AsrStage()], {})


def test_asr_required_failure_propagates_the_model_call_error():
    fake = FakeModelClient(error=ModelCallError("whisper: undecodable input"))
    with pytest.raises(ModelCallError, match="undecodable"):
        execute([AsrStage()], {"whisper": fake})


# ---------------------------------------------------------------------------
# diarize — thin client over servers/pyannote
# ---------------------------------------------------------------------------

def test_diarize_pins_the_synthetic_golden_slot_and_the_wire_envelope():
    fake = FakeModelClient(GOLDEN_DIARIZE)
    _, result = execute([DiarizeStage()], {"pyannote": fake})
    assert result.slots["diarization"] == DIARIZATION_SLOT_SYN
    assert fake.calls == [{
        "input_b64": B64,
        "codec": "audio/webm",
        "params": {"span_seconds": 6.496},
    }]


def test_diarize_pins_the_real_speech_golden_slot():
    fake = FakeModelClient(GOLDEN_DIARIZE_REAL)
    _, result = execute([DiarizeStage()], {"pyannote": fake},
                        c1=C1_REAL, span=SPAN_REAL)
    assert result.slots["diarization"] == DIARIZATION_SLOT_REAL


def test_diarize_normalizes_labels_to_speaker_n_by_first_onset():
    # Whatever vocabulary the server speaks, labels come out speaker-N in
    # first-onset order (raw label as the deterministic tie-break).
    fake = FakeModelClient({"turns": [
        {"start_s": 0.5, "end_s": 1.0, "speaker": "SPEAKER_07"},
        {"start_s": 2.0, "end_s": 3.0, "speaker": "SPEAKER_01"},
        {"start_s": 2.5, "end_s": 3.5, "speaker": "SPEAKER_07"},
    ]})
    _, result = execute([DiarizeStage()], {"pyannote": fake})
    assert [s["speaker"] for s in result.slots["diarization"]["splits"]] == \
        ["speaker-0", "speaker-1", "speaker-0"]


def test_diarize_same_onset_tie_breaks_on_the_raw_label():
    fake = FakeModelClient({"turns": [
        {"start_s": 1.0, "end_s": 2.0, "speaker": "zz"},
        {"start_s": 1.0, "end_s": 3.0, "speaker": "aa"},
    ]})
    _, result = execute([DiarizeStage()], {"pyannote": fake})
    assert [s["speaker"] for s in result.slots["diarization"]["splits"]] == \
        ["speaker-1", "speaker-0"]  # "aa" < "zz" at the shared onset


def test_diarize_no_turns_is_the_honest_empty_claim():
    fake = FakeModelClient({"turns": []})
    _, result = execute([DiarizeStage()], {"pyannote": fake})
    assert result.slots["diarization"] == {
        "version": "diarize.v1-pyannote.v1", "splits": []}


def test_diarize_optional_failure_is_a_hole_not_a_crash():
    fake = FakeModelClient(error=ModelCallError("pyannote: bad input"))
    _, result = execute([DiarizeStage()], {"pyannote": fake})
    assert result.statuses == {"diarize": "failed"}
    assert result.slots == {}


# ---------------------------------------------------------------------------
# acoustic — thin client over servers/ast, v0 caption-selection ported
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("golden", [GOLDEN_TAGS, GOLDEN_TAGS_REAL],
                         ids=["synthetic", "real"])
def test_acoustic_pins_the_golden_slots(golden):
    fake = FakeModelClient(golden)
    _, result = execute([AcousticStage()], {"ast": fake})
    assert result.slots["acoustic"] == ACOUSTIC_SLOT_GOLDEN
    assert fake.calls[0]["params"] == {"top_k": 20}
    assert fake.calls[0]["input_b64"] == B64
    assert fake.calls[0]["codec"] == "audio/webm"


def test_acoustic_selection_is_v0s_speech_drop_threshold_topk():
    fake = FakeModelClient({"tags": [
        {"label": "Speech", "score": 0.9},               # speech family: dropped
        {"label": "Typing", "score": 0.52},
        {"label": "Dog", "score": 0.31},
        {"label": "Water tap, faucet", "score": 0.12},
        {"label": "Tick-tock", "score": 0.09},           # under 0.1: dropped
    ]})
    _, result = execute([AcousticStage()], {"ast": fake})
    assert result.slots["acoustic"] == {
        "version": "acoustic.v1-ast.v1",
        "values": ["typing", "dog", "water tap, faucet"],
        "confidence": 0.52,
    }


def test_acoustic_caps_at_top_k_3():
    fake = FakeModelClient({"tags": [
        {"label": "A1", "score": 0.5}, {"label": "B2", "score": 0.4},
        {"label": "C3", "score": 0.3}, {"label": "D4", "score": 0.2},
    ]})
    _, result = execute([AcousticStage()], {"ast": fake})
    assert result.slots["acoustic"]["values"] == ["a1", "b2", "c3"]


def test_acoustic_equal_scores_tie_break_on_the_raw_label():
    fake = FakeModelClient({"tags": [
        {"label": "Zebra", "score": 0.4}, {"label": "Anvil", "score": 0.4},
    ]})
    _, result = execute([AcousticStage()], {"ast": fake})
    assert result.slots["acoustic"]["values"] == ["anvil", "zebra"]
    assert result.slots["acoustic"]["confidence"] == 0.4


def test_acoustic_optional_failure_is_a_hole():
    fake = FakeModelClient(error=ModelCallError("ast: bad input"))
    _, result = execute([AcousticStage()], {"ast": fake})
    assert result.statuses == {"acoustic": "failed"}
    assert result.slots == {}


# ---------------------------------------------------------------------------
# speaker_align — pure CPU derived view (asr x diarization -> transcript)
# ---------------------------------------------------------------------------

def _aligned(whisper_result, pyannote_result, *, c1=C1_SYN, span=SPAN_SYN):
    stages = [AsrStage(), DiarizeStage(), SpeakerAlignStage()]
    clients = {"whisper": FakeModelClient(whisper_result),
               "pyannote": FakeModelClient(pyannote_result)}
    return execute(stages, clients, c1=c1, span=span)


def test_speaker_align_pins_the_synthetic_golden_transcript():
    _, result = _aligned(GOLDEN_TRANSCRIBE, GOLDEN_DIARIZE)
    assert result.slots["transcript"] == TRANSCRIPT_SLOT_SYN
    assert result.statuses == {"asr": "ok", "diarize": "ok",
                               "speaker_align": "ok"}


def test_speaker_align_pins_the_real_speech_golden_transcript():
    _, result = _aligned(GOLDEN_TRANSCRIBE_REAL, GOLDEN_DIARIZE_REAL,
                         c1=C1_REAL, span=SPAN_REAL)
    assert result.slots["transcript"] == TRANSCRIPT_SLOT_REAL


def test_speaker_align_no_overlap_yields_speaker_none():
    _, result = _aligned(
        {"text": "a", "language": "en",
         "segments": [{"start_s": 0.0, "end_s": 1.0, "text": "a"}]},
        {"turns": [{"start_s": 5.0, "end_s": 6.0, "speaker": "spk_0"}]},
    )
    (split,) = result.slots["transcript"]["splits"]
    assert split["speaker"] is None
    assert split["value"] == "a"


def test_speaker_align_empty_asr_emits_empty_splits():
    _, result = _aligned({"text": "", "language": "en", "segments": []},
                         GOLDEN_DIARIZE)
    assert result.slots["transcript"] == {
        "version": "speaker_align.v1-builtin.v1", "splits": []}


def test_speaker_align_empty_diarization_leaves_speakers_none():
    _, result = _aligned(GOLDEN_TRANSCRIBE, {"turns": []})
    assert [s["speaker"] for s in result.slots["transcript"]["splits"]] == \
        [None, None]


def test_speaker_align_equal_overlap_tie_breaks_on_the_lower_label():
    # seg [2,5] overlaps speaker-1's [2,3] and speaker-0's [4,5] by exactly 1 s
    # each -> the lexicographically smaller label wins (v0 assign.py rule).
    _, result = _aligned(
        {"text": "x", "language": "en",
         "segments": [{"start_s": 2.0, "end_s": 5.0, "text": "x"}]},
        {"turns": [
            {"start_s": 0.0, "end_s": 1.0, "speaker": "A"},
            {"start_s": 2.0, "end_s": 3.0, "speaker": "B"},
            {"start_s": 4.0, "end_s": 5.0, "speaker": "A"},
        ]},
    )
    (split,) = result.slots["transcript"]["splits"]
    assert split["speaker"] == "speaker-0"


def test_speaker_align_reads_inputs_tolerant_of_a_version_key():
    """The blackboard carries stage values without the executor's version stamp,
    but the join must tolerate stamped dicts (the slot-value shape) too."""
    stage = SpeakerAlignStage()
    ctx = StageContext(
        c1=C1_SYN, blob=BLOB, span_seconds=SPAN_SYN,
        inputs={
            "asr": StageOutput(value={
                "version": "asr.v1-fw.v1", "language": "en", "value": "a",
                "splits": [{"t_start": "2026-08-06T12:00:00+00:00",
                            "t_end": "2026-08-06T12:00:02+00:00", "value": "a"}],
            }),
            "diarize": StageOutput(value={
                "version": "diarize.v1-pyannote.v1",
                "splits": [{"t_start": "2026-08-06T12:00:00+00:00",
                            "t_end": "2026-08-06T12:00:03+00:00",
                            "speaker": "speaker-0"}],
            }),
        },
    )
    out = stage.run_sync(ctx)
    assert out.value == {"splits": [{
        "t_start": "2026-08-06T12:00:00+00:00",
        "t_end": "2026-08-06T12:00:02+00:00",
        "value": "a", "speaker": "speaker-0",
    }]}


def test_diarize_hole_cancels_the_speaker_align_cone():
    stages = [AsrStage(), DiarizeStage(), SpeakerAlignStage()]
    clients = {"whisper": FakeModelClient(GOLDEN_TRANSCRIBE),
               "pyannote": FakeModelClient(error=ModelCallError("down"))}
    _, result = execute(stages, clients)
    assert result.statuses == {"asr": "ok", "diarize": "failed",
                               "speaker_align": "cancelled"}
    assert set(result.slots) == {"asr"}  # diarization + transcript are holes


# ---------------------------------------------------------------------------
# Contract: every registered stage's slot validates against C2 v1
# ---------------------------------------------------------------------------

def _record_for(stages, clients, *, c1=C1_SYN, span=SPAN_SYN):
    resolved, result = execute(stages, clients, c1=c1, span=span)
    return build_c2(c1, result.slots, resolved.pipeline_version)


def test_full_registered_graph_assembles_a_valid_v1_record():
    clients = {"whisper": FakeModelClient(GOLDEN_TRANSCRIBE),
               "pyannote": FakeModelClient(GOLDEN_DIARIZE),
               "ast": FakeModelClient(GOLDEN_TAGS)}
    record = _record_for(
        [AsrStage(), DiarizeStage(), AcousticStage(), SpeakerAlignStage()],
        clients)
    assert validate_c2(record) == []
    assert set(record["content"]["slots"]) == {
        "asr", "diarization", "acoustic", "transcript"}
    assert record["content"]["slots"]["asr"] == ASR_SLOT_SYN
    assert record["content"]["slots"]["diarization"] == DIARIZATION_SLOT_SYN
    assert record["content"]["slots"]["acoustic"] == ACOUSTIC_SLOT_GOLDEN
    assert record["content"]["slots"]["transcript"] == TRANSCRIPT_SLOT_SYN


def test_asr_slot_alone_validates_against_the_contract():
    record = _record_for([AsrStage()],
                         {"whisper": FakeModelClient(GOLDEN_TRANSCRIBE)})
    assert validate_c2(record) == []


def test_diarization_slot_alone_validates_against_the_contract():
    record = _record_for([DiarizeStage()],
                         {"pyannote": FakeModelClient(GOLDEN_DIARIZE)})
    assert validate_c2(record) == []


def test_acoustic_slot_alone_validates_against_the_contract():
    record = _record_for([AcousticStage()],
                         {"ast": FakeModelClient(GOLDEN_TAGS)})
    assert validate_c2(record) == []


def test_vad_silence_record_validates_against_the_contract():
    record = _record_for(
        [AsrStage()],
        {"whisper": FakeModelClient({"text": "", "language": "en",
                                     "segments": []})})
    assert validate_c2(record) == []


# ---------------------------------------------------------------------------
# Byte budgets: the real-speech goldens fit with wide margin (>= 4x headroom)
# ---------------------------------------------------------------------------

def test_declared_budgets_leave_wide_margin_over_the_real_goldens():
    clients = {"whisper": FakeModelClient(GOLDEN_TRANSCRIBE_REAL),
               "pyannote": FakeModelClient(GOLDEN_DIARIZE_REAL),
               "ast": FakeModelClient(GOLDEN_TAGS_REAL)}
    stages = [AsrStage(), DiarizeStage(), AcousticStage(), SpeakerAlignStage()]
    _, result = execute(stages, clients, c1=C1_REAL, span=SPAN_REAL)
    budgets = {s.slot_name: s.byte_budget for s in stages}
    for slot_name, emitted in result.slots.items():
        assert emitted_slot_bytes(emitted) * 4 <= budgets[slot_name], slot_name
