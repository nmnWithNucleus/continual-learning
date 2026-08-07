"""servers/ast — acoustic-event tagging (AST AudioSet classifier) behind the model-server seam.

Copied-from (not moved): app/audio/acoustic/ast.py. Semantics preserved: the transformers
``audio-classification`` pipeline consumes RAW container bytes — its ``ffmpeg_read`` shells
out to the system ffmpeg, so it demuxes whatever the capture surfaces emit (webm/opus,
mp4/aac, wav) and resamples to the model's 16 kHz. The server returns RAW tags; caption
folding (``caption_from_tags``) is pure post-processing and stays CLIENT-side.

Version law (L4): model id, revision, and device are pinned IN CODE below. No
output-affecting env vars; the runner reads only operational env (host/port/log level).
Device is identity: cuda, no CPU fallback — load fails loud if CUDA is unavailable.
"""
from __future__ import annotations

import base64
import binascii
import re
import subprocess

from dp_servers_common.backend import BackendError, ModelBackend
from dp_servers_common.runner import serve
from dp_servers_common.wire import InferRequest

MODEL_ID = "MIT/ast-finetuned-audioset-10-10-0.4593"
REVISION = "f826b80d28226b62986cc218e5cec390b1096902"
DEVICE = "cuda"
DEFAULT_TOP_K = 20 # the prior pipeline construction value (app/audio/acoustic/ast.py)

_ALLOWED_PARAMS = frozenset({"top_k"})


def _ffmpeg_version -> str:
 """Version of the system ffmpeg the pipeline's ffmpeg_read shells out to.

 ffmpeg is part of this server's identity: it does the demux + resample to 16 kHz
 before the model ever sees samples. Fails loud (in load) if ffmpeg is missing.
 """
 out = subprocess.run(
 ["ffmpeg", "-version"], capture_output=True, text=True, check=True
 ).stdout
 match = re.match(r"ffmpeg version (\S+)", out)
 if not match:
 raise RuntimeError(f"cannot parse ffmpeg version from: {out[:80]!r}")
 return match.group(1)


class AstBackend(ModelBackend):
 name = "ast"

 def __init__(self) -> None:
 self._pipe = None
 self._ffmpeg = None

 def load(self) -> None:
 import torch

 if not torch.cuda.is_available: # device is identity — no CPU fallback
 raise RuntimeError(
 "CUDA is not available; device 'cuda' is pinned identity for this "
 "server (no CPU fallback)"
 )
 self._ffmpeg = _ffmpeg_version # fail loud now, not on first infer

 from transformers import pipeline

 self._pipe = pipeline(
 task="audio-classification",
 model=MODEL_ID,
 revision=REVISION,
 device=0, # first visible GPU; the supervisor sets CUDA_VISIBLE_DEVICES
 )

 def identity(self) -> dict:
 import torch
 import transformers

 return {
 "model_name": MODEL_ID,
 "weights": {"revision": REVISION},
 "frameworks": {
 "transformers": transformers.__version__,
 "torch": torch.__version__,
 "ffmpeg": self._ffmpeg,
 },
 "device": DEVICE,
 }

 def infer(self, request: InferRequest) -> dict:
 unknown = set(request.params) - _ALLOWED_PARAMS
 if unknown:
 raise BackendError(f"unknown params: {sorted(unknown)}")
 top_k = request.params.get("top_k", DEFAULT_TOP_K)
 if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k < 1:
 raise BackendError(f"top_k must be a positive int, got {top_k!r}")

 if not request.input_b64:
 raise BackendError("input_b64 is required (raw audio container bytes)")
 try:
 audio_bytes = base64.b64decode(request.input_b64, validate=True)
 except (binascii.Error, ValueError) as exc:
 raise BackendError(f"input_b64 is not valid base64: {exc}") from exc
 if not audio_bytes:
 raise BackendError("input_b64 decoded to zero bytes")

 # request.codec is advisory only: ffmpeg demuxes by sniffing the container,
 # exactly as in prior (which never consulted the codec either).
 try:
 preds = self._pipe(audio_bytes, top_k=top_k)
 except ValueError as exc:
 # ffmpeg_read raises ValueError on undecodable input ("Malformed soundfile"):
 # deterministic — every replica fails these bytes identically.
 raise BackendError(f"undecodable audio input: {exc}") from exc
 # Anything else (CUDA errors, OOM,...) falls through to the framework's
 # 500-transient handler: /infer is side-effect-free, so retry is safe.

 tags = [{"label": p["label"], "score": float(p["score"])} for p in preds]
 tags.sort(key=lambda t: t["score"], reverse=True) # stable: keeps pipeline tie order
 return {"tags": tags}


if __name__ == "__main__":
 serve(AstBackend)
