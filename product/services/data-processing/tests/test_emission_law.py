"""§4 of `handoff/ws-video-clip.md` — the record-vs-mutation law, as CI (WS-E).

The law is prose in the design and (after WS-E2) a raise in `stage.py`. This file is the
half in between: it holds the *live registry* — the real, integrated stages the service
ships — against the riders R1–R4 and the mechanically-checkable verdicts of the 18-row
worked table, **across a matrix of configurations**, not just the default one.

WHY A MATRIX. Every rider is conditioned on *enabledness*, and enabledness is a function
of the environment read fresh per call (`resolve_pipeline()`, `get_ocr_config()`,
`get_audio_config()`). A law asserted only under the default env would be silent about
exactly the configuration a cutover flips to. So each check below runs over `_MATRIX` —
legacy and clip, every OCR backend, every audio sidecar on, plus mistyped values (a typo
must resolve to a safe default, never to a hole in the law).

WHAT IS CHECKED WHERE (the three layers, deliberately not one):
  * registration-time (`stage.py`, WS-E2) — the STRUCTURAL half of R1: a sidecar with a
    non-empty `provides` that never declares `version_fragment` at all cannot be
    imported. Settings do not exist at import, so that is as far as a raise can reach.
  * this file — the CONFIGURATION half: over the matrix, an enabled provides-bearing
    sidecar must return a NON-EMPTY fragment for that config (a declared resolver that
    returns `''` when enabled is caught here and nowhere else), plus R3(b), the mutate
    rules, and the table.
  * the owning stage tests (`test_screentext.py`, `test_clipcap.py`, …) — behaviour.

The checkers (`r1_violations`, …) take a stage LIST, not the registry, so the same code
that vets the live registry is the code the negative tests point at a hypothetical bad
stage. `test_r1_catches_a_hypothetical_violating_stage` is the exit criterion: the law
must FAIL on a violator, or it is decoration.
"""
from __future__ import annotations

from typing import Iterable, NamedTuple

import pytest

from app.config import Settings, get_settings
from app.processing.base import ProcessedContent, ProcessedUnit, empty_enrichments
from app.stagegraph import stage as stage_mod
from app.stagegraph.executor import resolve
from app.stagegraph.stage import (
    Stage, StageContext, StageResult, register_stage, stages_for,
)
from app.vision import emit, ocr
from app.vision.clip_types import ClipDesc, ClipFrames
from app.vision.config import get_vision_settings
from app.vision.ocr.config import get_ocr_config

MODALITIES = ("audio", "video")

# The ONE frozen R1 exemption (§4.3 R1, D-14): the legacy `keyframes` prep sidecar has a
# non-empty `provides` and no fragment, solely so the retired `vidproc-*-v0` record_ids
# reproduce byte-for-byte. It is named here, once, and WS-E2 names the same pair in
# `stage.py`. No new stage may join it — `test_r1_exemption_is_exactly_keyframes` fails
# if this set grows, and `test_no_other_stage_needs_the_r1_exemption` fails if any other
# live stage would have needed it.
FROZEN_R1_EXEMPTIONS = frozenset({("video", "keyframes")})


# --------------------------------------------------------------------------------------
# The configuration matrix
# --------------------------------------------------------------------------------------

# Every env var any of the configurations below touches — cleared before each case so a
# leaked value from the ambient environment or a previous test can never make the law
# vacuously true.
_ENV_KEYS = (
    "ASR_BACKEND", "VIDEO_PIPELINE", "VIDEO_BACKEND", "VIDEO_OCR_BACKEND",
    "VIDEO_OCR_RECORDS", "DIARIZE_BACKEND", "TRANSLATE_BACKEND", "TRANSLATE_TARGET",
    "ACOUSTIC_BACKEND", "INJECT_CAPTION_BACKEND", "INJECT_CAPTION_INDEX",
)

