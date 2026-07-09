"""Unit tests for the carver + a live-path smoke of the module CLI against the fakes."""
from __future__ import annotations

import io
import math
import wave

import httpx
import pytest

from app import clients, wav
from tests.conftest import DP_URL, STORAGE_URL
from tests.fakes import FakeDataProcessing, FakeStorage


@pytest.mark.parametrize(
    "n_seconds,chunk_seconds,expected",
    [(10, 5, 2), (12, 5, 3), (7, 3, 3), (5, 5, 1), (1, 5, 1), (0, 5, 0)],
)
def test_carve_count_is_ceil(n_seconds, chunk_seconds, expected):
    audio = wav.read_wav(wav.generate_sample_wav(seconds=n_seconds, sample_rate=16000))
    chunks = wav.carve(audio, chunk_seconds)
    assert len(chunks) == expected
    if expected:
        assert math.ceil(n_seconds / chunk_seconds) == expected


def test_carve_indices_dense_and_last_chunk_shorter():
    audio = wav.read_wav(wav.generate_sample_wav(seconds=12, sample_rate=16000))
    chunks = wav.carve(audio, 5)
    assert [c.index for c in chunks] == [0, 1, 2]                    # dense, zero-based
    # Contiguous spans, final one shorter.
    assert [(c.t_start_seconds, c.t_end_seconds) for c in chunks] == [
        (0.0, 5.0), (5.0, 10.0), (10.0, 12.0),
    ]


def test_each_chunk_is_a_standalone_wav():
    audio = wav.read_wav(wav.generate_sample_wav(seconds=7, sample_rate=16000))
    chunks = wav.carve(audio, 5)
    total_frames = 0
    for c in chunks:
        with wave.open(io.BytesIO(c.data), "rb") as w:    # each chunk parses as a WAV
            assert w.getframerate() == 16000
            assert w.getnchannels() == 1
            assert w.getsampwidth() == 2
            total_frames += w.getnframes()
    assert total_frames == audio.n_frames                  # lossless carve (all frames kept)


def test_capture_from_a_source_wav_file(tmp_path, monkeypatch):
    """source= a real .wav path is carved exactly like the synthetic sample."""
    src = tmp_path / "sample.wav"
    src.write_bytes(wav.generate_sample_wav(seconds=8, sample_rate=16000))

    monkeypatch.setenv("RECORDING_RETRY_BACKOFF", "0")
    events: list = []
    storage, dp = FakeStorage(events), FakeDataProcessing(events)

    def fake_async_client(base_url, timeout):
        handler = storage if "storage" in base_url else dp
        return httpx.AsyncClient(base_url=base_url, timeout=timeout,
                                 transport=httpx.MockTransport(handler))

    monkeypatch.setattr(clients, "async_client", fake_async_client)

    from app.cli import main

    rc = main([
        "--storage-url", STORAGE_URL,
        "--dp-url", DP_URL,
        "--source", str(src),
        "--chunk-seconds", "5",
        "--base-wallclock", "2026-07-09T12:00:00Z",
    ])
    assert rc == 0
    assert len(dp.envelopes) == math.ceil(8 / 5)   # 2 chunks
    assert [e["sequence"] for e in dp.envelopes] == [0, 1]
    assert {e["stream_id"] for e in dp.envelopes} == {dp.envelopes[0]["stream_id"]}
