"""dp_servers_common — the shared model-server framework ( L9 machinery).

Every model server (whisper, pyannote, ast, ocr) is this framework plus one
ModelBackend implementation: load once at startup (warmup), report identity at
/health, serve /infer. Behavior lives in backend code and the supervisor manifest;
env vars here are operational-only (host, port, log level) per L4.
"""
