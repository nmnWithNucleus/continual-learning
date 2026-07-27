"""Storage service (:8083) — FastAPI app for the serve-loop + learn-loop MVP.

Serve-loop (v0.0):
  POST /sessions/turns                 -> validate a C4 turn record, persist, {ok, turn_id}
  GET  /sessions/turns/{turn_id}       -> the stored C4 (404 if absent)
  GET  /sessions/{session_id}/turns    -> C4 turns for a session, ordered by created_at
  GET  /model-directory/resolve        -> C6 body {model_id, adapter, adapter_path}

Learn-loop (capture M0) — the /raw blob leg (C1) + the /context store (C2):
  PUT  /raw/blobs?user_id=&device_id=&chunk_id=&codec=&sha256=&bytes=
       body = raw bytes (application/octet-stream). Verifies sha256+bytes, mints an opaque
       blob_ref, stores the bytes; idempotent on chunk_id. -> {blob_ref, bytes, sha256}
  GET  /raw/blobs?ref=<blob_ref>       -> the raw bytes (ref is a query param, may contain '/';
       404 if unknown OR since-deleted)
  POST /context/records                -> validate a C2 record, idempotent upsert, {ok, record_id}
  GET  /context/records/{record_id}    -> the stored C2 (404 if absent)
  GET  /context/records?user_id=&from=&to=  -> C2 records for a user, ordered by t_start
                                               (window is half-open [from, to); bounds optional)

Learn-loop (D18) — the per-user profile (C12) + the training-window ledger (C10 evolved):
  GET  /users/{user_id}/profile        -> the C12 profile body (404 if the user has none —
       never a default: there is no server-side default timezone anywhere in the system)
  PUT  /users/{user_id}/profile        -> {home_tz} -> upsert, bumps profile_version.
       home_tz is resolved against tzdata on write; an abbreviation ("PST") or an
       unknown IANA id is a 400.
  POST /training/windows               -> {user_id} -> IDEMPOTENT get-or-create of the
       user's open window over [last_trained_t, now-delta). Returns an already-open
       window UNCHANGED; bounds are immutable once opened.
  GET  /training/windows?user_id=&state=    -> the user's windows, oldest first
  POST /training/windows/{window_id}/close  -> {user_id, outcome} -> consolidate. The
       watermark advances IFF outcome == "published". user_id is required because
       window_id is a PER-USER token — addressing is always (user_id, window_id).
  GET  /training/daylog?user_id=&window_id=  -> the C10 v1 day-log for that window:
       segment rows + rendered scene blocks over every C2 record whose INGEST_TIME fell
       in the window. Materialized on demand and cached. 409 if the user has no C12
       profile (no fallback zone => not schedulable, an alert rather than a silent
       UTC render).

Learn-loop (D18) — the recipe registry (C13) + the training reservoir (C14):
  GET  /recipes/{recipe_id}            -> the versioned training recipe, VERBATIM.
       recipe_id == the filename stem; GLOBAL and versioned, never per-user.
  GET  /policies/{policy_id}           -> the gate policy, VERBATIM. A SEPARATE artifact
       with its own id: only the training recipe may enter a cycle stage key, so a
       publish-threshold change must never fork recipe_id.
  POST /reservoir/{user_id}/{window_id}  -> {recipe_id, corpus_text} -> append-only,
       content-hashed admission of a night's amplified corpus. Re-admitting identical
       content is a no-op; different content under the same key is a 409, never an
       overwrite. Retention is keep-everything: no sweeper, no expiry.
  GET  /reservoir/{user_id}?before_window=  -> the C14 LEDGER (window_id, recipe_id, sha,
       chars, admitted_at) — never the corpus bodies.

  GET  /health                         -> {ok: true}
"""
from __future__ import annotations

import hashlib
from typing import Any, Literal, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Body, FastAPI, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse, Response

