"""The replay ChunkSource: a recorded day re-emitted through the UNCHANGED emit path.

The property the whole Phase-3 dogfood rests on is the last test here: a replayed chunk
produces a C1 envelope indistinguishable from a live capture's — same modality, same
codec, same schema — even though it was selected by a different registry key. If that
stopped being true, "the real pipeline" would be a claim rather than a fact.
"""
from __future__ import annotations

import json

import httpx
import pytest

from app import capturer, clients, contracts, sources
from app.config import get_settings
from app.sources import ChunkSource, SourceChunk
from app.sources.replay_source import ReplayPlanSource, build as build_replay, load_plan
from tests.conftest import DP_URL, STORAGE_URL
from tests.fakes import FakeDataProcessing, FakeStorage

# A standalone 44-byte WAV header plus a little PCM: the source treats the bytes as
# opaque, but a header-only file is exactly what a truncated cache write looks like, so
# the payload has to be past that guard for the cache-hit tests to mean anything.
WAV = (b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
       b"\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
       + b"\x00\x01" * 64)


def write_plan(tmp_path, rows) -> str:
    for row in rows:
        if row.get("_bytes") is not None:
            path = tmp_path / f"{row['chunk_key']}.wav"
            path.write_bytes(row.pop("_bytes"))
            row["audio"] = str(path)
    path = tmp_path / "day5.replay.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return str(path)


def two_chunk_plan(tmp_path) -> str:
    return write_plan(tmp_path, [
        {"chunk_key": "vid_c000", "t_start": "2025-09-02T08:00:00Z",
         "t_end": "2025-09-02T08:19:59.999000Z", "audio": "", "_bytes": WAV},
        {"chunk_key": "vid_c001", "t_start": "2025-09-02T08:20:00Z",
         "t_end": "2025-09-02T08:40:05.732000Z", "audio": "", "_bytes": WAV + b"\x01"},
    ])


# ------------------------------------------------------------------ the seam + registry

def test_replay_source_satisfies_the_chunksource_seam(tmp_path):
    src = build_replay(get_settings(), source=two_chunk_plan(tmp_path))
    assert isinstance(src, ReplayPlanSource)
    assert isinstance(src, ChunkSource)
    # Declares AUDIO, not a new C1 modality: the registry key is the only thing that
    # differs from a live capture, and it never reaches the wire.
    assert src.modality == "audio"
    assert src.codec == "audio/wav"


def test_registry_dispatches_the_variant_key_without_disturbing_audio():
    assert sources.SOURCE_BUILDERS["audio"].__module__.endswith("wav_source")
    assert sources.SOURCE_BUILDERS["replay"].__module__.endswith("replay_source")


def test_build_source_routes_replay(tmp_path):
    src = sources.build_source("replay", settings=get_settings(),
                               source=two_chunk_plan(tmp_path))
    assert isinstance(src, ReplayPlanSource)


# ------------------------------------------------------------------ spans come from the plan

def test_chunks_carry_the_plan_spans_and_bytes_verbatim(tmp_path):
    src = build_replay(get_settings(), source=two_chunk_plan(tmp_path))
    chunks = list(src.chunks())

    assert [(c.t_start, c.t_end) for c in chunks] == [
        ("2025-09-02T08:00:00Z", "2025-09-02T08:19:59.999000Z"),
        ("2025-09-02T08:20:00Z", "2025-09-02T08:40:05.732000Z"),
    ]
    assert all(isinstance(c, SourceChunk) for c in chunks)
    assert [c.data for c in chunks] == [WAV, WAV + b"\x01"]


def test_carve_and_wallclock_overrides_are_ignored(tmp_path, caplog):
    """The plan owns the spine. Silently re-carving or re-stamping would break the
    wall-clock join data-processing makes against the same plan."""
    src = build_replay(get_settings(), source=two_chunk_plan(tmp_path),
                       chunk_seconds=5.0, base_wallclock="2020-01-01T00:00:00Z")
    spans = [(c.t_start, c.t_end) for c in src.chunks()]
    assert spans[0] == ("2025-09-02T08:00:00Z", "2025-09-02T08:19:59.999000Z")
    assert len(spans) == 2


def test_plan_order_is_capture_order(tmp_path):
    rows = [{"chunk_key": f"vid_c{i:03d}", "t_start": f"2025-09-02T08:{i:02d}:00Z",
             "t_end": f"2025-09-02T08:{i:02d}:59Z", "audio": "", "_bytes": WAV + bytes([i])}
            for i in range(5)]
    src = build_replay(get_settings(), source=write_plan(tmp_path, rows))
    assert [c.data[-1] for c in src.chunks()] == [0, 1, 2, 3, 4]