_MATRIX: dict[str, dict[str, str]] = {
    # --- legacy (the shipped default path) --------------------------------------------
    "legacy-default": {},
    "legacy-vlm": {"VIDEO_PIPELINE": "keyframe", "VIDEO_BACKEND": "vlm"},
    "legacy-audio-all-on": {
        "DIARIZE_BACKEND": "mock", "TRANSLATE_BACKEND": "mock", "TRANSLATE_TARGET": "en",
        "ACOUSTIC_BACKEND": "mock", "INJECT_CAPTION_BACKEND": "index",
    },
    # A mistyped mode/backend must resolve to the safe default in BOTH resolvers, so the
    # law has to hold here too (this is the config a bad deploy actually produces).
    "legacy-typos": {
        "VIDEO_PIPELINE": "kyframe", "VIDEO_OCR_BACKEND": "nonsense",
        "DIARIZE_BACKEND": "nope", "VIDEO_BACKEND": "nope",
    },
    # --- clip (what the cutover flips to) ---------------------------------------------
    "clip-ocr-mock": {"VIDEO_PIPELINE": "clip", "VIDEO_OCR_BACKEND": "mock"},
    "clip-ocr-off": {"VIDEO_PIPELINE": "clip", "VIDEO_OCR_BACKEND": "off"},
    "clip-ocr-ppocr": {"VIDEO_PIPELINE": "clip", "VIDEO_OCR_BACKEND": "ppocr"},
    "clip-ocr-vlm": {
        "VIDEO_PIPELINE": "clip", "VIDEO_OCR_BACKEND": "vlm", "VIDEO_BACKEND": "vlm",
    },
    "clip-ocr-typo": {"VIDEO_PIPELINE": "CLIP", "VIDEO_OCR_BACKEND": "nonsense"},
    "clip-audio-all-on": {
        "VIDEO_PIPELINE": "clip", "VIDEO_OCR_BACKEND": "mock",
        "DIARIZE_BACKEND": "mock", "TRANSLATE_BACKEND": "mock", "TRANSLATE_TARGET": "en",
        "ACOUSTIC_BACKEND": "mock", "INJECT_CAPTION_BACKEND": "index",
    },
}


class Config(NamedTuple):
    label: str
    settings: Settings


@pytest.fixture(params=sorted(_MATRIX), ids=sorted(_MATRIX))
def config(request, monkeypatch) -> Config:
    """One row of `_MATRIX`, applied to the environment (read fresh per call by every
    resolver in the service) and to the `Settings` the executor passes to stages."""
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ASR_BACKEND", "mock")  # no GPU, no model download, ever
    for key, value in _MATRIX[request.param].items():
        monkeypatch.setenv(key, value)
    return Config(label=request.param, settings=get_settings())


def _live(modality: str) -> list[Stage]:
    """The real registry — the integrated stages this service ships, not a fixture."""
    return stages_for(modality)


def _enabled(stages: Iterable[Stage], settings) -> list[Stage]:
    return [s for s in stages if s.is_enabled(settings)]


# --------------------------------------------------------------------------------------
# The checkers — the law itself, as functions over a stage list
# --------------------------------------------------------------------------------------

def r1_violations(
    modality: str, stages: Iterable[Stage], settings,
    *, exemptions: frozenset = FROZEN_R1_EXEMPTIONS,
) -> list[str]:
    """R1 FORK RIDER — an enabled `sidecar` with a non-empty `provides` must return a
    non-empty `version_fragment`. A provided slot exists only to be consumed, i.e. to
    change bytes of a record this stage does not itself emit; without a fragment that
    change lands under an unchanged `pipeline_version` and silently upserts."""
    out = []
    for s in stages:
        if s.kind != "sidecar" or not s.provides or (modality, s.name) in exemptions:
            continue
        if s.is_enabled(settings) and not s.version_fragment(settings):
            out.append(
                f"R1: {modality}/{s.name} is an enabled sidecar providing "
                f"{tuple(s.provides)} but its version_fragment is empty — its config can "
                "change bytes it does not emit, under an unforked pipeline_version"
            )
    return out