from . import registry, schemas
from .daylog import DayLogRecipeUnusable, DayLogRefused, materialize_daylog
from .db import Store, WindowOutcomeConflict, WindowRefused
from .ids import PATH_ID_PATTERN, validate_path_id
from .models import (
    BlobWriteAck,
    ContextWriteAck,
    DayLogBody,
    Health,
    ProcessedRecord,
    ProfileWrite,
    ReservoirAdmit,
    ReservoirEntry,
    ReservoirLedger,
    ResolveResponse,
    TrainingWindow,
    TurnRecord,
    TurnWriteAck,
    UserProfile,
    WindowClose,
    WindowOpen,
)
from .reservoir import CorpusConflict, Reservoir, ledger_body
from .window_id import validate_window_id


def create_app() -> FastAPI:
    """App factory. Reads STORAGE_DB_PATH at call time, so tests can point at a temp DB."""
    app = FastAPI(
        title="Nucleus storage service",
        version="0.0",
        summary="Durable /sessions + model directory for the serve-loop MVP.",
    )
    store = Store()
    store.seed_base()
    app.state.store = store
    # The reservoir is a filesystem store beside the DB, constructed here for the same
    # reason Store is: it reads its root from the environment at create_app() time.
    reservoir = Reservoir()
    app.state.reservoir = reservoir

    @app.get("/health", response_model=Health)
    def health() -> Health:
        return Health(ok=True)

    @app.post("/sessions/turns", response_model=TurnWriteAck)
    def write_turn(record: dict[str, Any] = Body(...)) -> TurnWriteAck:
        # 1) Authoritative gate: validate against the frozen C4 JSON Schema.
        problems = schemas.validate_c4(record)
        if problems:
            raise HTTPException(
                status_code=422,
                detail={"error": "C4 schema validation failed", "violations": problems},
            )
        # 2) Mirror check: the pydantic model must agree with the schema.
        TurnRecord.model_validate(record)
        # 3) Persist the record verbatim (idempotent on turn_id).
        turn_id = store.put_turn(record)
        return TurnWriteAck(ok=True, turn_id=turn_id)

    @app.get("/sessions/turns/{turn_id}")
    def read_turn(turn_id: str) -> JSONResponse:
        record = store.get_turn(turn_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"turn_id {turn_id!r} not found")
        return JSONResponse(content=record)

    @app.get("/sessions/{session_id}/turns")
    def list_session_turns(session_id: str) -> JSONResponse:
        return JSONResponse(content=store.list_turns(session_id))

    @app.get("/model-directory/resolve", response_model=ResolveResponse)
    def resolve(user_id: str = Query(..., min_length=1)) -> ResolveResponse:
        body = store.resolve(user_id)
        # Contract-check our own output against the frozen C6 JSON Schema before serving.
        problems = schemas.validate_c6(body)
        if problems:  # pragma: no cover - would indicate a directory-data bug
            raise HTTPException(
                status_code=500,
                detail={"error": "C6 schema validation failed", "violations": problems},
            )
        return ResolveResponse(**body)

    # --- /raw blob leg (C1) -----------------------------------------------------

    @app.put("/raw/blobs", response_model=BlobWriteAck)
    async def put_raw_blob(
        request: Request,
        user_id: str = Query(..., min_length=1),
        chunk_id: str = Query(..., min_length=1),
        sha256: str = Query(..., min_length=1, description="SHA-256 hex of the blob bytes"),
        device_id: Optional[str] = Query(None),
        codec: Optional[str] = Query(None),
        blob_bytes: Optional[int] = Query(None, alias="bytes", ge=0),
    ) -> BlobWriteAck:
        data = await request.body()
        # End-to-end integrity: the bytes we received must match what recording claims.
        actual_sha = hashlib.sha256(data).hexdigest()
        if actual_sha.lower() != sha256.lower():
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "sha256 mismatch",
                    "declared": sha256,
                    "actual": actual_sha,
                },
            )
        if blob_bytes is not None and blob_bytes != len(data):
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "bytes mismatch",
                    "declared": blob_bytes,
                    "actual": len(data),
                },
            )
        result = store.put_blob(
            chunk_id=chunk_id,
            user_id=user_id,
            device_id=device_id,
            codec=codec,
            sha256=actual_sha,
            data=data,
        )
        return BlobWriteAck(**result)

    @app.get("/raw/blobs")
    def get_raw_blob(ref: str = Query(..., min_length=1)) -> Response:
        # ref is a QUERY param (not a path segment) because a blob_ref may contain '/'.
        data = store.get_blob(ref)
        if data is None:
            raise HTTPException(status_code=404, detail=f"blob ref {ref!r} not found")
        return Response(content=data, media_type="application/octet-stream")

    # --- /context store (C2) ----------------------------------------------------

    @app.post("/context/records", response_model=ContextWriteAck)
    def write_context_record(record: dict[str, Any] = Body(...)) -> ContextWriteAck:
        # 1) Authoritative gate: validate against the frozen C2 JSON Schema.
        problems = schemas.validate_c2(record)
        if problems:
            raise HTTPException(
                status_code=422,
                detail={"error": "C2 schema validation failed", "violations": problems},
            )
        # 2) Mirror check: the pydantic model must agree with the schema.
        ProcessedRecord.model_validate(record)
        # 3) Persist verbatim, time-indexed on (user_id, t_start); idempotent on record_id.
        record_id = store.put_context(record)
        return ContextWriteAck(ok=True, record_id=record_id)

    @app.get("/context/records/{record_id}")
    def read_context_record(record_id: str) -> JSONResponse:
        record = store.get_context(record_id)
        if record is None:
            raise HTTPException(
                status_code=404, detail=f"record_id {record_id!r} not found"
            )
        return JSONResponse(content=record)

    @app.get("/context/records")
    def list_context_records(
        user_id: str = Query(..., min_length=1),
        from_ts: Optional[str] = Query(
            None, alias="from", description="Window start, RFC3339 UTC (inclusive)"
        ),
        to_ts: Optional[str] = Query(
            None, alias="to", description="Window end, RFC3339 UTC (exclusive)"
        ),
    ) -> JSONResponse:
        return JSONResponse(content=store.list_context(user_id, from_ts, to_ts))

    # --- per-user profile (C12) -------------------------------------------------

    @app.get("/users/{user_id}/profile", response_model=UserProfile)
    def read_profile(user_id: str = Path(..., min_length=1)) -> UserProfile:
        body = store.get_profile(user_id)
        if body is None:
            # 404, NEVER a default. There is no server-side default timezone anywhere
            # in the system (D17), so "no profile" means this user is not schedulable —
            # an operational alert, not a silent fallback to UTC.
            raise HTTPException(
                status_code=404,
                detail=f"no profile for user_id {user_id!r} (home_tz is declared, "
                       f"not inferred — there is no default timezone)",
            )
        # Contract-check our own output against the frozen C12 schema before serving.
        problems = schemas.validate_c12(body)
        if problems:  # pragma: no cover - would indicate a profile-data bug
            raise HTTPException(
                status_code=500,
                detail={"error": "C12 schema validation failed", "violations": problems},
            )
        return UserProfile(**body)

    @app.put("/users/{user_id}/profile", response_model=UserProfile)
    def write_profile(
        user_id: str = Path(..., min_length=1),
        body: ProfileWrite = Body(...),
    ) -> UserProfile:
        # 1) Shape gate: the frozen C12 pattern, applied by validating a probe body so
        #    the pattern lives ONLY in the contract. It structurally excludes
        #    abbreviations ("PST"/"MST" are ambiguous and DST-sensitive) by requiring a
        #    region/city form, and admits bare "UTC" because that is a real zone.
        #    profile_version/updated_at are storage-minted below; the placeholders here
        #    exist only to make the probe a complete C12.
        probe = {
            "contract": "C12",
            "version": "0",
            "user_id": user_id,
            "home_tz": body.home_tz,
            "profile_version": 1,
            "updated_at": "1970-01-01T00:00:00Z",
        }
        problems = schemas.validate_c12(probe)
        if problems:
            raise HTTPException(
                status_code=400,
                detail={"error": "C12 schema validation failed", "violations": problems},
            )
        # 2) THE AUTHORITY: tzdata resolution. The pattern is a cheap shape gate and
        #    happily admits ids that do not exist ("Not/AZone"); only zoneinfo can say
        #    whether a zone is real. Same rule recording already applies at the capture
        #    edge — unknown IANA id => 400.
        try:
            ZoneInfo(body.home_tz)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "unknown IANA timezone",
                    "home_tz": body.home_tz,
                    "reason": str(exc),
                },
            ) from exc
        # 3) Persist. This is the ONLY writer of home_tz in the service — it is
        #    DECLARED, NOT INFERRED, so nothing seeds it from a device-reported
        #    device_tz and nothing updates it when the user travels.
        stored = store.put_profile(user_id, body.home_tz)
        problems = schemas.validate_c12(stored)
        if problems:  # pragma: no cover - would indicate a write-path bug
            raise HTTPException(
                status_code=500,
                detail={"error": "C12 schema validation failed", "violations": problems},
            )
        return UserProfile(**stored)

    # --- training-window ledger (D18, C10 evolved) ------------------------------

    def _checked_window(window: dict[str, Any]) -> TrainingWindow:
        """Contract-check a ledger row against the frozen C10 window schema, then hand
        back the typed mirror. Storage mints every field, so a violation is a 500.

        The schema is the AUTHORITATIVE gate and the pydantic model is the second check,
        never the only one — the same order `/context/records` applies to C2. It matters
        here specifically because the schema is strictly stronger than the model: only it
        can say that `state` and `outcome` AGREE, and a consolidated row with a null
        outcome would pass `TrainingWindow` while being a night whose training status is
        unanswerable (`last_trained_t` is derived by selecting `outcome = 'published'`).
        """
        problems = schemas.validate_c10_window(window)
        if problems:  # pragma: no cover - would indicate a ledger-write bug
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "C10 training-window schema validation failed",
                    "violations": problems,
                },
            )
        return TrainingWindow(**window)

    @app.post("/training/windows", response_model=TrainingWindow)
    def open_training_window(body: WindowOpen = Body(...)) -> TrainingWindow:
        # IDEMPOTENT get-or-create. A retry returns the SAME window_id and the SAME
        # bounds — recomputing `now` would mint a fresh id, a fresh journal, and
        # therefore a full re-train, a second C5 entry and a second reservoir admission.
        try:
            window = store.open_training_window(body.user_id)
        except WindowRefused as exc:
            raise HTTPException(status_code=409, detail=exc.detail) from exc
        return _checked_window(window)

    @app.get("/training/windows", response_model=list[TrainingWindow])
    def list_training_windows(
        user_id: str = Query(..., min_length=1),
        state: Optional[Literal["open", "consolidated"]] = Query(
            None, description="Filter by ledger state; omit for all"
        ),
    ) -> list[TrainingWindow]:
        # Element-wise: the enumeration is a bare JSON array with no envelope, so the
        # contract is over the ROW and every row is checked against it.
        return [_checked_window(w) for w in store.list_windows(user_id, state)]

    @app.post("/training/windows/{window_id}/close", response_model=TrainingWindow)
    def close_training_window(
        window_id: str, body: WindowClose = Body(...)
    ) -> TrainingWindow:
        # The one validator. A window_id that this service did not mint cannot name a
        # row, so reject it at the edge rather than 404-ing on a shape we know is wrong.
        if not validate_window_id(window_id):
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "malformed window_id",
                    "window_id": window_id,
                    "expected": "w<YYYYMMDD>T<HHMMSS>Z",
                },
            )
        try:
            window = store.close_training_window(body.user_id, window_id, body.outcome)
        except WindowOutcomeConflict as exc:
            raise HTTPException(status_code=409, detail=exc.detail) from exc
        if window is None:
            # Also what a cross-user attempt gets: isolation fails closed rather than
            # revealing that the id names somebody else's window.
            raise HTTPException(
                status_code=404,
                detail=f"window_id {window_id!r} not found for user_id {body.user_id!r}",
            )
        return _checked_window(window)

    # --- the C10 day-log fetch (D18) --------------------------------------------

    @app.get("/training/daylog", response_model=DayLogBody)
    def read_daylog(
        user_id: str = Query(..., min_length=1),
        window_id: str = Query(..., min_length=1),
    ) -> DayLogBody:
        # Addressed by (user_id, window_id) TOGETHER: window_id is a per-user token
        # derived from an instant, so two users' windows can legitimately share one and an
        # id-only fetch would be a cross-user read of somebody's whole day.
        if not validate_window_id(window_id):
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "malformed window_id",
                    "window_id": window_id,
                    "expected": "w<YYYYMMDD>T<HHMMSS>Z",
                },
            )
        try:
            # Materialized ON DEMAND and cached. There is no scheduler in this service and
            # adding one would be a new runtime dependency for a prototype whose only
            # caller asks for a given window's day-log once.
            body = materialize_daylog(store, user_id, window_id)
        except DayLogRefused as exc:
            # 409, not a UTC-rendered day-log: with no profile there is no fallback zone,
            # and there is no server-side default timezone anywhere in the system. A user
            # with no home_tz does not consolidate — visible, never silent.
            raise HTTPException(status_code=409, detail=exc.detail) from exc
        except DayLogRecipeUnusable as exc:
            # 500, not a fallback render: the body STAMPS recipe_id and the segmentation
            # knobs come out of that same recipe, so a recipe we cannot read is a day-log
            # we cannot honestly label. recipe_id is service config and never a request
            # parameter, so the caller cannot fix this — same posture as RegistryFault.
            raise HTTPException(status_code=500, detail=exc.detail) from exc
        if body is None:
            raise HTTPException(
                status_code=404,
                detail=f"window_id {window_id!r} not found for user_id {user_id!r}",
            )
        # Contract-check our own output against the frozen C10 schema before serving.
        problems = schemas.validate_c10(body)
        if problems:  # pragma: no cover - would indicate a materialization bug
            raise HTTPException(
                status_code=500,
                detail={"error": "C10 schema validation failed", "violations": problems},
            )
        return DayLogBody(**body)

    # --- the recipe registry (C13) ----------------------------------------------

    def _artifact_id_gate(kind: str, artifact_id: str) -> None:
        """One gate for both registry reads: an id that cannot be a path component is a
        422 at the edge, not a filesystem miss deeper in. This is what makes traversal
        structurally impossible rather than caught-by-luck — the resolver below never sees
        a separator or a '..'."""
        if not validate_path_id(artifact_id):
            raise HTTPException(
                status_code=422,
                detail={
                    "error": f"malformed {kind}_id",
                    f"{kind}_id": artifact_id,
                    "expected": PATH_ID_PATTERN,
                    "reason": "it becomes a filesystem path component",
                },
            )

    def _serve_artifact(kind: str, artifact_id: str, fetch, validate) -> JSONResponse:
        try:
            artifact = fetch(artifact_id)
        except registry.ArtifactMissing as exc:
            raise HTTPException(status_code=404, detail=exc.detail) from exc
        except registry.RegistryFault as exc:
            # 500: the request was fine, our own registry contents are not.
            raise HTTPException(status_code=500, detail=exc.detail) from exc
        problems = validate(artifact)
        if problems:
            raise HTTPException(
                status_code=500,
                detail={
                    "error": f"C13 {kind} schema validation failed",
                    f"{kind}_id": artifact_id,
                    "violations": problems,
                },
            )
        # VERBATIM, through a bare JSONResponse with no response_model — the convention
        # this service uses for any body it stores rather than mints. Continuum's
        # load_recipe()/load_policy() must be able to read this response unchanged, and the
        # artifacts carry provenance prose a strict model would strip.
        return JSONResponse(content=artifact)

    @app.get("/recipes/{recipe_id}")
    def read_recipe(recipe_id: str = Path(..., min_length=1)) -> JSONResponse:
        # No user_id anywhere on this surface, and there must never be one: recipe_id is
        # GLOBAL and versioned (== the filename stem) and it enters continuum's stage keys,
        # so a per-user recipe would fork the id per user and destroy comparability.
        _artifact_id_gate("recipe", recipe_id)
        return _serve_artifact(
            "recipe", recipe_id, registry.fetch_recipe, schemas.validate_c13_recipe
        )

    @app.get("/policies/{policy_id}")
    def read_policy(policy_id: str = Path(..., min_length=1)) -> JSONResponse:
        # A SEPARATE endpoint for a SEPARATE artifact, which is the contract itself:
        # `policy_id` is recorded in the gate report and must never enter a stage key, so
        # re-deciding what is shippable must never fork `recipe_id` or re-train anything.
        _artifact_id_gate("policy", policy_id)
        return _serve_artifact(
            "policy", policy_id, registry.fetch_policy, schemas.validate_c13_policy
        )

    # --- the training reservoir (C14) -------------------------------------------

    def _window_id_gate(window_id: str) -> None:
        if not validate_window_id(window_id):
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "malformed window_id",
                    "window_id": window_id,
                    "expected": "w<YYYYMMDD>T<HHMMSS>Z",
                },
            )

    @app.post("/reservoir/{user_id}/{window_id}", response_model=ReservoirEntry)
    def admit_corpus(
        user_id: str = Path(..., min_length=1),
        window_id: str = Path(..., min_length=1),
        body: ReservoirAdmit = Body(...),
    ) -> ReservoirEntry:
        _artifact_id_gate("user", user_id)
        _window_id_gate(window_id)
        _artifact_id_gate("recipe", body.recipe_id)
        try:
            entry = reservoir.admit(user_id, window_id, body.recipe_id, body.corpus_text)
        except CorpusConflict as exc:
            # 409, never an overwrite. The reservoir is append-only and a corpus is a fact
            # about a night that already happened; replacing it would delete evidence, and
            # deletion here is a deliberate privacy act, never a side effect of a retry.
            raise HTTPException(status_code=409, detail=exc.detail) from exc
        return ReservoirEntry(**entry)

    @app.get("/reservoir/{user_id}", response_model=ReservoirLedger)
    def read_reservoir_ledger(
        user_id: str = Path(..., min_length=1),
        before_window: Optional[str] = Query(
            None,
            description="Return only entries STRICTLY BEFORE this window_id "
                        "(string comparison on the opaque id; never a date)",
        ),
    ) -> ReservoirLedger:
        _artifact_id_gate("user", user_id)
        if before_window is not None:
            # Validated rather than passed through: this filter is a plain string
            # comparison, so a malformed value does not fail — it silently returns the
            # wrong set. "2026-07-21" sorts below every `w...` id and would empty the
            # ledger, and continuum's replay would then quietly train on no history at all.
            _window_id_gate(before_window)
        body = ledger_body(
            user_id, reservoir.entries(user_id, before_window=before_window), before_window
        )
        # Contract-check our own output against the frozen C14 schema before serving.
        problems = schemas.validate_c14(body)
        if problems:  # pragma: no cover - would indicate a ledger-projection bug
            raise HTTPException(
                status_code=500,
                detail={"error": "C14 schema validation failed", "violations": problems},
            )
        return ReservoirLedger(**body)

    return app


app = create_app()
