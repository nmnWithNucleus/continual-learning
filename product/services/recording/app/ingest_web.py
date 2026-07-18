"""Ingest router — the server half of the phone web client wire (WS-B <-> WS-C).

POST /ingest/segments                  — one self-contained A/V segment (raw bytes body).
                                         Idempotent on (session_id, seq): same sha again
                                         -> {status:"duplicate"} (counted, not re-emitted);
                                         different sha -> 409. sha256 param verified when
                                         non-empty, computed server-side when empty.
                                         Ack = spool + ledger row are durable. Async mode
                                         acks immediately; RECORDING_INGEST_SYNC=1 awaits
                                         this segment's demux+emit first (tests/small ops).
POST /ingest/sessions/{id}/end         — client end marker {last_seq}; fixes
                                         expected_segments so the report can name a lost tail.
GET  /ingest/sessions                  — per-session summaries.
GET  /ingest/sessions/{id}/report      — the continuity/gap report joining both legs
                                         (client->server seq, server->DP C1 sequence).
POST /ingest/sessions/{id}/retry       — re-enqueue this session's failed segments.

The report's DP side is checked LIVE against data-processing GET /continuity/{stream_id}
(short timeout; unreachable/unknown -> checked:false, never a fabricated verdict).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from . import clients, emitter, ledger, timeutil
from .config import Settings, get_settings

logger = logging.getLogger("recording.ingest")

router = APIRouter(prefix="/ingest")

# DP /continuity probe: short and best-effort — the report must not hang on DP.
_CONTINUITY_TIMEOUT = 2.0

# session_id becomes a spool directory name; keep it filesystem-safe (client mints
# ULID-ish ids, so this only ever rejects garbage/hostile input). '.'/'..' match the
# class but are path navigation, not names — rejected explicitly.
_SAFE_ID = re.compile(r"[A-Za-z0-9._-]+")

# Upper bound on a session's segment numbering: at 10 s/segment this is ~3 years of
# one session — anything larger is garbage/hostile, and bounding it keeps every
# per-seq structure (ledger rows, report gap walk) trivially sized.
MAX_SEQ = 9_999_999

# The report returns at most this many individual missing seqs (missing_count always
# carries the true total) so a pathological session can't balloon the payload.
_MISSING_LIST_CAP = 1000


def _ext_for_mime(mime: str) -> str:
    m = (mime or "").lower()
    if "mp4" in m:
        return ".mp4"
    if "webm" in m:
        return ".webm"
    return ".bin"


def _spool_path(
    settings: Settings, session_id: str, seq: int, mime: str, sha256: str
) -> Path:
    # Content-addressed (sha prefix in the name): a conflicting re-POST of the same
    # seq with different bytes can never clobber the original spooled bytes.
    name = f"{seq}.{sha256[:12]}{_ext_for_mime(mime)}"
    return Path(settings.var_dir) / "spool" / session_id / name


async def _read_body_capped(request: Request, max_bytes: int) -> bytes:
    """Buffer the raw segment body, refusing anything past ``max_bytes`` (413)."""
    parts: list[bytes] = []
    size = 0
    async for part in request.stream():
        size += len(part)
        if size > max_bytes:
            raise HTTPException(
                413, f"segment exceeds the {max_bytes}-byte limit (RECORDING_MAX_SEGMENT_MB)"
            )
        parts.append(part)
    return b"".join(parts)


# ------------------------------------------------------------------ segment upload

@router.post("/segments")
async def upload_segment(
    request: Request,
    session_id: str = Query(min_length=1),
    seq: int = Query(ge=0, le=MAX_SEQ),
    user_id: str = Query(min_length=1),
    device_id: str = Query(min_length=1),
    t_start: str = Query(min_length=1),
    t_end: str = Query(min_length=1),
    mime: str = Query(min_length=1),
    sha256: str = Query(default=""),
) -> dict:
    if not _SAFE_ID.fullmatch(session_id) or session_id in (".", ".."):
        raise HTTPException(400, "session_id must be filesystem-safe ([A-Za-z0-9._-])")
    try:
        timeutil.parse_wallclock(t_start)
        timeutil.parse_wallclock(t_end)
    except ValueError as exc:
        raise HTTPException(400, f"bad t_start/t_end: {exc}") from exc

    settings = get_settings()
    data = await _read_body_capped(request, settings.max_segment_bytes)
    if not data:
        raise HTTPException(400, "empty segment body")
    digest = hashlib.sha256(data).hexdigest()
    if sha256 and sha256.lower() != digest:
        raise HTTPException(400, f"sha256 mismatch: client sent {sha256}, body is {digest}")

    led = ledger.for_settings(settings)
    led.ensure_session(session_id, user_id=user_id, device_id=device_id, started_at=t_start)

    # SPOOL FIRST, ledger second — the ack contract is "spool + ledger row are
    # durable", and the client's retry pump treats ANY ok (received/duplicate) as
    # "the bytes are on the server" before dropping them from memory. If the row
    # committed first, a crash between row and spool would make the retry hit the
    # duplicate branch and ack bytes that exist nowhere. The spool name is
    # content-addressed, so this write can never clobber different bytes.
    spool = _spool_path(settings, session_id, seq, mime, digest)
    await asyncio.to_thread(_write_spool, spool, data)

    status, prior_state = led.record_segment(
        session_id,
        seq,
        sha256=digest,
        nbytes=len(data),
        mime=mime,
        t_start=t_start,
        t_end=t_end,
        received_at=timeutil.rfc3339(datetime.now(timezone.utc)),
        spool_path=str(spool),
    )
    if status == "conflict":
        spool.unlink(missing_ok=True)  # keep only the original seq's bytes spooled
        raise HTTPException(
            409, f"segment (session {session_id}, seq {seq}) already received with a different sha256"
        )

    # A segment past the end marker means that marker is stale (a pagehide beacon
    # fired mid-session and recording continued): reopen so the verdict can't read
    # 'clean' against a stale expected count while a tail is still uploading.
    if status == "received":
        led.reopen_if_past_end(session_id, seq)

    if status == "duplicate" and prior_state != "received":
        # Terminal (emitted/failed) — nothing to heal; /retry owns failed segments.
        return {"ok": True, "session_id": session_id, "seq": seq, "status": "duplicate"}

    # 'received' — fresh, or a duplicate of a segment still awaiting processing (a
    # retry after an ack whose enqueue/process never ran, e.g. crash). Enqueue is
    # idempotent downstream: process_segment no-ops on already-emitted segments and
    # per-session FIFO serializes double entries.
    fut = emitter.get_emitter(request.app).enqueue(session_id, seq)
    if settings.ingest_sync:
        try:
            await fut
        except Exception:
            # Already recorded as state='failed' in the ledger (the report shows it);
            # the ack still stands — the segment IS durably received.
            logger.warning("sync processing of (%s, %d) failed", session_id, seq, exc_info=True)
    return {"ok": True, "session_id": session_id, "seq": seq, "status": status}


def _write_spool(spool: Path, data: bytes) -> None:
    spool.parent.mkdir(parents=True, exist_ok=True)
    part = spool.with_suffix(spool.suffix + ".part")
    part.write_bytes(data)
    part.replace(spool)  # atomic within the spool dir: never a half-written spool file


# ------------------------------------------------------------------- end marker

class EndRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    last_seq: int = Field(ge=-1, le=MAX_SEQ)  # -1: ended before any segment was captured


@router.post("/sessions/{session_id}/end")
async def end_session(session_id: str, body: EndRequest) -> dict:
    led = ledger.for_settings(get_settings())
    if not led.mark_ended(session_id, body.last_seq):
        raise HTTPException(404, f"unknown session {session_id}")
    return {"ok": True}


# ---------------------------------------------------------------- sessions list

@router.get("/sessions")
async def list_sessions() -> dict:
    led = ledger.for_settings(get_settings())
    return {"sessions": led.session_summaries()}


# ------------------------------------------------------------------- gap report

def _missing_info(
    received: list[int], *, ended: bool, expected: int | None
) -> tuple[list[int], int]:
    """Client-leg gaps: (capped seq list, true total count).

    Holes below the max received seq, plus — once the session ended with a known
    expected count — the missing tail the end marker reveals. The walk is O(received)
    (gaps between sorted seqs), never O(max seq), and the returned list is capped at
    ``_MISSING_LIST_CAP`` while the count is always exact.
    """
    seen = sorted(set(received))
    top = seen[-1] if seen else -1
    count = (top + 1) - len(seen)
    tail_end = expected if (ended and expected is not None and expected > top + 1) else top + 1
    count += tail_end - (top + 1)

    capped: list[int] = []
    prev = -1
    for s in [*seen, tail_end]:
        if s - prev > 1 and len(capped) < _MISSING_LIST_CAP:
            capped.extend(range(prev + 1, min(s, prev + 1 + _MISSING_LIST_CAP - len(capped))))
        prev = s
    return capped, count


def _dp_missing_unacked(raw_missing: list, acked: set[int], limit_seq: int) -> list[int]:
    """DP-reported missing minus what OUR ledger holds a DP ack for.

    DP's tracker is in-memory: a mid-session DP restart makes it report already
    -delivered-and-acked sequences as a leading gap. We hold the ack receipts, so a
    sequence is only truly missing if DP reports it AND we never got its `/ingest`
    ack. Accepts both the real tracker's [lo, hi] runs and flat ints (older fakes);
    everything is clipped to sequences the ledger actually allocated."""
    seqs: set[int] = set()
    for item in raw_missing or []:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            lo, hi = int(item[0]), min(int(item[1]), limit_seq)
            seqs.update(range(max(0, lo), hi + 1))
        elif isinstance(item, int) and 0 <= item <= limit_seq:
            seqs.add(item)
    return sorted(seqs - acked)


async def _dp_continuity(settings: Settings, stream_id: str) -> dict:
    """Live DP-side check for one stream; {'checked': False} when DP can't answer."""
    ac = clients.async_client(settings.dp_url, _CONTINUITY_TIMEOUT)
    try:
        resp = await ac.get(f"/continuity/{stream_id}")
        if resp.status_code != 200:
            return {"checked": False}
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return {"checked": False}
    finally:
        await ac.aclose()
    return {
        "checked": True,
        "max_sequence": data.get("max_sequence"),
        "missing": data.get("missing") or [],
        "duplicate_deliveries": data.get("duplicate_deliveries", 0),
    }