def r3b_violations(modality: str, stages: Iterable[Stage], settings) -> list[str]:
    """R3(b) DIALECT HONESTY — never `best_effort` + a non-empty fragment: the dialect
    states the ATTEMPTED configuration and is resolved before any stage runs, so a stage
    that may be skipped would stamp a claim the record set does not have. (R4's "stage
    outcome" clause makes the same combination illegal from set-stability.)"""
    out = []
    for s in stages:
        if s.policy != "best_effort":
            continue
        if s.version_fragment(settings):
            out.append(
                f"R3(b): {modality}/{s.name} is best_effort but carries fragment "
                f"{s.version_fragment(settings)!r} — the dialect would claim a property "
                "the record set may not have"
            )
        if s.kind != "sidecar":
            out.append(f"R3(a): {modality}/{s.name} is best_effort but kind={s.kind!r}")
    return out


def mutate_violations(modality: str, stages: Iterable[Stage]) -> list[str]:
    """The single-resolver rule: a mutate's enabledness IS `version_fragment != ''`, so
    overriding `enabled()` re-opens the silent-overwrite class (`stage.py:226-230`).
    Static — it does not depend on settings."""
    return [
        f"{modality}/{s.name}: mutate stages must not override enabled()"
        for s in stages
        if s.kind == "mutate" and type(s)._defines("enabled")
    ]


def writes_violations(modality: str, stages: Iterable[Stage], settings) -> list[str]:
    """`writes ⊆ primary.mutable_slots` for every ENABLED mutate, against the primary
    that is actually enabled in this configuration (a modality may ship two primaries —
    video ships the legacy `captions` and the clip `clipcap` — and a mutate must be legal
    against whichever one it runs beside)."""
    enabled = _enabled(stages, settings)
    primaries = [s for s in enabled if s.kind == "primary"]
    if len(primaries) != 1:
        return [f"{modality}: expected exactly one enabled primary, got "
                f"{[s.name for s in primaries]}"]
    primary = primaries[0]
    out = []
    for s in enabled:
        if s.kind != "mutate":
            continue
        extra = set(s.writes) - set(primary.mutable_slots)
        if extra:
            out.append(
                f"{modality}/{s.name}: writes {sorted(extra)} not in "
                f"{primary.name}.mutable_slots {tuple(primary.mutable_slots)}"
            )
    return out


# --------------------------------------------------------------------------------------
# R1 — over the live registry
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize("modality", MODALITIES)
def test_r1_fork_rider_holds_over_the_live_registry(config, modality):
    assert r1_violations(modality, _live(modality), config.settings) == []


def test_r1_the_clip_stages_are_compliant_by_construction(config):
    """The design's claim, asserted rather than trusted: in clip mode both provides-
    bearing clip sidecars carry their fragment (`+cp-v1`, `+ocr-*-v1`), for EVERY OCR
    backend including `off` and a typo — `off` is an honest dialect ("OCR was configured
    off"), never stage-disablement, because `clipcap` hard-needs the slot."""
    if not config.label.startswith("clip-"):
        pytest.skip("clip-mode configurations only")
    by_name = {s.name: s for s in _live("video")}
    for name, expected_prefix in (("clipprep", "+cp-"), ("screentext", "+ocr-")):
        stage = by_name[name]
        assert stage.kind == "sidecar" and stage.provides
        assert stage.is_enabled(config.settings), f"{name} must be enabled in clip mode"
        fragment = stage.version_fragment(config.settings)
        assert fragment.startswith(expected_prefix) and fragment != ""


