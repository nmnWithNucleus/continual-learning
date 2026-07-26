"""D17 — the capture wire carries the device's own timezone.

Recording is the ONLY service that can know where the user physically was at a
moment: our clients already compute the local instant and then throw the zone away
converting to UTC. These tests pin the server half — accept it, validate it at the
edge, persist it, and hand it to C1 (including on the re-drive path).
"""
from __future__ import annotations

import hashlib
import sqlite3

from app.capturer import _build_envelope
from app.ledger import Ledger
from tests.test_capture_web import ingest  # noqa: F401 — the wiring fixture

_BODY = b"\x00" * 64
_ARGS = {
    "session_id": "s-tz", "user_id": "beta-user", "device_id": "phone",
    "t_start": "2026-07-20T12:00:00Z", "t_end": "2026-07-20T12:00:10Z",
    "mime": "audio/mp4", "sha256": hashlib.sha256(_BODY).hexdigest(),
}


def _post(ingest, **extra):  # noqa: F811
    params = {**_ARGS, "seq": extra.pop("seq", 0), **extra}
    return ingest.client.post("/capture/segments", params=params, content=_BODY,
                              headers={"content-type": "application/octet-stream"})


def test_wire_accepts_and_persists_the_device_zone(ingest):  # noqa: F811
    res = _post(ingest, device_tz="America/Los_Angeles",
                device_utc_offset_minutes=-420)
    assert res.status_code == 200, res.text

    row = Ledger(ingest.var / "ledger.db").segment("s-tz", 0)
    assert row["device_tz"] == "America/Los_Angeles"
    assert row["device_utc_offset_minutes"] == -420


def test_wire_still_works_without_a_zone(ingest):  # noqa: F811
    """Every field is optional — a client that can't determine its zone must
    still capture, and stores NULL rather than a fabricated default."""
    assert _post(ingest).status_code == 200
    row = Ledger(ingest.var / "ledger.db").segment("s-tz", 0)
    assert row["device_tz"] is None


def test_abbreviations_are_rejected_at_the_edge(ingest):  # noqa: F811
    """'PST' is ambiguous (US Pacific vs Philippines) and DST-sensitive. Rejecting
    it HERE, at the client boundary, beats discovering it in the renderer a night
    later and three services away."""
    res = _post(ingest, device_tz="PST")
    assert res.status_code == 400
    assert "IANA" in res.text


def test_unknown_iana_zone_is_rejected(ingest):  # noqa: F811
    res = _post(ingest, device_tz="Mars/Olympus_Mons")
    assert res.status_code == 400
    assert "unknown device_tz" in res.text


def test_out_of_range_offset_is_rejected(ingest):  # noqa: F811
    assert _post(ingest, device_utc_offset_minutes=9999).status_code == 422


def test_envelope_omits_absent_fields_rather_than_nulling_them():
    """C1 declares both optional. An ABSENT key is what makes consumers fall back
    to the user's profile home_tz; an explicit null would fail the schema gate."""
    plain = _build_envelope(
        user_id="u", device_id="d", stream_id="s", sequence=0, chunk_id="c",
        modality="audio", codec="audio/wav", t_start=_ARGS["t_start"],
        t_end=_ARGS["t_end"], blob_ref="r", sha256="x" * 64, nbytes=1,
    )
    assert "device_tz" not in plain
    assert "device_utc_offset_minutes" not in plain

    stamped = _build_envelope(
        user_id="u", device_id="d", stream_id="s", sequence=0, chunk_id="c",
        modality="audio", codec="audio/wav", t_start=_ARGS["t_start"],
        t_end=_ARGS["t_end"], blob_ref="r", sha256="x" * 64, nbytes=1,
        device_tz="Asia/Tokyo", device_utc_offset_minutes=540,
    )
    # _build_envelope validates against the frozen C1 schema + pydantic mirror,
    # so reaching here at all proves the additive fields are contract-legal.
    assert stamped["device_tz"] == "Asia/Tokyo"
    assert stamped["device_utc_offset_minutes"] == 540


# --- pre-D17 ledgers ----------------------------------------------------------

_OLD_SEGMENTS = """
CREATE TABLE segments (
  session_id TEXT NOT NULL, seq INTEGER NOT NULL, sha256 TEXT NOT NULL,
  bytes INTEGER NOT NULL, mime TEXT NOT NULL, t_start TEXT NOT NULL,
  t_end TEXT NOT NULL, received_at TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'received', spool_path TEXT NOT NULL, error TEXT,
  PRIMARY KEY (session_id, seq)
);
"""


def test_migration_adds_columns_without_inventing_a_zone(tmp_path):
    """A segment received before clients reported a zone genuinely HAS no zone.
    Backfilling one (the server's, say) would be exactly the silently-wrong-
    timezone failure this slice exists to remove, so NULL is the honest value."""
    db = tmp_path / "ledger.db"
    conn = sqlite3.connect(db)
    conn.executescript(_OLD_SEGMENTS)
    conn.execute(
        "INSERT INTO segments (session_id, seq, sha256, bytes, mime, t_start,"
        " t_end, received_at, spool_path) VALUES"
        " ('s-old', 0, 'abc', 1, 'audio/mp4', 't0', 't1', 'r', '/tmp/x')"
    )
    conn.commit()
    conn.close()

    row = Ledger(db).segment("s-old", 0)
    assert "device_tz" in row and row["device_tz"] is None
    assert row["device_utc_offset_minutes"] is None
