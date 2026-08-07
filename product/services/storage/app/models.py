"""Pydantic models mirroring the frozen contract shapes (C3 nested, C4, C6).

These mirror the JSON Schemas in ``product/contracts/`` field-for-field
(``extra="forbid"`` == ``additionalProperties: false``). The JSON Schemas remain the
authoritative gate on the write path; these give typed access + a second, independent
check that our shapes still line up.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["user", "system"]
Surface = Literal["computer", "extension", "mobile", "wearable"]
Modality = Literal["text", "speech", "image", "video"]
# C1/C2 capture modalities differ from C3's ("audio" vs "speech"); keep them distinct.
CaptureModality = Literal["audio", "image", "video", "text"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Message(_Strict):
    role: Role
    text: str


class ClientCapabilities(_Strict):
    surface: Surface
    modalities: list[Modality]
    can_render_markdown: bool


class UserPrompt(_Strict):
    """C3 UserPrompt v0 (text-only) — nested inside a C4 turn record."""

    contract: Literal["C3"]
    version: Literal["0"]
    user_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    created_at: str
    messages: list[Message] = Field(min_length=1)
    client_capabilities: ClientCapabilities
    template_version: str


class TurnRecord(_Strict):
    """C4 turn record v0 — the unit persisted in ``/sessions``."""

    contract: Literal["C4"]
    version: Literal["0"]
    user_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    user_prompt: UserPrompt
    response_text: str
    model_id: str
    adapter: str
    created_at: str
    completed_at: str
    tool_traces: list[Any]
    mentor_traces: list[Any]


class ResolveResponse(_Strict):
    """C6 resolve v0 — the model-directory resolution body inference reads per request."""

    model_id: str
    adapter: str
    adapter_path: str | None


# --- C2 v1 processed record (learn-loop /context; DP rebuild, D24) ---------------
#
# The branch mirrors C2 **v1** exclusively (founder ruling R1, 2026-08-06): one record
# per chunk, content is a slots map, and the v0 concepts — content.kind/text,
# enrichments, discriminator, source.modality, processed_at — do not exist here. The
# frozen `c2_processed_record.v1.json` stays the authoritative gate; this mirror is the
# second, independent check, restated from the contract (services do not import each
# other's code, so DP's own mirror is not imported — the ids.py precedent).

# The producing stage's dialect segment (`<stage>.v<S>-<backend>.v<B>[.exp-<code>]`)
# and the `+`-joined sorted composition. Anchored and rust-regex-matched by pydantic,
# where `$` is end-of-haystack — so the mirror also rejects the trailing-newline shape
# `validate_c2`'s fullmatch closes at the schema gate.
SLOT_VERSION_PATTERN = r"^[a-z0-9_]+\.v[0-9]+-[a-z0-9_]+\.v[0-9]+(\.exp-[a-z0-9_]+)?$"
PIPELINE_VERSION_PATTERN = (
    r"^[a-z0-9_]+\.v[0-9]+-[a-z0-9_]+\.v[0-9]+(\.exp-[a-z0-9_]+)?"
    r"(\+[a-z0-9_]+\.v[0-9]+-[a-z0-9_]+\.v[0-9]+(\.exp-[a-z0-9_]+)?)*$"
)


class AsrSplit(_Strict):
    t_start: str
    t_end: str
    value: str


class AsrSlot(_Strict):
    """`value` == "" is the honest empty claim (ASR ran, VAD found no speech — L11)."""

    version: str = Field(pattern=SLOT_VERSION_PATTERN)
    language: str | None = None
    value: str
    splits: list[AsrSplit] | None = None


class DiarizationSplit(_Strict):
    t_start: str
    t_end: str
    speaker: str


class DiarizationSlot(_Strict):
    version: str = Field(pattern=SLOT_VERSION_PATTERN)
    splits: list[DiarizationSplit]


class TranscriptSplit(_Strict):
    t_start: str
    t_end: str
    value: str
    # Required-nullable: the key is always present, null where alignment found no turn.
    speaker: str | None


class TranscriptSlot(_Strict):
    """The speaker-aligned transcript — C10 v2 routes THIS slot's splits into the
    day-log's speech lines, each bucketed by its own t_start."""

    version: str = Field(pattern=SLOT_VERSION_PATTERN)
    splits: list[TranscriptSplit]


