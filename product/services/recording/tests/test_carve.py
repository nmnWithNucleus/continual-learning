"""VAD-cut variable chunking: find_cuts/carve_at units + the path through the source.

Synthetic PCM only (no real speech): a 220 Hz tone models speech energy, zeros model
pauses — the same idiom as ``wav.generate_sample_wav``. Silence spans are kept above
~20% of each stream so the p20 noise-floor calibration lands among the silence windows
(threshold -> the absolute floor), the regime the detector targets; the degenerate
tests cover the other regime, where nothing clears the adaptive threshold and the
carve degrades to hard max_s cuts.
"""
from __future__ import annotations

import io
import math
import wave
from array import array

import pytest

from app import wav
from app.carve import carve_at, find_cuts
from app.config import get_settings
from app.sources.wav_source import build as build_wav

SR = 16000
HOP = int(round(SR * 30 / 1000.0))     # frames per default 30 ms hop


@pytest.fixture(autouse=True)
def _clean_chunking_env(monkeypatch):
    """Pin the default carve-mode resolution regardless of the operator's shell env."""
    for var in ("CHUNK_SECONDS", "VAD_MIN_CHUNK_SECONDS", "VAD_MAX_CHUNK_SECONDS"):
        monkeypatch.delenv(var, raising=False)


# ------------------------------------------------------------- synthetic PCM helpers

def tone(seconds: float, *, sample_rate: int = SR, freq_hz: float = 220.0,
         amplitude: float = 0.3) -> bytes:
    """s16le mono sine — 'speech' energy (RMS ~7000, far above the 200 floor)."""
    two_pi_f = 2.0 * math.pi * freq_hz
    n = int(round(seconds * sample_rate))
    return array(
        "h",
        (int(amplitude * 32767 * math.sin(two_pi_f * (i / sample_rate))) for i in range(n)),
    ).tobytes()


def silence(seconds: float, *, sample_rate: int = SR) -> bytes:
    return bytes(2 * int(round(seconds * sample_rate)))


def write_wav(path, pcm: bytes, *, sample_rate: int = SR) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)


def assert_cut_invariants(cuts: list[int], total: int, min_frames: int, max_frames: int):
    """The find_cuts contract: strictly increasing, in (0, total), bounds respected
    (no chunk < min or > max, except the final remainder which may be < min)."""
    assert cuts == sorted(set(cuts))
    assert all(0 < c < total for c in cuts)
    bounds = [0, *cuts, total]
    lengths = [b - a for a, b in zip(bounds, bounds[1:])]
    for length in lengths[:-1]:
        assert min_frames <= length <= max_frames
    assert lengths[-1] <= max_frames


# ------------------------------------------------------------------ find_cuts units