@router.get("/sessions/{session_id}/report")
async def session_report(session_id: str) -> dict:
    settings = get_settings()
    led = ledger.for_settings(settings)
    session = led.get_session(session_id)
    if session is None:
        raise HTTPException(404, f"unknown session {session_id}")

    seq_states = led.segment_states(session_id)
    received = [s for s, _state in seq_states]
    ended = bool(session["ended"])
    missing, missing_count = _missing_info(
        received, ended=ended, expected=session["expected_segments"]
    )

    emit_leg: list[dict] = []
    dp_reports_missing = False
    for stream in led.streams_for_session(session_id):
        rows = led.stream_chunks(stream["stream_id"])
        emitted = [r for r in rows if r["dp_acked"]]
        dp_side = await _dp_continuity(settings, stream["stream_id"])
        if dp_side["checked"]:
            # Reconcile against OUR ack receipts: DP's in-memory tracker forgets a
            # restart-preceding prefix and would otherwise fabricate a permanent
            # leading gap for chunks that were delivered and acked. Only sequences
            # DP misses AND we hold no ack for count as loss.
            acked = {r["sequence"] for r in emitted}
            limit = max((r["sequence"] for r in rows), default=-1)
            dp_side["missing_unacked"] = _dp_missing_unacked(
                dp_side["missing"], acked, limit
            )
            if dp_side["missing_unacked"]:
                dp_reports_missing = True
        emit_leg.append(
            {
                "modality": stream["modality"],
                "stream_id": stream["stream_id"],
                "codec": stream["codec"],
                "chunks_emitted": len(emitted),
                "last_sequence": max((r["sequence"] for r in emitted), default=None),
                "pending": sum(
                    1 for r in rows if not r["dp_acked"] and r["segment_state"] == "received"
                ),
                "failed": sum(
                    1 for r in rows if not r["dp_acked"] and r["segment_state"] == "failed"
                ),
                "dp": dp_side,
            }
        )

    # The "zero silent loss" verdict, checked end-to-end across both legs: clean =
    # ended AND no client-leg hole AND every received segment emitted AND no
    # DP-checked stream missing anything. Work still in flight (not ended, or ended
    # with segments mid-emit) is "recording"; anything else is "gaps".
    any_failed = any(state == "failed" for _seq, state in seq_states)
    any_pending = any(state == "received" for _seq, state in seq_states)
    problems = missing_count > 0 or any_failed or dp_reports_missing
    if not ended or (not problems and any_pending):
        verdict = "recording"
    elif problems:
        verdict = "gaps"
    else:
        verdict = "clean"

    return {
        "session_id": session_id,
        "user_id": session["user_id"],
        "device_id": session["device_id"],
        "started_at": session["started_at"],
        "ended": ended,
        "expected_segments": session["expected_segments"],
        "received_segments": len(received),
        # Session-level drain state: per-stream `pending` can't see segments that
        # haven't been demuxed yet (their modality is unknown until then), so this
        # is THE "is processing finished" signal for pollers and the client UI.
        "segment_states": {
            "received": sum(1 for _s, state in seq_states if state == "received"),
            "emitted": sum(1 for _s, state in seq_states if state == "emitted"),
            "failed": sum(1 for _s, state in seq_states if state == "failed"),
        },
        "client_leg": {
            "missing_seqs": missing,          # capped at _MISSING_LIST_CAP entries
            "missing_count": missing_count,   # always the exact total
            "duplicate_deliveries": session["duplicate_deliveries"],
            "unterminated": not ended,
        },
        "emit_leg": emit_leg,
        "verdict": verdict,
    }


# ------------------------------------------------------------------------ retry

@router.post("/sessions/{session_id}/retry")
async def retry_failed(session_id: str, request: Request) -> dict:
    settings = get_settings()
    led = ledger.for_settings(settings)
    if led.get_session(session_id) is None:
        raise HTTPException(404, f"unknown session {session_id}")
    seqs = led.reset_failed(session_id)
    em = emitter.get_emitter(request.app)
    futures = [em.enqueue(session_id, seq) for seq in seqs]
    if settings.ingest_sync and futures:
        # Same contract as upload: wait for determinism, but outcomes (including a
        # repeat failure, already re-marked in the ledger) live in the report.
        await asyncio.gather(*futures, return_exceptions=True)
    return {"ok": True, "session_id": session_id, "retried": len(seqs)}