class AcousticSlot(_Strict):
    """`values: []` is ran-and-nothing-detected, an honest empty claim."""

    version: str = Field(pattern=SLOT_VERSION_PATTERN)
    values: list[str]
    confidence: float | None = Field(default=None, ge=0, le=1)


class CaptionSlot(_Strict):
    version: str = Field(pattern=SLOT_VERSION_PATTERN)
    value: str


class OcrSlot(_Strict):
    """`value` == "" is ran-and-empty (screen had no legible text), distinct from the
    slot being absent, which is a hole (the stage failed) — L11."""

    version: str = Field(pattern=SLOT_VERSION_PATTERN)
    value: str


class SlotsV1(_Strict):
    """The six pinned slot types. An unknown slot name fails closed (extra="forbid" ==
    the contract's additionalProperties:false); an EMPTY map is legal — the
    all-optional-failure record under L7. New slot types land here additively when
    their first producer ships, moving with the contract edit."""

    asr: AsrSlot | None = None
    diarization: DiarizationSlot | None = None
    transcript: TranscriptSlot | None = None
    acoustic: AcousticSlot | None = None
    caption: CaptionSlot | None = None
    ocr: OcrSlot | None = None


class ContentV1(_Strict):
    slots: SlotsV1


class DeviceLocation(_Strict):
    """Where the capturing device was, when it could say (C1 -> C2 passthrough)."""

    lat: float | None = None
    lon: float | None = None
    accuracy_m: float | None = None


class Source(_Strict):
    """Provenance back to the raw chunk in /raw — verbatim from C1 minus modality,
    which is root-level in v1 (a C1 chunk is strictly single-modality, so the record's
    modality is a fact about the record, not merely provenance)."""

    device_id: str = Field(min_length=1)
    stream_id: str = Field(min_length=1)
    chunk_id: str = Field(min_length=1)
    blob_ref: str = Field(min_length=1)
    # The D17 civil-time trio, carried verbatim from C1 by data-processing —
    # device_clock included (the v0 schema omitted it; v1 closes the gap). This model
    # is _Strict and is checked as a MIRROR of the frozen schema: an additive schema
    # field not declared here passes the schema gate and is then rejected here, so the
    # two must move together. NULL/absent = the device didn't report one.
    device_tz: str | None = Field(default=None, min_length=1)
    device_utc_offset_minutes: int | None = Field(default=None, ge=-1080, le=1080)
    device_clock: Literal["synced", "unsynced"] | None = None
    device_location: DeviceLocation | None = None


class ProcessedRecord(_Strict):
    """C2 processed record v1 — the unit persisted in ``/context``.

    ``record_id`` = sha256(chunk_id NUL pipeline_version), 64 lowercase hex; the length
    bounds close the trailing-newline trap alongside the pattern (the c10 ``window_id``
    precedent). Storage-side timestamps (``created_at``/``updated_at``) are NOT in C2 —
    storage assigns them (D27).
    """

    contract: Literal["C2"]
    version: Literal["1"]
    record_id: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    user_id: str = Field(min_length=1)
    modality: CaptureModality
    source: Source
    t_start: str
    t_end: str
    content: ContentV1
    pipeline_version: str = Field(min_length=1, pattern=PIPELINE_VERSION_PATTERN)


# --- C12 per-user profile (D18) -------------------------------------------------


class UserProfile(_Strict):
    """C12 user profile v0 — the per-user POLICY row.

    Mirrors ``contracts/c12_user_profile.v0.json`` field-for-field; the JSON Schema
    stays the authority (this service validates its own output against it before
    serving, the same self-check ``/model-directory/resolve`` does for C6).

    ``home_tz`` carries no default and no fallback: there is no server-side default
    timezone anywhere in the system, so this model has no default for it either — a
    profile that cannot name a zone is not a profile.
    """

    contract: Literal["C12"]
    version: Literal["0"]
    user_id: str = Field(min_length=1)
    home_tz: str = Field(min_length=1)
    profile_version: int = Field(ge=1)
    updated_at: str


class ProfileWrite(_Strict):
    """Request body of the profile write. Only ``home_tz`` is settable.

    ``profile_version`` and ``updated_at`` are storage-minted and deliberately NOT
    accepted from the caller (``_Strict`` makes sending them a 422): a monotone counter
    a client could set is not monotone.
    """

    home_tz: str = Field(min_length=1)


# --- training-window ledger (D18, C10 evolved) ----------------------------------