def test_cut_lands_inside_the_pause():
    pcm = tone(6) + silence(4) + tone(6)
    cuts = find_cuts(pcm, SR)
    assert len(cuts) == 1
    assert 6 * SR < cuts[0] < 10 * SR                 # inside the silence span
    assert abs(cuts[0] - 8 * SR) <= 2 * HOP           # ~ the pause midpoint
    assert_cut_invariants(cuts, len(pcm) // 2, 5 * SR, 30 * SR)


def test_first_pause_past_min_wins():
    # Pauses at ~[2, 3.5]s (midpoint < min_s=5 -> skipped) and ~[6.5, 8]s (taken).
    pcm = tone(2) + silence(1.5) + tone(3) + silence(1.5) + tone(3)
    cuts = find_cuts(pcm, SR)
    assert len(cuts) == 1                              # remainder (~3.75s) < min_s: final
    assert int(6.5 * SR) < cuts[0] < 8 * SR
    assert abs(cuts[0] - int(7.25 * SR)) <= 2 * HOP


def test_hard_cut_at_exactly_max_then_pause_taken():
    # One pause at ~[4, 7]s, midpoint ~5.5s; min_s=1, max_s=4.
    pcm = tone(4) + silence(3) + tone(4)
    cuts = find_cuts(pcm, SR, min_s=1.0, max_s=4.0)
    assert len(cuts) == 3
    assert cuts[0] == 4 * SR                           # no pause before max_s: exact hard cut
    assert 4 * SR < cuts[1] < 7 * SR                   # then the pause midpoint is taken
    assert abs(cuts[1] - int(5.5 * SR)) <= 2 * HOP
    assert cuts[2] == cuts[1] + 4 * SR                 # no pause offers again: exact hard cut
    assert_cut_invariants(cuts, len(pcm) // 2, 1 * SR, 4 * SR)


def test_bounds_respected_across_many_pauses():
    pcm = (tone(3) + silence(1)) * 8                   # 32s, a pause every 4th second
    total = len(pcm) // 2
    cuts = find_cuts(pcm, SR)
    assert len(cuts) >= 3
    assert_cut_invariants(cuts, total, 5 * SR, 30 * SR)
    for c in cuts:
        assert 3 * SR < c % (4 * SR) < 4 * SR          # every cut inside a silence span


@pytest.mark.parametrize("make", [silence, tone], ids=["all-silence", "all-tone"])
def test_degenerate_input_degrades_to_max_s_cuts(make):
    # Nothing clears the adaptive threshold -> no speech pauses to align to ->
    # hard cuts at exactly max_s; the 10s remainder is the final chunk.
    sr = 8000
    cuts = find_cuts(make(70, sample_rate=sr), sr)
    assert cuts == [30 * sr, 60 * sr]


def test_short_and_empty_inputs_yield_no_cuts():
    assert find_cuts(b"", SR) == []
    assert find_cuts(tone(3), SR) == []                          # shorter than min_s
    assert find_cuts(tone(2) + silence(1) + tone(2), SR) == []   # pause before min_s only


def test_find_cuts_rejects_bad_args():
    with pytest.raises(ValueError, match="even byte count"):
        find_cuts(b"\x00", SR)
    with pytest.raises(ValueError, match="min_s"):
        find_cuts(tone(1), SR, min_s=10.0, max_s=5.0)
    with pytest.raises(ValueError, match="sample_rate"):
        find_cuts(tone(1), 0)


# ------------------------------------------------------------------- carve_at units

def test_carve_at_slices_standalone_adjacent_wavs():
    pcm = tone(6) + silence(4) + tone(6)
    audio = wav.WavAudio(sample_rate=SR, channels=1, sampwidth=2, frames=pcm)
    chunks = carve_at(audio, find_cuts(pcm, SR))
    assert len(chunks) == 2
    assert [c.index for c in chunks] == [0, 1]                   # dense, zero-based
    total_frames = 0
    for c in chunks:
        with wave.open(io.BytesIO(c.data), "rb") as w:           # each parses standalone
            assert w.getframerate() == SR
            assert w.getnchannels() == 1
            assert w.getsampwidth() == 2
            assert w.getnframes() == c.n_frames
            total_frames += w.getnframes()
    assert total_frames == audio.n_frames                        # lossless carve
    for a, b in zip(chunks, chunks[1:]):
        assert a.t_end_seconds == b.t_start_seconds              # frame-exact adjacency
    assert chunks[0].t_start_seconds == 0.0
    assert chunks[-1].t_end_seconds == audio.duration_seconds


def test_carve_at_no_cuts_and_empty_audio():
    audio = wav.WavAudio(sample_rate=SR, channels=1, sampwidth=2, frames=tone(2))
    [only] = carve_at(audio, [])                                 # whole stream, one chunk
    assert (only.t_start_seconds, only.t_end_seconds) == (0.0, 2.0)
    assert only.n_frames == audio.n_frames
    empty = wav.WavAudio(sample_rate=SR, channels=1, sampwidth=2, frames=b"")
    assert carve_at(empty, []) == []


def test_carve_at_rejects_bad_offsets():
    audio = wav.WavAudio(sample_rate=SR, channels=1, sampwidth=2, frames=tone(2))
    for bad in ([0], [audio.n_frames], [200, 100], [100, 100], [-5]):
        with pytest.raises(ValueError, match="strictly increasing"):
            carve_at(audio, bad)


# ------------------------------------- the VAD path through the audio ChunkSource

def test_source_default_is_vad_carve_with_exact_adjacency(tmp_path):
    path = tmp_path / "speechish.wav"
    write_wav(path, tone(6) + silence(4) + tone(6))
    src = build_wav(get_settings(), source=str(path),
                    base_wallclock="2026-07-09T12:00:00Z")
    chunks = list(src.chunks())

    assert len(chunks) == 2                            # one pause-aligned cut (~8s)
    assert chunks[0].t_start == "2026-07-09T12:00:00Z"
    assert chunks[-1].t_end == "2026-07-09T12:00:16Z"
    for a, b in zip(chunks, chunks[1:]):
        assert a.t_end == b.t_start                    # string-identical adjacency
    for c in chunks:
        with wave.open(io.BytesIO(c.data), "rb") as w:
            assert w.getnchannels() == 1
            assert w.getframerate() == SR


def test_vad_bounds_come_from_env(monkeypatch):
    monkeypatch.setenv("VAD_MIN_CHUNK_SECONDS", "1")
    monkeypatch.setenv("VAD_MAX_CHUNK_SECONDS", "3")
    # The synthetic sample is a pure tone -> degenerate -> hard cuts at max_s=3.
    src = build_wav(get_settings(), sample_seconds=7,
                    base_wallclock="2026-07-09T12:00:00Z")
    assert [(c.t_start, c.t_end) for c in src.chunks()] == [
        ("2026-07-09T12:00:00Z", "2026-07-09T12:00:03Z"),
        ("2026-07-09T12:00:03Z", "2026-07-09T12:00:06Z"),
        ("2026-07-09T12:00:06Z", "2026-07-09T12:00:07Z"),
    ]


def test_vad_carve_requires_s16_mono(tmp_path):
    path = tmp_path / "stereo.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(bytes(4 * SR))
    src = build_wav(get_settings(), source=str(path))
    with pytest.raises(ValueError, match="s16le mono"):
        list(src.chunks())


# ------------------------------------------------- fixed-mode regression (M0 intact)

def test_explicit_chunk_seconds_is_fixed_and_byte_identical_to_m0():
    settings = get_settings()
    src = build_wav(settings, sample_seconds=12, chunk_seconds=5,
                    base_wallclock="2026-07-09T12:00:00Z")
    got = list(src.chunks())

    audio = wav.read_wav(
        wav.generate_sample_wav(seconds=12, sample_rate=settings.sample_rate)
    )
    expected = wav.carve(audio, 5)
    assert [c.data for c in got] == [c.data for c in expected]   # byte-identical chunks
    assert [(c.t_start, c.t_end) for c in got] == [
        ("2026-07-09T12:00:00Z", "2026-07-09T12:00:05Z"),
        ("2026-07-09T12:00:05Z", "2026-07-09T12:00:10Z"),
        ("2026-07-09T12:00:10Z", "2026-07-09T12:00:12Z"),
    ]


def test_env_chunk_seconds_forces_fixed_mode(monkeypatch):
    monkeypatch.setenv("CHUNK_SECONDS", "5")
    src = build_wav(get_settings(), sample_seconds=12,
                    base_wallclock="2026-07-09T12:00:00Z")
    assert [(c.t_start, c.t_end) for c in src.chunks()] == [
        ("2026-07-09T12:00:00Z", "2026-07-09T12:00:05Z"),
        ("2026-07-09T12:00:05Z", "2026-07-09T12:00:10Z"),
        ("2026-07-09T12:00:10Z", "2026-07-09T12:00:12Z"),
    ]
