"""dp-server: pyannote speaker diarization (Stage B, WP-B3).

One ModelBackend around pyannote/speaker-diarization-3.1, served by the
dp_servers_common framework (warmup thread, /health identity, /infer). The
diarization behavior is a faithful copy of the v0 in-process seam
(app/audio/diarize/pyannote.py, node-7 smoke-validated 2026-07-19) moved behind
the model-server wire contract.

L4: everything output-affecting is pinned IN CODE below — model id, pipeline
revision, device. The only env this module reads is operational: HF_TOKEN /
HUGGINGFACE_TOKEN (auth for the gated repos; the token's VALUE is never printed,
logged, or written anywhere).

Empirical gotchas preserved from v0 (do not "clean up"):
  * torch >= 2.6 flips ``torch.load(weights_only=)`` to True and then rejects
    pyannote's Lightning checkpoints (non-tensor globals) — the scoped
    ``_trusted_checkpoint_load()`` context forces ``weights_only=False`` for the
    duration of the pipeline load only.
  * ``Pipeline.from_pretrained`` returns ``None`` (does not raise) on a missing/
    invalid token or un-accepted gated conditions — check and raise loudly.
    BOTH pyannote/speaker-diarization-3.1 AND pyannote/segmentation-3.0 must be
    accepted by the token's account.
  * GPU placement is ``pipeline.to(torch.device(...))`` AFTER load — never a
    ``device=`` kwarg on ``from_pretrained``.
  * torchaudio's soundfile backend cannot demux webm/opus — pre-decode every
    chunk to 16 kHz mono WAV via ffmpeg, giving the temp file the codec-derived
    extension so ffmpeg demuxes the right container.
  * ``itertracks(yield_label=True)`` yields ``(Segment, track_id, label)`` — the
    THIRD value is the speaker. Turns may temporally OVERLAP (overlapped
    speech); we keep them. Turns are clamped to ``[0, span_seconds]`` and raw
    ``SPEAKER_xx`` labels are renormalized to ``spk_0..`` by first onset (raw
    label as the deterministic tie-break).
"""
from __future__ import annotations

import base64
import binascii
import contextlib
import math
import os
import subprocess
import tempfile
from pathlib import Path

from dp_servers_common.backend import BackendError, ModelBackend
from dp_servers_common.runner import serve
from dp_servers_common.wire import InferRequest

# ---- pinned identity (L4: in code, never env) ----------------------------------
MODEL_ID = "pyannote/speaker-diarization-3.1"
REVISION = "84fd25912480287da0247647c3d2b4853cb3ee5d"
DEVICE = "cuda"  # no CPU fallback — device is identity; load() fails loud without CUDA

# Sub-models the pipeline config pulls in at load time; their resolved snapshot
# revisions are read back from the local HF cache after load and carried in identity.
_SEGMENTATION_ID = "pyannote/segmentation-3.0"
_EMBEDDING_ID = "pyannote/wespeaker-voxceleb-resnet34-LM"

_ALLOWED_PARAMS = {"span_seconds", "min_speakers", "max_speakers"}


@contextlib.contextmanager
def _trusted_checkpoint_load():
    """Force ``weights_only=False`` for the duration of the pyannote model load.

    torch 2.6 flipped ``torch.load``'s ``weights_only`` default to ``True``, and
    pyannote's Lightning checkpoints carry non-tensor globals (e.g.
    ``torch.torch_version.TorchVersion``) that the safe-unpickler rejects — so
    ``Pipeline.from_pretrained`` raises ``UnpicklingError: Weights only load
    failed`` on any box with torch >= 2.6 (found empirically on node-7
    2026-07-19: torch 2.8, pyannote 3.3.2). The gated model is pulled with OUR
    HF token from a trusted repo, so ``weights_only=False`` is safe HERE; the
    override is scoped to the load and the original ``torch.load`` is restored
    immediately after — never a global monkeypatch. pyannote loads several
    sub-checkpoints (segmentation + embedding), all covered by the scope."""
    import torch  # lazy

    original = torch.load

    def _patched(*args, **kwargs):
        kwargs["weights_only"] = False
        return original(*args, **kwargs)

    torch.load = _patched
    try:
        yield
    finally:
        torch.load = original