def test_a_disabled_stages_fragment_never_reaches_the_dialect(config):
    """R1 is one-directional on purpose, and this is the guard on the other side.

    `screentext`'s fragment is a pure function of the OCR config, so it is non-empty even
    in keyframe mode, where the stage is disabled (`+ocr-mock-v1` with no OCR stage in the
    graph). That is harmless ONLY because `resolve()` composes `pipeline_version` from the
    ENABLED set alone — assert exactly that, rather than forcing every resolver to gate
    itself on enabledness. The observable form is WS-G's byte-for-byte legacy dialect: in
    keyframe mode the video dialect is the bare `vidproc-*-v0` literal, with nothing from
    the dormant clip cohort in it."""
    version = resolve("video", _live("video"), config.settings).pipeline_version
    disabled = [s for s in _live("video") if not s.is_enabled(config.settings)]
    for s in disabled:
        fragment = s.version_fragment(config.settings)
        assert not fragment or fragment not in version, (
            f"video/{s.name} is disabled but its fragment {fragment!r} is in the dialect "
            f"{version!r} — the record would claim a stage that never ran"
        )
    if not config.label.startswith("clip-"):
        assert version in ("vidproc-mock-v0", "vidproc-vlm-v0"), version


def test_r1_exemption_is_exactly_keyframes_and_is_still_live():
    """The exemption set is frozen at one named pair, and it is not vestigial: the stage
    it names really is a provides-bearing sidecar with an empty fragment while enabled."""
    assert FROZEN_R1_EXEMPTIONS == frozenset({("video", "keyframes")})
    keyframes = {s.name: s for s in _live("video")}["keyframes"]
    assert keyframes.kind == "sidecar"
    assert keyframes.provides  # non-empty: it feeds the legacy captions primary
    assert keyframes.version_fragment(get_settings()) == ""


def test_no_other_stage_needs_the_r1_exemption(config):
    """Run the law with NO exemptions: the only violation, in any configuration, must be
    the one frozen pair. This is what makes the exemption a named debt rather than a
    hole — a second stage sliding under it turns this test red."""
    violations = [
        (modality, v)
        for modality in MODALITIES
        for v in r1_violations(
            modality, _live(modality), config.settings, exemptions=frozenset()
        )
    ]
    names = {(m, v.split()[1].split("/")[1]) for m, v in violations}
    assert names <= FROZEN_R1_EXEMPTIONS, f"unexempted R1 violations: {violations}"


# --------------------------------------------------------------------------------------
# R1 — the negative case (the exit criterion): the law must FAIL on a violator
# --------------------------------------------------------------------------------------

class _BadSidecar(Stage):
    """A hypothetical stage that violates R1: it feeds another stage's bytes (a non-empty
    `provides`) while leaving the dialect unforked. NOT registered at class-definition
    time — after WS-E2 the structural half of this is a registration error (see
    `test_ws_e2_registration_rejects_the_structural_r1_violation`); here the instance is
    handed straight to the checker, which is the layer that must catch a *declared*
    resolver returning `''` while enabled."""

    name = "badsidecar"
    modality = "zz"
    kind = "sidecar"
    provides = ("something_the_primary_reads",)
    order = 99

    def run_sync(self, ctx):  # pragma: no cover - never executed
        return StageResult()


class _BadSidecarWithResolver(_BadSidecar):
    """Worse, and invisible to any import-time check: it DECLARES a fragment resolver and
    returns `''` for the very configuration in which it is enabled."""

    name = "badsidecar_resolver"
    order = 98

    def version_fragment(self, settings) -> str:
        return ""


@pytest.mark.parametrize("bad", [_BadSidecar, _BadSidecarWithResolver])
def test_r1_catches_a_hypothetical_violating_stage(config, bad):
    violations = r1_violations("zz", [bad()], config.settings)
    assert len(violations) == 1
    assert bad.name in violations[0] and "R1" in violations[0]