WindowState = Literal["open", "consolidated"]
# The cycle outcomes. `published` is the ONLY one that advances the watermark;
# `skipped_no_data` deliberately does not (refined 2026-07-27).
WindowOutcome = Literal["published", "gate_failed", "frozen", "skipped_no_data", "crashed"]


class TrainingWindow(_Strict):
    """One row of the training-window ledger, as served.

    Mirrors ``contracts/c10_training_window.v1.json`` field-for-field; that schema stays
    the AUTHORITY and this service validates its own output against it before serving
    (``schemas.validate_c10_window``), exactly as it does for C6/C10/C12/C14. The schema
    is a SIBLING of the day-log schema rather than an extension of it because C10 is
    a family of operations and these are two different bodies on two different endpoints.

    Note that the schema is strictly STRONGER than this model, which is the point of
    keeping both: pydantic can type ``state`` and ``outcome`` independently but cannot say
    that the two AGREE, so a ``consolidated`` row carrying a null ``outcome`` passes here
    and is caught only by the schema's ``if``/``then`` pair. That row would be a night
    whose training status is unanswerable — ``last_trained_t`` is derived by selecting
    rows whose outcome is ``published``.

    Storage-minted end to end, so it declares a strict response model (the convention
    in this service: verbatim user data goes out as a bare JSONResponse, our own bodies
    go out through a model). ``window_id`` is OPAQUE to every consumer — it may be
    compared with ``<`` / ``>=`` and nothing else, and it is constructed in exactly one
    place (``app.window_id.mint_window_id``). It is a PER-USER token — it does not name
    a window on its own, which is why ``user_id`` rides beside it everywhere.
    """

    window_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    t_start: str
    t_end: str
    state: WindowState
    outcome: WindowOutcome | None = None
    opened_at: str
    closed_at: str | None = None


class WindowOpen(_Strict):
    """Request body of the idempotent window open. The bounds are NOT settable —
    they are facts about storage's own ingest clock, which is why the ledger lives
    here rather than in continuum."""

    user_id: str = Field(min_length=1)


class WindowClose(_Strict):
    """Request body of the window close.

    ``outcome`` is a CLOSED enum: an unrecognised outcome must be a 422, never a silent
    non-advance (or, worse, a silent advance).

    ``user_id`` is required because ``window_id`` is a per-user token — it is derived
    from an instant, so two users' windows can legitimately share one, and a close keyed
    on the id alone would be a cross-user write to anyone who can guess a second. This
    is the same ``(user_id, window_id)`` addressing the day-log fetch uses.
    """

    user_id: str = Field(min_length=1)
    outcome: WindowOutcome


# --- C10 day-log fetch (D18) ----------------------------------------------------


class DayLogAsrLine(_Strict):
    """One speech line inside a segment bucket. A diarized C2 transcript contributes one
    of these PER SUB-SPAN, each bucketed by its own ``t``."""

    spk: str | None
    text: str
    t: str


class DayLogSegment(_Strict):
    seg_id: str = Field(min_length=1)
    t_start: str
    t_end: str
    caption: list[str]
    asr: list[DayLogAsrLine]
    ocr: list[str]
    # null == NOT SCORED, which is not the same as zero: C2 v0 carries no quality field,
    # and an unscored row must pass the amplification gate rather than fail it.
    quality: float | None = None
    tz: str | None = None


class DayLogBlock(_Strict):
    block_id: str = Field(min_length=1)
    seg_ids: list[str]
    text: str
    # Deliberately open (dict, not a model): the contract allows additive anchor keys —
    # `place` lands when geo enrichment does — and a strict mirror here would turn an
    # additive contract change into a 500 on the read path.
    anchors: dict[str, Any]
    quality: float | None = None