def _hf_cache_snapshot_revision(repo_id: str) -> str:
    """Resolve the snapshot revision of ``repo_id`` from the local cache.

    The pipeline config references its sub-models without a revision, so
    ``Pipeline.from_pretrained`` resolves them at ``main`` — the cache's
    ``refs/main`` file records exactly the commit that resolution produced.
    pyannote 3.3.2 downloads into ITS OWN hub-layout cache
    (``pyannote.audio.core.model.CACHE_DIR`` — ``~/.cache/torch/pyannote``
    unless PYANNOTE_CACHE points elsewhere; found empirically here: the
    sub-models are NOT in the default HF hub cache), so that cache is checked
    first, the HF hub cache second. Fall back to a single snapshot dir;
    anything ambiguous fails loud (identity must be truthful, never guessed)."""
    from huggingface_hub.constants import HF_HUB_CACHE  # transitive dep of pyannote
    from pyannote.audio.core.model import CACHE_DIR as PYANNOTE_CACHE_DIR

    tried = []
    for cache_root in (PYANNOTE_CACHE_DIR, HF_HUB_CACHE):
        repo_dir = Path(cache_root) / f"models--{repo_id.replace('/', '--')}"
        ref_main = repo_dir / "refs" / "main"
        if ref_main.is_file():
            return ref_main.read_text(encoding="utf-8").strip()
        snapshots_dir = repo_dir / "snapshots"
        snapshots = (
            sorted(p.name for p in snapshots_dir.iterdir() if p.is_dir())
            if snapshots_dir.is_dir()
            else []
        )
        if len(snapshots) == 1:
            return snapshots[0]
        tried.append(f"{snapshots_dir} ({len(snapshots)} snapshots, no refs/main)")
    raise RuntimeError(
        f"cannot resolve the cached snapshot revision of {repo_id!r}; tried: "
        + "; ".join(tried)
    )


def _ffmpeg_version() -> str:
    """The ffmpeg version token (e.g. '7.1') — part of identity: ffmpeg is the
    decode front-end for every chunk, so its version is output-affecting."""
    proc = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True)
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError("ffmpeg -version failed — ffmpeg is required to decode chunks")
    first_line = proc.stdout.splitlines()[0].strip()
    parts = first_line.split()
    # "ffmpeg version 7.1 Copyright ..." -> "7.1"
    if len(parts) >= 3 and parts[0] == "ffmpeg" and parts[1] == "version":
        return parts[2]
    return first_line


def _decode_to_wav(audio_bytes: bytes, codec: str | None) -> bytes:
    """Decode the raw chunk to 16 kHz mono PCM WAV via ffmpeg (v0 behavior).

    pyannote loads audio through torchaudio, whose default backend on this stack
    is soundfile/libsndfile — which does NOT demux compressed capture containers
    ("Format not recognised" on webm/opus, found empirically on node-7
    2026-07-19). ffmpeg demuxes webm/opus + m4a/aac + wav uniformly, so we
    pre-decode to a WAV soundfile CAN read. 16 kHz mono matches pyannote's
    internal working rate, so this adds no resample the pipeline wouldn't
    already do. The temp file gets the codec-derived extension so ffmpeg picks
    the right demuxer. A decode failure is deterministic for the same bytes ->
    BackendError (422)."""
    ext = (codec or "wav").split(";")[0].split("/")[-1].strip().lower() or "wav"
    with tempfile.NamedTemporaryFile(suffix=f".{ext}") as src:
        src.write(audio_bytes)
        src.flush()
        proc = subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
             "-i", src.name, "-ac", "1", "-ar", "16000", "-f", "wav", "pipe:1"],
            capture_output=True,
        )
    if proc.returncode != 0 or not proc.stdout:
        raise BackendError(
            f"ffmpeg failed to decode the {codec!r} chunk for diarization: "
            f"{proc.stderr.decode('utf-8', 'replace')[:400]}"
        )
    return proc.stdout


def _normalize(raw_turns: list[tuple[float, float, str]]) -> list[dict]:
    """Map raw pyannote labels to a stable ``spk_0..`` vocabulary by first onset
    (raw label as the deterministic tie-break) — v0 behavior."""
    first_onset: dict[str, float] = {}
    for start, _end, raw in raw_turns:
        if raw not in first_onset or start < first_onset[raw]:
            first_onset[raw] = start
    order = sorted(first_onset, key=lambda r: (first_onset[r], r))
    remap = {raw: f"spk_{i}" for i, raw in enumerate(order)}
    return [
        {"start_s": start, "end_s": end, "speaker": remap[raw]}
        for (start, end, raw) in raw_turns
    ]