def test_r1_over_the_live_registry_catches_a_registered_violator(config):
    """Not just the checker — the *registry-driven* path. Register a violating stage into
    a throwaway modality, then run the same live-registry query the CI test above runs and
    assert it goes red. Registration is done through the real decorator, so the day
    WS-E2's raise lands this test proves it (see the WS-E2 test, which supersedes the
    structural half)."""
    try:
        register_stage(_BadSidecarWithResolver)
        assert stages_for("zz")  # discovery ran; the throwaway modality is live
        assert r1_violations("zz", stages_for("zz"), config.settings) != []
    finally:
        stage_mod._REGISTRY.pop("zz", None)


# --------------------------------------------------------------------------------------
# R3 / R4 — best_effort, and the mutate rules
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize("modality", MODALITIES)
def test_no_best_effort_stage_carries_a_fragment(config, modality):
    assert r3b_violations(modality, _live(modality), config.settings) == []


@pytest.mark.parametrize("modality", MODALITIES)
def test_no_mutate_overrides_enabled(modality):
    assert mutate_violations(modality, _live(modality)) == []


@pytest.mark.parametrize("modality", MODALITIES)
def test_mutate_writes_are_a_subset_of_the_primarys_mutable_slots(config, modality):
    assert writes_violations(modality, _live(modality), config.settings) == []


@pytest.mark.parametrize("modality", MODALITIES)
def test_exactly_one_enabled_primary_per_modality(config, modality):
    """`executor.py:114-119`, restated as law: zero primaries 500s every ingest of that
    modality, two silently double-write the record. Both legacy and clip mode must land
    on exactly one — including under a mistyped `VIDEO_PIPELINE`."""
    primaries = [s for s in _enabled(_live(modality), config.settings)
                 if s.kind == "primary"]
    assert len(primaries) == 1, [s.name for s in primaries]
    assert primaries[0].version_fragment(config.settings), (
        f"{modality}/{primaries[0].name}: the base dialect must be non-empty when enabled"
    )


@pytest.mark.parametrize("modality", MODALITIES)
def test_no_required_stage_sits_downstream_of_a_best_effort_one(config, modality):
    """R3(a)'s cone rule (`executor.py:202-219`): a required stage behind a skippable one
    has a hollow promise. Checked over the enabled set of every configuration."""
    enabled = {s.name: s for s in _enabled(_live(modality), config.settings)}
    for s in enabled.values():
        if s.policy != "required":
            continue
        for need in s.needs:
            upstream = enabled.get(need)
            assert upstream is None or upstream.policy == "required", (
                f"{modality}/{s.name} is required but needs best_effort {need}"
            )


@pytest.mark.parametrize("modality", MODALITIES)
def test_resolvers_are_pure_functions_of_configuration(config, modality):
    """R4's determinism, at the resolver level: `is_enabled` / `version_fragment` must be
    functions of (settings, env) alone. Two calls with nothing in between must agree —
    a resolver that consulted a model, a clock, or a cache would show up here."""
    for s in _live(modality):
        assert s.is_enabled(config.settings) == s.is_enabled(config.settings)
        assert s.version_fragment(config.settings) == s.version_fragment(config.settings)


def test_the_graph_resolves_and_the_dialect_is_stable(config):
    """End of the chain: with the law satisfied, the real resolver must produce a graph
    and a `pipeline_version` — and the same one twice. This is the composed consequence
    of every fragment rule above."""
    for modality in MODALITIES:
        first = resolve(modality, _live(modality), config.settings)
        second = resolve(modality, _live(modality), config.settings)
        assert first.pipeline_version == second.pipeline_version
        assert first.pipeline_version  # never empty: the primary's base dialect is in it


# --------------------------------------------------------------------------------------
# §4.4 — the 18-row worked table
# --------------------------------------------------------------------------------------