class DayLogBody(_Strict):
    """C10 v2 — the day-log fetch body (D28: the slot-walk renderer over C2 v1).

    Mirrors ``contracts/c10_daylog.v2.json``; the JSON Schema stays the authority and this
    service validates its own output against it before serving. C10 EVOLVED in place from
    the raw C2 range read (which is not retired and keeps its own, event-time, semantics).

    ``t_start``/``t_end`` are the window's ``updated_at``-axis bounds (D27), not the
    extent of the rendered content — segment timestamps routinely precede ``t_start``,
    which is the late-upload case working correctly.
    """

    contract: Literal["C10"]
    version: Literal["2"]
    user_id: str = Field(min_length=1)
    window_id: str = Field(min_length=1)
    t_start: str
    t_end: str
    daylog_format_version: str = Field(min_length=1)
    recipe_id: str = Field(min_length=1)
    # The FALLBACK zone actually used, read from the C12 profile at materialization time.
    # Not the zone every block rendered in: a block prefers the capturing device's own
    # device_tz, so a travelling user's blocks carry different zones and different dates.
    home_tz: str = Field(min_length=1)
    segments: list[DayLogSegment]
    blocks: list[DayLogBlock]
    content_fingerprint: str = Field(min_length=1)


# --- C14 reservoir (D18) --------------------------------------------------------
#
# There is deliberately NO pydantic mirror of a C13 recipe or gate policy. Those are
# served VERBATIM off disk (a `JSONResponse`, the convention this service uses for any
# body it stores rather than mints), so a strict mirror would either strip the human
# provenance prose those artifacts carry or 500 on it — and continuum's `load_recipe`
# must be able to read the response unchanged. The frozen C13 schemas are the gate.


class ReservoirEntry(_Strict):
    """One admitted corpus, as it appears in the ledger AND as the admission ack.

    The same shape both times on purpose: admission is idempotent, so the ack for a fresh
    admission and the ack for a re-admission of identical content are byte-identical, and
    both equal the row the ledger will serve. An `admitted: true/false` flag would make the
    response differ between two calls that are defined to be the same call.

    Mirrors the ``entries`` items of ``contracts/c14_reservoir_ledger.v0.json``. What it
    does NOT carry is the corpus body or its path on disk: the ledger answers "what has
    been admitted", not "give me the text", and a path is a fact about this server's disk.
    """

    user_id: str = Field(min_length=1)
    window_id: str = Field(min_length=1)
    recipe_id: str = Field(min_length=1)
    sha: str = Field(min_length=64, max_length=64)
    chars: int = Field(ge=0)
    admitted_at: str


class ReservoirLedger(_Strict):
    """C14 v0 — the reservoir ledger body. Mirrors the frozen schema; storage validates
    its own output against that schema before serving."""

    contract: Literal["C14"]
    version: Literal["0"]
    user_id: str = Field(min_length=1)
    # Echoed back so a cached response is unambiguous about what it filtered.
    before_window: str | None = None
    entries: list[ReservoirEntry]


class ReservoirAdmit(_Strict):
    """Request body of an admission. ``user_id`` and ``window_id`` are path segments, not
    body fields — they are the address, and carrying them twice invites the two copies to
    disagree.

    ``sha``/``chars``/``admitted_at`` are storage-minted and deliberately NOT settable: a
    content hash the caller supplies is not a content hash, it is a claim. (``/raw`` takes
    a declared sha256 and VERIFIES it because the bytes crossed a device boundary; here the
    text in the request body IS the artifact, so there is nothing to verify against.)
    """

    recipe_id: str = Field(min_length=1)
    # No minimum length: an empty corpus is a defect upstream, but an append-only audit
    # store that refuses what it is given loses the evidence of the defect.
    corpus_text: str


# --- E-2 whole-record retraction (D28) ------------------------------------------


class RetractionSelector(_Strict):
    """The selector echoed on the manifest — whichever of the three whole-record axes
    the caller named (they AND together over the mandatory user_id)."""

    record_id: str | None = None
    chunk_id: str | None = None
    pipeline_version: str | None = None


class RetractionManifest(_Strict):
    """E-2's auditable manifest (D28): whole-record counts by ``pipeline_version``,
    plus the day-log cascade's blast radius. Storage-minted end to end, hence a strict
    response model; there is no cross-service contract file — E-2 is a service-owned
    retention primitive that Platform's M2 orchestration will call, and its shape is
    pinned on the D28 card."""

    user_id: str = Field(min_length=1)
    dry_run: bool
    selector: RetractionSelector
    records: int = Field(ge=0)
    by_pipeline_version: dict[str, int]
    day_logs_invalidated: int = Field(ge=0)


class TurnWriteAck(_Strict):
    ok: bool
    turn_id: str


class ContextWriteAck(_Strict):
    ok: bool
    record_id: str


class BlobWriteAck(_Strict):
    blob_ref: str
    bytes: int
    sha256: str


class Health(_Strict):
    ok: bool