def _parse_params(params: dict) -> tuple[float, dict[str, int]]:
    """Validate /infer params — fail loud (BackendError -> 422) on anything off.

    span_seconds: REQUIRED finite float > 0 (the chunk's span; turns are clamped
    to it). min_speakers / max_speakers: optional ints >= 1 passed to the
    pipeline as clustering hints. Unknown params are a deterministic error, not
    silently ignored."""
    unknown = sorted(set(params) - _ALLOWED_PARAMS)
    if unknown:
        raise BackendError(f"unknown params: {unknown}; allowed: {sorted(_ALLOWED_PARAMS)}")

    if "span_seconds" not in params:
        raise BackendError("missing required param 'span_seconds'")
    raw_span = params["span_seconds"]
    if isinstance(raw_span, bool) or not isinstance(raw_span, (int, float)):
        raise BackendError(f"param 'span_seconds' must be a number, got {type(raw_span).__name__}")
    span_seconds = float(raw_span)
    if not math.isfinite(span_seconds) or span_seconds <= 0.0:
        raise BackendError(f"param 'span_seconds' must be a finite number > 0, got {raw_span!r}")

    hints: dict[str, int] = {}
    for key in ("min_speakers", "max_speakers"):
        if key not in params:
            continue
        value = params[key]
        if isinstance(value, bool) or not isinstance(value, int):
            raise BackendError(f"param {key!r} must be an integer, got {type(value).__name__}")
        if value < 1:
            raise BackendError(f"param {key!r} must be >= 1, got {value}")
        hints[key] = value
    if "min_speakers" in hints and "max_speakers" in hints \
            and hints["min_speakers"] > hints["max_speakers"]:
        raise BackendError(
            f"min_speakers ({hints['min_speakers']}) > max_speakers ({hints['max_speakers']})"
        )
    return span_seconds, hints


class PyannoteBackend(ModelBackend):
    name = "pyannote"

    def __init__(self) -> None:
        self._pipeline = None
        self._weights: dict | None = None
        self._frameworks: dict | None = None

    # ---- lifecycle -------------------------------------------------------------
    def load(self) -> None:
        import torch  # lazy: keep module import cheap/CPU-safe
        import torchaudio
        from importlib.metadata import version as _dist_version
        from pyannote.audio import Pipeline

        if not torch.cuda.is_available():
            raise RuntimeError(
                "CUDA is unavailable but this server's pinned device is 'cuda' — "
                "device is identity (L4); refusing to fall back to CPU."
            )

        # Operational auth only: the token's value is passed to from_pretrained and
        # never printed, logged, or written anywhere.
        token = (os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN") or "").strip() or None

        with _trusted_checkpoint_load():
            pipeline = Pipeline.from_pretrained(
                f"{MODEL_ID}@{REVISION}", use_auth_token=token
            )
        if pipeline is None:  # from_pretrained signals auth failure by returning None
            raise RuntimeError(
                "pyannote Pipeline.from_pretrained returned None — set a valid HF token "
                "(HF_TOKEN / HUGGINGFACE_TOKEN) and accept the gated user conditions for "
                "BOTH pyannote/speaker-diarization-3.1 and pyannote/segmentation-3.0."
            )
        pipeline.to(torch.device(DEVICE))  # in-place; AFTER load, never device= on from_pretrained
        self._pipeline = pipeline

        self._weights = {
            "pipeline_revision": REVISION,
            "segmentation_revision": _hf_cache_snapshot_revision(_SEGMENTATION_ID),
            "embedding_revision": _hf_cache_snapshot_revision(_EMBEDDING_ID),
        }
        self._frameworks = {
            "pyannote.audio": _dist_version("pyannote.audio"),
            "torch": torch.__version__,
            "torchaudio": torchaudio.__version__,
            "ffmpeg": _ffmpeg_version(),
        }

    def identity(self) -> dict:
        assert self._weights is not None and self._frameworks is not None
        return {
            "model_name": MODEL_ID,
            "weights": dict(self._weights),
            "frameworks": dict(self._frameworks),
            "device": DEVICE,
        }

    # ---- inference -------------------------------------------------------------
    def infer(self, request: InferRequest) -> dict:
        span_seconds, hints = _parse_params(request.params)

        if not request.input_b64:
            raise BackendError("missing input_b64 (raw audio container bytes, base64)")
        try:
            audio_bytes = base64.b64decode(request.input_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise BackendError(f"input_b64 is not valid base64: {exc}") from exc
        if not audio_bytes:
            raise BackendError("input_b64 decoded to zero bytes")

        wav_bytes = _decode_to_wav(audio_bytes, request.codec)

        # Pre-decoded WAV goes to the pipeline via a named temp file (v0 posture).
        with tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
            tmp.write(wav_bytes)
            tmp.flush()
            annotation = self._pipeline(tmp.name, **hints)

        raw_turns: list[tuple[float, float, str]] = []
        for segment, _track, label in annotation.itertracks(yield_label=True):
            start = min(max(float(segment.start), 0.0), span_seconds)
            end = min(max(float(segment.end), start), span_seconds)
            if end > start:
                raw_turns.append((start, end, str(label)))

        raw_turns.sort(key=lambda t: (t[0], t[1]))
        return {"turns": _normalize(raw_turns)}


if __name__ == "__main__":
    serve(PyannoteBackend())