# Every row of §4.4, with its verdict and where it is enforced. A row is either encoded
# as an assertion below (`test_...`) or carries the reason it cannot be — the map itself
# is asserted complete, so a future row cannot be quietly dropped.
TABLE_ROWS: dict[int, tuple[str, str]] = {
    1:  ("PRIMARY", "test_row1_clip_primary_is_one_unforked_caption_record"),
    2:  ("RETIRED", "test_row2_per_keyframe_records_are_retired_in_clip_mode"),
    3:  ("RECORD",  "test_row3_ocr_is_one_self_anchored_record_with_a_fragment"),
    4:  ("NOT EMITTED", "not mechanically checkable here: bbox/confidence are stage-local "
                        "variables in app/vision/ocr/assemble.py; their absence from C2 is "
                        "the ProcessedContent shape — see test_row4_and_17_c2_has_no_bbox_"
                        "or_quality_field for the shape half"),
    5:  ("field of the OCR record's text", "prose: region role is rendered INTO the OCR "
                                           "text (tests/test_ocr_assemble.py)"),
    6:  ("field of the caption's text", "prose: the `app` key of the guided-JSON schema "
                                        "(tests/test_clipcap.py)"),
    7:  ("inside the OCR record", "prose: role=titlebar, same channel as row 5"),
    8:  ("caption content", "prose: no `intent` stage exists — an absence cannot be "
                            "asserted over a registry, only never added"),
    9:  ("SLOT", "test_row9_activity_is_a_slot_not_a_record"),
    10: ("caption content", "prose: the idle path emits the same one caption record — "
                            "covered by row 1's set-stability assertion"),
    11: ("MUTATE", "test_row11_speaker_identity_is_a_mutate_with_a_mandatory_fragment"),
    12: ("DEFER", "prose: no face stage exists (see row 8)"),
    13: ("not a mutate", "test_row13_no_mutate_can_be_permanently_unrunnable"),
    14: ("RECORD, additive", "test_row14_and_15_additive_sidecars_declare_no_fragment"),
    15: ("RECORD, additive (latent hole flagged)",
         "test_row14_and_15_additive_sidecars_declare_no_fragment"),
    16: ("not a mutate, not a second record",
         "prose: the re-read is in-stage; there is no second OCR record to assert — "
         "row 3's exactly-one-unit assertion is the observable half"),
    17: ("NOT EMITTED", "test_row4_and_17_c2_has_no_bbox_or_quality_field"),
    18: ("NOT-C2", "test_row18_no_stage_claims_a_cross_chunk_span"),
}


def test_the_worked_table_is_covered_row_for_row():
    """The table has 18 rows; every one is either an assertion in this file or an
    explicit, reasoned exception. Adding a row without deciding which is a red test."""
    assert sorted(TABLE_ROWS) == list(range(1, 19))
    encoded = {n for n, (_, where) in TABLE_ROWS.items() if where.startswith("test_")}
    for name in {where for _, where in TABLE_ROWS.values() if where.startswith("test_")}:
        assert name in globals(), f"table row points at missing test {name}"
    # Rows whose verdict is a stage-level fact must be mechanically enforced, not prose.
    assert {1, 2, 3, 9, 11, 13, 14, 15, 17, 18} <= encoded


def test_row1_clip_primary_is_one_unforked_caption_record(config):
    """Row 1 — the clip description is the PRIMARY: exactly ONE `kind='caption'` unit,
    `discriminator=""`, `t_start=None` (so `build_c2` carries the C1 span verbatim), and
    the record set does not depend on what the model said (R4)."""
    if not config.label.startswith("clip-"):
        pytest.skip("clip-mode configurations only")
    clipcap = {s.name: s for s in _live("video")}["clipcap"]
    assert clipcap.kind == "primary" and clipcap.policy == "required"
    assert clipcap.is_enabled(config.settings)
    assert clipcap.version_fragment(config.settings)  # the base dialect (§4.2 T3)

    vs = get_vision_settings()
    verbose = ClipDesc(app="Mail", activity="writing", description="A long description. " * 20,
                       sensitive=False, raw="{}", parsed=True)
    terse = ClipDesc(app="unknown", activity="idle", description="Nothing changed.",
                     sensitive=False, raw="", parsed=False)
    for desc in (verbose, terse):
        unit = emit.caption_unit(desc, 60.0, vs)
        assert isinstance(unit, ProcessedUnit)
        assert unit.content.kind == "caption"
        assert unit.discriminator == ""       # the canonical 1:1 record id
        assert unit.t_start is None and unit.t_end is None
        assert "\n" not in unit.content.text  # D-12: one line


