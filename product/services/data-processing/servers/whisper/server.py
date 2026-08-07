"""servers/whisper — faster-whisper large-v3 behind the model-server seam.

One loaded WhisperModel serves BOTH tasks (v0 parity: the translate backend
reuses the ASR model with task="translate"). Decode semantics mirror
app/asr/faster_whisper.py exactly: faster-whisper decodes io.BytesIO(raw
container bytes) via av; vad_parameters={"min_silence_duration_ms": 500};
segment times are chunk-relative seconds; text = joined stripped segment texts.

L4: everything output-affecting is pinned IN CODE below — model id, weights
revision, device, compute type, param defaults. The only env this process reads
is operational (DP_SERVER_HOST/PORT/LOG_LEVEL via the runner; HF_TOKEN
passthrough is operational auth; CUDA_VISIBLE_DEVICES does physical GPU
pinning while device_index stays 0).
"""
from __future__ import annotations

import base64
import binascii
import ctypes
import glob
import io
import sysconfig
from importlib.metadata import version as pkg_version

from dp_servers_common.backend import BackendError, ModelBackend, TransientBackendError
from dp_servers_common.runner import serve
from dp_servers_common.wire import InferRequest

# ---- identity: pinned in code, surfaced verbatim at /health (L4) -------------
MODEL_ID = "Systran/faster-whisper-large-v3"
REVISION = "edaa852ec7e145841d8ffdb056a99866b5f0a478"
DEVICE = "cuda"
COMPUTE_TYPE = "float16"

# ---- /infer param surface: defaults pinned in code, never env ----------------
DEFAULT_TASK = "transcribe"
DEFAULT_BEAM_SIZE = 1
DEFAULT_LANGUAGE = None          # None = auto-detect (stages may pin "en")
DEFAULT_VAD = True
# v0 parity: 500 ms keeps natural pauses inside one segment; the kwarg is passed
# unconditionally (faster-whisper only applies it when vad_filter is on).
VAD_PARAMETERS = {"min_silence_duration_ms": 500}

_ALLOWED_PARAMS = {"task", "beam_size", "language", "vad"}
_TASKS = {"transcribe", "translate"}


def _preload_nvidia_libs() -> None:
    """ctypes-preload the venv's nvidia cublas/cudnn shared libs with
 RTLD_GLOBAL so ctranslate2 resolves them without LD_LIBRARY_PATH games.

 Some libs only resolve after a sibling is loaded, so retry in passes; a lib
 that truly cannot load fails loud later when the model constructs on CUDA.
 """
    pattern = f"{sysconfig.get_path('purelib')}/nvidia/*/lib/*.so*"
    pending = sorted(glob.glob(pattern))
    for _ in range(3):
        failed = []
        for path in pending:
            try:
                ctypes.CDLL(path, mode=ctypes.RTLD_GLOBAL)
            except OSError:
                failed.append(path)
        if not failed:
            return
        pending = failed


class WhisperBackend(ModelBackend):
    name = "whisper"

    def __init__(self) -> None:
        self._model = None
        # av's error base class, set in load(); () catches nothing before then.
        self._av_error: type[BaseException] | tuple = ()

    def load(self) -> None:
        _preload_nvidia_libs()  # BEFORE any CUDA-linked import
        import av
        import ctranslate2
        from faster_whisper import WhisperModel
        from huggingface_hub import snapshot_download

        if ctranslate2.get_cuda_device_count() < 1:
            raise RuntimeError(
                "CUDA unavailable (ctranslate2 sees 0 devices). Device is part of "
                "the server's identity — no CPU fallback (L4)."
            )

        # Resolve the exact snapshot ourselves and hand the resolved PATH to
        # WhisperModel — that pins the revision regardless of WhisperModel kwargs.
        model_path = snapshot_download(MODEL_ID, revision=REVISION)
        self._model = WhisperModel(
            model_path,
            device=DEVICE,
            device_index=0,  # CUDA_VISIBLE_DEVICES does the physical pinning
            compute_type=COMPUTE_TYPE,
        )
        self._av_error = av.FFmpegError

    def identity(self) -> dict:
        return {
            "model_name": MODEL_ID,
            "weights": {"revision": REVISION},
            "frameworks": {
                "faster_whisper": pkg_version("faster-whisper"),
                "ctranslate2": pkg_version("ctranslate2"),
                "av": pkg_version("av"),
                # onnxruntime executes the Silero VAD gate — output-affecting,
                # so it is part of the dialect (cleanup round 2026-08-06).
                "onnxruntime": pkg_version("onnxruntime"),
            },
            "device": DEVICE,
            "compute_type": COMPUTE_TYPE,
        }

    def infer(self, request: InferRequest) -> dict:
        params = request.params or {}
        unknown = set(params) - _ALLOWED_PARAMS
        if unknown:
            # Params are behavior — a typo must fail loud, not silently no-op.
            raise BackendError(
                f"unknown params {sorted(unknown)}; allowed: {sorted(_ALLOWED_PARAMS)}"
            )

        task = params.get("task", DEFAULT_TASK)
        if task not in _TASKS:
            raise BackendError(f"task must be one of {sorted(_TASKS)}, got {task!r}")
        beam_size = params.get("beam_size", DEFAULT_BEAM_SIZE)
        if isinstance(beam_size, bool) or not isinstance(beam_size, int) or beam_size < 1:
            raise BackendError(f"beam_size must be an int >= 1, got {beam_size!r}")
        language = params.get("language", DEFAULT_LANGUAGE)
        if language is not None and (not isinstance(language, str) or not language):
            raise BackendError(
                f"language must be a non-empty string or null, got {language!r}"
            )
        vad = params.get("vad", DEFAULT_VAD)
        if not isinstance(vad, bool):
            raise BackendError(f"vad must be a bool, got {vad!r}")

        if not request.input_b64:
            raise BackendError("input_b64 is required (raw audio container bytes)")
        try:
            raw = base64.b64decode(request.input_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise BackendError(f"input_b64 is not valid base64: {exc}") from exc
        if not raw:
            raise BackendError("input_b64 decoded to zero bytes")

        try:
            segment_iter, info = self._model.transcribe(
                io.BytesIO(raw),
                task=task,
                beam_size=beam_size,
                language=language,
                vad_filter=vad,
                vad_parameters=VAD_PARAMETERS,
            )
            segments: list[dict] = []
            parts: list[str] = []
            for seg in segment_iter:  # generator: decode/inference errors surface here too
                seg_text = (seg.text or "").strip()
                segments.append(
                    {"start_s": float(seg.start), "end_s": float(seg.end), "text": seg_text}
                )
                if seg_text:
                    parts.append(seg_text)
        except self._av_error as exc:
            raise BackendError(f"audio decode failed: {exc}") from exc
        except ValueError as exc:
            raise BackendError(f"undecodable input: {exc}") from exc
        except RuntimeError as exc:
            # ctranslate2 surfaces CUDA faults (OOM etc.) as RuntimeError —
            # replica-local, another replica may succeed.
            raise TransientBackendError(f"inference runtime failure: {exc}") from exc

        if task == "translate":
            out_language = "en"  # v0 parity: translate output is ALWAYS English
        else:
            out_language = getattr(info, "language", None) or "en"
        return {
            "text": " ".join(parts).strip(),
            "language": out_language,
            "segments": segments,
        }


if __name__ == "__main__":
    serve(WhisperBackend())