# ------------------------------------------------------------------ failure modes

def test_missing_source_is_a_loud_error():
    with pytest.raises(ValueError, match="requires --source"):
        build_replay(get_settings())


def test_empty_plan_is_a_loud_error(tmp_path):
    path = tmp_path / "empty.jsonl"
    path.write_text("")
    with pytest.raises(ValueError, match="empty"):
        load_plan(path)


def test_plan_missing_required_columns_is_a_loud_error(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text(json.dumps({"chunk_key": "x"}) + "\n")
    with pytest.raises(ValueError, match="missing"):
        load_plan(path)


def test_absent_audio_with_no_media_raises_rather_than_yielding_silence(tmp_path):
    """A missing cache entry must never become an empty/synthetic chunk: the blob is
    what ASR runs on AND what satisfies data-processing's sha check."""
    path = write_plan(tmp_path, [{"chunk_key": "vid_c000", "t_start": "2025-09-02T08:00:00Z",
                                  "t_end": "2025-09-02T08:20:00Z",
                                  "audio": str(tmp_path / "nope.wav")}])
    src = build_replay(get_settings(), source=path)
    with pytest.raises(FileNotFoundError, match="no cached audio"):
        list(src.chunks())


def test_extraction_fails_when_the_reader_dies(tmp_path):
    """ffmpeg encodes a truncated stream and exits 0, so a reader that died mid-transfer
    would otherwise cache a SHORT wav — a silently shorter day, not a loud failure."""
    audio = tmp_path / "cold.wav"
    plan = write_plan(tmp_path, [{"chunk_key": "vid_c000", "t_start": "2025-09-02T08:00:00Z",
                                  "t_end": "2025-09-02T08:20:00Z", "audio": str(audio),
                                  "media": str(tmp_path / "absent.mkv")}])
    src = build_replay(get_settings(), source=plan)
    with pytest.raises(RuntimeError):
        list(src.chunks())
    assert not audio.exists()


def test_extraction_fills_the_cache_from_media(tmp_path, monkeypatch):
    """A cold cache extracts from the source media and caches the result atomically."""
    audio = tmp_path / "cold.wav"
    plan = write_plan(tmp_path, [{"chunk_key": "vid_c000", "t_start": "2025-09-02T08:00:00Z",
                                  "t_end": "2025-09-02T08:20:00Z", "audio": str(audio),
                                  "media": str(tmp_path / "src.mkv")}])
    (tmp_path / "src.mkv").write_bytes(b"pretend-media")
    monkeypatch.setattr(ReplayPlanSource, "_extract", lambda self, media: WAV)

    chunks = list(build_replay(get_settings(), source=plan).chunks())
    assert chunks[0].data == WAV
    assert audio.read_bytes() == WAV                      # cached for the next run
    assert not list(tmp_path.glob(".*part"))              # no partial left behind


# ------------------------------------------------------------------ the wire is unchanged

@pytest.mark.anyio
async def test_replayed_chunks_emit_schema_valid_audio_c1(tmp_path, monkeypatch):
    """End to end through the REAL emit path: a replayed capture PUTs blobs and pushes
    C1 envelopes that validate against the frozen schema as modality 'audio'."""

    events: list = []
    storage, dp = FakeStorage(events), FakeDataProcessing(events)
    def fake_client(base_url: str, timeout: float) -> httpx.AsyncClient:
        handler = storage if base_url == STORAGE_URL else dp
        return httpx.AsyncClient(base_url=base_url, timeout=timeout,
                                 transport=httpx.MockTransport(handler))

    monkeypatch.setattr(clients, "async_client", fake_client)
    monkeypatch.setenv("RECORDING_RETRY_BACKOFF", "0")

    result = await capturer.run_capture(
        settings=get_settings(), storage_url=STORAGE_URL, dp_url=DP_URL,
        modality="replay", source=two_chunk_plan(tmp_path),
        user_id="replay-speed", device_id="replay-tour-audio")

    assert result["chunks_emitted"] == 2
    assert result["sequences"] == [0, 1]
    envelopes = [e for e in dp.envelopes]
    assert len(envelopes) == 2
    for env in envelopes:
        contracts.validate_c1(env)                     # the frozen schema, not a mirror
        assert env["modality"] == "audio" and env["codec"] == "audio/wav"
        assert env["user_id"] == "replay-speed"
    assert envelopes[0]["t_start"] == "2025-09-02T08:00:00Z"
    assert envelopes[0]["stream_id"] == envelopes[1]["stream_id"]   # one day, one stream
