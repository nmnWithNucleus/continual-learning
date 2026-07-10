"""The ChunkSource seam: the WAV source satisfies it, the registry dispatches on it, and
a brand-NEW modality source flows through the unchanged blob-first + C1-push emit path.

The last test is the load-bearing proof of the factoring: a fresh modality (here a mock
'image' source) is added by registering ONE builder, and the modality-agnostic emit path
carries it end to end — same blob-first ordering, C1 still schema-valid — with zero edits
to capturer.py and no change to the C1 wire shape.
"""
from __future__ import annotations

import math

import httpx
import pytest

from app import clients, contracts, sources
from app.config import get_settings
from app.sources import ChunkSource, SourceChunk
from app.sources.wav_source import WavFileSource, build as build_wav
from tests.conftest import DP_URL, STORAGE_URL
from tests.fakes import FakeDataProcessing, FakeStorage


# ------------------------------------------------------- WAV source implements the seam

def test_wav_source_satisfies_chunksource_protocol():
    src = build_wav(get_settings(), sample_seconds=12, chunk_seconds=5,
                    base_wallclock="2026-07-09T12:00:00Z")
    assert isinstance(src, WavFileSource)
    assert isinstance(src, ChunkSource)          # structural: modality, codec, chunks()
    assert src.modality == "audio"
    assert src.codec == "audio/wav"


def test_wav_source_yields_ordered_wallclock_stamped_chunks():
    src = build_wav(get_settings(), sample_seconds=12, chunk_seconds=5,
                    base_wallclock="2026-07-09T12:00:00Z")
    chunks = list(src.chunks())

    assert len(chunks) == math.ceil(12 / 5)      # 3 chunks
    assert all(isinstance(c, SourceChunk) for c in chunks)
    assert all(isinstance(c.data, bytes) and c.data for c in chunks)

    # Dense, contiguous wall-clock spans (RFC3339): chunk i end == chunk i+1 start.
    spans = [(c.t_start, c.t_end) for c in chunks]
    assert spans == [
        ("2026-07-09T12:00:00Z", "2026-07-09T12:00:05Z"),
        ("2026-07-09T12:00:05Z", "2026-07-09T12:00:10Z"),
        ("2026-07-09T12:00:10Z", "2026-07-09T12:00:12Z"),
    ]
    for a, b in zip(chunks, chunks[1:]):
        assert a.t_end == b.t_start


# ------------------------------------------------------------------ registry dispatch

def test_build_source_dispatches_audio():
    src = sources.build_source("audio", settings=get_settings(), sample_seconds=10,
                               chunk_seconds=5, base_wallclock="2026-07-09T12:00:00Z")
    assert isinstance(src, ChunkSource)
    assert src.modality == "audio"
    assert len(list(src.chunks())) == 2


def test_build_source_unknown_modality_raises():
    with pytest.raises(ValueError, match="no ChunkSource registered for modality 'video'"):
        sources.build_source("video", settings=get_settings())


# ---------------------------------- NEW modality drops in: emit path + C1 wire unchanged

class _MockImageSource:
    """A minimal non-audio ChunkSource — proves the seam is modality-agnostic.

    Satisfies ChunkSource structurally (no base class imported): modality/codec + an
    ordered chunks() of opaque bytes with instantaneous wall-clock spans (t_start==t_end,
    as a still image has no duration).
    """

    modality = "image"
    codec = "image/png"

    def __init__(self, base_wallclock: str | None = None, **_ignored) -> None:
        self._t = base_wallclock or "2026-07-09T12:00:00Z"

    def chunks(self):
        png = b"\x89PNG\r\n\x1a\n"                # PNG magic; opaque to the emit path
        yield SourceChunk(png + b"frame-0", self._t, self._t)
        yield SourceChunk(png + b"frame-1", self._t, self._t)


def _make_image_wiring(monkeypatch):
    """Wiring identical to conftest's, plus a registered mock 'image' source."""
    monkeypatch.setenv("RECORDING_RETRY_BACKOFF", "0")
    monkeypatch.setitem(
        sources.SOURCE_BUILDERS, "image",
        lambda settings, **overrides: _MockImageSource(**overrides),
    )
    events: list = []
    storage, dp = FakeStorage(events), FakeDataProcessing(events)

    def fake_async_client(base_url, timeout):
        handler = storage if "storage" in base_url else dp
        return httpx.AsyncClient(base_url=base_url, timeout=timeout,
                                 transport=httpx.MockTransport(handler))

    monkeypatch.setattr(clients, "async_client", fake_async_client)

    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app), storage, dp, events


def test_mock_source_satisfies_protocol():
    assert isinstance(_MockImageSource(), ChunkSource)


def test_new_modality_flows_through_emit_path_unchanged(monkeypatch):
    client, storage, dp, events = _make_image_wiring(monkeypatch)

    resp = client.post("/capture/run", json={
        "storage_url": STORAGE_URL, "dp_url": DP_URL, "modality": "image",
        "base_wallclock": "2026-07-09T12:00:00Z",
    })
    assert resp.status_code == 200, resp.text
    out = resp.json()

    # The image source's 2 chunks emitted with dense zero-based sequence, one stream_id.
    assert out["chunks_emitted"] == 2
    assert out["sequences"] == [0, 1]
    assert len({e["stream_id"] for e in dp.envelopes}) == 1

    # C1 carries the source's modality/codec — and STILL validates against the frozen
    # C1 schema (modality enum includes 'image'); the wire shape did not change.
    for seq, env in enumerate(dp.envelopes):
        assert env["modality"] == "image"
        assert env["codec"] == "image/png"
        assert env["sequence"] == seq
        assert env["t_start"] == env["t_end"] == "2026-07-09T12:00:00Z"
        assert contracts.c1_errors(env) == [], env

    # Same blob-first invariant the emit path guarantees for every modality.
    expected: list = []
    for cid in storage.blobs:
        expected += [("PUT", cid), ("POST", cid)]
    assert events == expected