def test_row2_per_keyframe_records_are_retired_in_clip_mode(config):
    """Row 2 — the per-keyframe caption record is RETIRED. In clip mode the enabled video
    set is exactly the three clip stages; the two legacy stages that minted 4 records per
    chunk are dormant, so no configuration emits both shapes."""
    if not config.label.startswith("clip-"):
        pytest.skip("clip-mode configurations only")
    enabled = {s.name for s in _enabled(_live("video"), config.settings)}
    assert enabled == {"clipprep", "screentext", "clipcap"}


def test_row3_ocr_is_one_self_anchored_record_with_a_fragment(config, monkeypatch):
    """Row 3 — OCR is its own RECORD (own frozen `kind` → own day-log line) and its
    config forks the dialect because it is an INPUT to the caption (D-09). Also R3(e) +
    R4: the record exists ALWAYS, with `content.text == ""` when nothing was legible —
    presence is the coverage signal, and the set never depends on the read's outcome."""
    if not config.label.startswith("clip-"):
        pytest.skip("clip-mode configurations only")
    screentext = {s.name: s for s in _live("video")}["screentext"]
    assert screentext.kind == "sidecar" and screentext.policy == "required"
    assert screentext.version_fragment(config.settings).startswith("+ocr-")

    # Drive the real stage with a frame set that yields NOTHING to read (no OCR times):
    # the unit must still be emitted, exactly one, with the fixed discriminator. Skipped
    # for `ppocr`, whose `assert_health` pre-check reaches the co-located sidecar over
    # HTTP by design (D-06) — house rule 5 keeps this suite offline; the declaration half
    # above still runs for every backend.
    if ocr._resolve(get_ocr_config()) == "ppocr":
        pytest.skip("ppocr's health pre-check needs the sidecar; offline suite (rule 5)")
    ctx = StageContext(
        c1={"chunk_id": "c-law", "t_start": "2026-07-25T00:00:00Z"},
        blob=b"", settings=config.settings, span_seconds=60.0,
        slots={"clip_frames": ClipFrames(frames=(), ocr_times=(), idle=True, span_s=60.0)},
    )
    result = screentext.run_sync(ctx)
    assert len(result.units) == 1
    unit = result.units[0]
    assert unit.content.kind == "ocr" and unit.content.text == ""
    assert unit.discriminator == "ocr"
    assert unit.t_start is None and unit.t_end is None
    assert result.slots["ocr_text"] == ""  # the injected block, same string, no fork


def test_row9_activity_is_a_slot_not_a_record(config):
    """Row 9 — scroll/typing activity is a stage-internal SLOT: it drives the idle gate
    and the OCR event selection, and is emitted as no record. `clipprep` is the stage
    that computes it, and it emits zero units by construction (`provides` only)."""
    if not config.label.startswith("clip-"):
        pytest.skip("clip-mode configurations only")
    clipprep = {s.name: s for s in _live("video")}["clipprep"]
    assert clipprep.kind == "sidecar"
    assert set(clipprep.provides) == {"clip_frames", "delta", "vision_settings"}
    # A prep sidecar that started emitting units would be a new record class owing R5 a
    # consumer and a character budget; the delta/activity trace has neither.
    assert "delta" in clipprep.provides


