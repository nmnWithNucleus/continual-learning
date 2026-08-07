"""Per-modality C1 fixtures + a POST helper the verifier can drive.

Each modality has TWO committed files in this directory:
  * ``<modality>.c1.json`` — a standalone, schema-valid C1 envelope (blob_sha256 /
    blob_bytes already baked to match the blob), so it can be inspected or POSTed
    as-is against a live ``/ingest``;
  * ``<modality>.blob``     — the trivial raw bytes the envelope references. The
    stub processors don't decode the bytes, so these are tiny.

Regenerate with ``scratchpad/gen_fixtures.py`` if the shapes ever change.

Helper surface (for tests AND the integration verifier):
  * ``MODALITIES`` / ``EXPECTED_RECORDS`` — the modality list + how many C2 records
    each fixture must yield (video is the 1-chunk-many-records case: 3).
  * ``load_c1(modality)`` / ``load_blob(modality)`` — read a fixture.
  * ``register_and_post(client, modality)`` — register the blob in the client's fake
    storage and POST the envelope to ``/ingest``; returns the httpx ``Response``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_DIR = Path(__file__).resolve().parent

# The modalities with a v0 plugin, and the record count each fixture must emit.
MODALITIES: tuple[str, ...] = ("audio", "image", "video", "text")
EXPECTED_RECORDS: dict[str, int] = {"audio": 1, "image": 1, "video": 3, "text": 1}
# The C2 content.kind each modality emits (frozen enum values).
EXPECTED_KIND: dict[str, str] = {
    "audio": "transcript",
    "image": "caption",
    "video": "caption",
    "text": "text",
}


def load_c1(modality: str) -> dict[str, Any]:
    """The standalone, schema-valid C1 envelope for ``modality``."""
    return json.loads((_DIR / f"{modality}.c1.json").read_text())


def load_blob(modality: str) -> bytes:
    """The raw blob bytes the ``modality`` envelope references."""
    return (_DIR / f"{modality}.blob").read_bytes()


def register_and_post(client, modality: str):
    """Register the fixture blob in the client's fake storage, then POST its C1 to
    ``/ingest``. Returns the httpx Response. ``client`` is a TestClient carrying a
    ``fake_storage`` (see ``tests.conftest.client``)."""
    c1 = load_c1(modality)
    client.fake_storage.add_blob(c1["blob_ref"], load_blob(modality))
    return client.post("/ingest", json=c1)
