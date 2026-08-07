"""T-3 — version composition (L4): pipeline_version is the sorted '+'-join of
every enabled stage's segment; a stage-set change changes the string; the
schema regex cannot express sortedness, so THIS suite owns it; each emitted
slot's version equals its stage's segment of the dialect (the contract's
deliberate redundancy can never drift).

The registration snapshot (a `needs`/slot/budget change without a vS bump = red)
pins the REAL registered stage sets' contract surface at the bottom.
"""
from __future__ import annotations

import asyncio
import re

import pytest

from app.stagegraph.executor import resolve, run_graph
from app.stagegraph.stage import Backend, Stage, StageOutput, stages_for

C1 = {
    "chunk_id": "t3-c", "modality": "audio", "user_id": "u", "device_id": "d",
    "stream_id": "s", "blob_ref": "raw/x",
    "t_start": "2026-08-06T00:00:00Z", "t_end": "2026-08-06T00:00:05Z",
}

# The contract's pipeline_version grammar, applied with fullmatch (the schema
# documents that enforcing implementations must).
_PV_RE = re.compile(
    r"[a-z0-9_]+\.v[0-9]+-[a-z0-9_]+\.v[0-9]+(\.exp-[a-z0-9_]+)?"
    r"(\+[a-z0-9_]+\.v[0-9]+-[a-z0-9_]+\.v[0-9]+(\.exp-[a-z0-9_]+)?)*")


def _stage(name, *, slot=None, vS=1, backend=("mock", 1), needs=(),
           value=None, required=True):
    return type(f"S_{name}", (Stage,), {
        "name": name, "modality": "audio", "stage_version": vS,
        "backend": Backend(*backend), "slot": slot, "needs": tuple(needs),
        "required": required, "byte_budget": 2048,
        "consumer": "speculative:test_fixture",
        "run_sync": lambda self, ctx: StageOutput(
            value=value if value is not None else {"value": name}),
    })()


def test_composition_is_sorted_regardless_of_declaration_order():
    stages = [_stage("zeta"), _stage("alpha"), _stage("mid")]
    pv = resolve("audio", stages).pipeline_version
    segments = pv.split("+")
    assert segments == sorted(segments)
    assert pv == "alpha.v1-mock.v1+mid.v1-mock.v1+zeta.v1-mock.v1"
    # Reversed declaration order -> identical string.
    assert resolve("audio", list(reversed(stages))).pipeline_version == pv


def test_stage_set_change_changes_the_string():
    base = [_stage("asr"), _stage("acoustic")]
    with_extra = base + [_stage("diarize", slot="diarization")]
    assert (resolve("audio", base).pipeline_version
            != resolve("audio", with_extra).pipeline_version)


def test_vs_vb_and_exp_all_move_the_string():
    assert resolve("audio", [_stage("asr", vS=2)]).pipeline_version \
        == "asr.v2-mock.v1"
    assert resolve("audio", [_stage("asr", backend=("mock", 2))]).pipeline_version \
        == "asr.v1-mock.v2"
    exp = _stage("asr")
    exp.experiment = "b7"
    assert resolve("audio", [exp]).pipeline_version == "asr.v1-mock.v1.exp-b7"


def test_composed_string_fullmatches_the_contract_grammar():
    stages = [_stage("asr"), _stage("speaker_align", slot="transcript",
                                    value={"splits": []})]
    stages[1].experiment = "x1"
    pv = resolve("audio", stages).pipeline_version
    assert _PV_RE.fullmatch(pv), pv
    assert not _PV_RE.fullmatch(pv + "\n")  # the trailing-newline trap stays closed


def test_every_emitted_slot_version_equals_its_stages_dialect_segment():
    """End-to-end: slot.version is not merely
    grammar-valid — it IS the producing stage's segment of pipeline_version."""
    stages = [
        _stage("asr", vS=2, backend=("fw", 3)),
        _stage("acoustic", backend=("ast", 1), value={"values": []},
               required=False),
        _stage("diarize", slot="diarization", backend=("pyannote", 1),
               value={"splits": []}, required=False),
    ]
    resolved = resolve("audio", stages)
    result = asyncio.run(run_graph(resolved, c1=C1, blob=b"", span_seconds=5.0,
                                   clients={}))
    dialect_segments = set(resolved.pipeline_version.split("+"))
    by_slot = {s.slot_name: s for s in stages}
    assert result.slots, "graph emitted nothing"
    for slot_name, emitted in result.slots.items():
        assert emitted["version"] == by_slot[slot_name].segment
        assert emitted["version"] in dialect_segments


# ---------------------------------------------------------------------------
# Registration snapshot — the REAL stage sets' contract surface. A change to
# needs / slot / required / byte_budget / backend / experiment without a vS/vB
# bump is RED here: bump the version AND update the snapshot in one commit.
# ---------------------------------------------------------------------------

EXPECTED_CONTRACTS: dict[str, dict[str, dict]] = {
    # modality -> stage name -> the L4 contract surface. A cell changing without
    # its version segment changing is the red this test exists for.
    "audio": {
        "acoustic": {"segment": "acoustic.v1-ast.v1", "slot": "acoustic",
                     "needs": (), "required": False, "byte_budget": 2048,
                     "consumer": "speculative:c10_ambient_route_unruled"},
        "asr": {"segment": "asr.v1-fw.v1", "slot": "asr",
                "needs": (), "required": True, "byte_budget": 32768,
                "consumer": "daylog:heard"},
        "diarize": {"segment": "diarize.v1-pyannote.v1", "slot": "diarization",
                    "needs": (), "required": False, "byte_budget": 8192,
                    "consumer": "stage:speaker_align"},
        "speaker_align": {"segment": "speaker_align.v1-builtin.v1",
                          "slot": "transcript", "needs": ("asr", "diarize"),
                          "required": False, "byte_budget": 49152,
                          "consumer": "daylog:heard"},
    },
    "video": {
        "clipcap": {"segment": "clipcap.v1-vlm.v2", "slot": "caption",
                    "needs": ("clipprep", "screentext"), "required": False,
                    "byte_budget": 4096, "consumer": "daylog:scene"},
        # clipprep emits no record slot (value=None); byte_budget=1 is the
        # structurally-inert minimum the registry requires.
        "clipprep": {"segment": "clipprep.v1-ffmpeg.v1", "slot": "clipprep",
                     "needs": (), "required": True, "byte_budget": 1,
                     "consumer": "stage:screentext+clipcap"},
        "screentext": {"segment": "screentext.v1-ppocr.v1", "slot": "ocr",
                       "needs": ("clipprep",), "required": False,
                       "byte_budget": 2048, "consumer": "daylog:world_text"},
    },
}


@pytest.mark.parametrize("modality", sorted(EXPECTED_CONTRACTS) or ["_pending_"])
def test_registered_contract_surface_matches_snapshot(modality):
    if modality == "_pending_":
        pytest.skip("snapshot lands with the real stage sets")
    stages = stages_for(modality)
    surface = {
        s.name: {
            "segment": s.segment, "slot": s.slot_name, "needs": tuple(s.needs),
            "required": s.required, "byte_budget": s.byte_budget,
            "consumer": s.consumer,
        }
        for s in stages
    }
    assert surface == EXPECTED_CONTRACTS[modality], (
        "the registered contract surface moved — if the change is intended, bump "
        "vS (contract change) or vB (behavior change) and update this snapshot "
        "in the same commit (T-3)"
    )