def test_row11_speaker_identity_is_a_mutate_with_a_mandatory_fragment(config):
    """Row 11 — a name for a diarized label is a T4 structure-fill: MUTATE on the audio
    primary, chained by (order, name), fragment MANDATORY. The mandate is structural:
    enabledness IS the fragment, so the stage cannot fill speakers under the undiarized
    dialect."""
    by_name = {s.name: s for s in _live("audio")}
    diarize = by_name["diarize"]
    primary = by_name["asr"]
    assert diarize.kind == "mutate"
    assert set(diarize.writes) <= set(primary.mutable_slots)
    assert not type(diarize)._defines("enabled")
    assert diarize.is_enabled(config.settings) == bool(
        diarize.version_fragment(config.settings)
    )


def test_row13_no_mutate_can_be_permanently_unrunnable(monkeypatch):
    """Row 13 — geolocation is NOT a mutate stage, and the reason generalises: "a mutate
    with an empty fragment can never run". So every registered mutate must be enabled in
    at least one configuration of the matrix; one that never is would be dead code
    masquerading as a rule. This test quantifies over the WHOLE matrix, so it applies the
    environments itself instead of taking one row from the `config` fixture."""
    runnable: set[tuple[str, str]] = set()
    mutates = {(m, s.name) for m in MODALITIES for s in _live(m) if s.kind == "mutate"}
    for label, env in _MATRIX.items():
        del label
        for key in _ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("ASR_BACKEND", "mock")
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        settings = get_settings()
        for modality in MODALITIES:
            for s in _live(modality):
                if s.kind == "mutate" and s.version_fragment(settings):
                    runnable.add((modality, s.name))
    assert mutates  # the rule is not vacuous: at least one mutate ships
    assert mutates == runnable, (
        f"mutates with no enabling configuration (they can never run): "
        f"{sorted(mutates - runnable)}"
    )


def test_row14_and_15_additive_sidecars_declare_no_fragment(config):
    """Rows 14/15 — translation and acoustic events are ADDITIVE sidecars: they add a
    record and feed nothing, so they declare NO fragment (forking the whole chunk's
    dialect on an additive toggle would re-key the primary for a change that never
    touched it). The guard that keeps this honest is `provides == ()`: row 15's flagged
    latent hole (a real acoustic backend that starts feeding something) turns the R1 test
    red the moment it declares a slot."""
    by_name = {s.name: s for s in _live("audio")}
    for name in ("translate", "acoustic", "injected_caption"):
        stage = by_name[name]
        assert stage.kind == "sidecar"
        assert stage.provides == (), f"{name} now provides a slot — R1 applies to it"
        assert stage.version_fragment(config.settings) == ""


def test_row4_and_17_c2_has_no_bbox_or_quality_field(config):
    """Rows 4 + 17 — OCR geometry/confidence and per-record quality FAIL T2: no consumer
    reads them. The mechanical half is the emitted shape: `content` carries exactly the
    four frozen C2 keys (no bbox, no quality), and `enrichments` is the present-but-empty
    four-key block, so nothing is stored that cannot be spent."""
    del config
    assert set(ProcessedContent.__dataclass_fields__) == {
        "kind", "text", "language", "segments"
    }
    assert set(empty_enrichments()) == {"speakers", "faces", "places", "objects"}
    assert all(v == [] for v in empty_enrichments().values())


def test_row18_no_stage_claims_a_cross_chunk_span(config):
    """Row 18 — a summary-of-summaries fails T1 (not a pure function of THIS chunk) and is
    continuum's job. Mechanically: no stage may declare a need on another modality's
    stages, and DP's whole surface is per-chunk — the registry is partitioned by modality
    and every `needs` resolves inside it (`_discover`'s post-check). Asserted here so a
    cross-modality/day-context stage cannot slip in as a DP stage."""
    del config
    for modality in MODALITIES:
        names = {s.name for s in _live(modality)}
        for s in _live(modality):
            assert set(s.needs) <= names, (
                f"{modality}/{s.name}: needs outside its modality — a cross-chunk or "
                "cross-stream dependency is NOT-C2 (T1), it belongs to continuum"
            )
