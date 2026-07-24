"""WS-F — screen-video observability, failure semantics, dialect visibility.

Proves the WS-F exit criteria, headless (mock backends, no GPU, no ffmpeg, no network):

  * every parent-side counter is visible on /metrics AT ZERO before any traffic, and
    increments correctly (units / content-chars / empty-output / partial-write);
  * /health reports the current video dialect + the DP_DIALECT_FREEZE flag;
  * the `dp_pipeline_dialect` gauge is live, and a two-dialect fixture trips the
    fleet alert expression `count by (modality) (dp_pipeline_dialect) > 1`;
  * a video graph run with `resources=None` (the INGEST_ISOLATION=subprocess shape)
    does not raise;
  * DP_DIALECT_FREEZE=1 makes a redelivery SERVE the stale receipt instead of
    reprocessing under a just-flipped dialect;
  * the blob sha256 verify (now off the event loop) still terminally rejects corrupt
    bytes;
  * the VLM circuit breaker's state machine.

Multi-unit chunks are driven with a bogus video blob: under the mock backend an
undecodable blob falls back to SYNTHETIC_KEYFRAMES caption units — three records per
chunk, no decoder needed.
"""
from __future__ import annotations

import asyncio
import json
import re

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.conftest import make_c1
from tests.fake_storage import FakeStorage


# ============================================================================ #
#  Helpers                                                                      #
# ============================================================================ #

def _bogus_video_c1(fs: FakeStorage, *, chunk_id: str = "vid-1"):
    """A valid video C1 whose blob is undecodable — mock backend → 3 synthetic
    caption units, so a multi-record chunk needs no ffmpeg."""
    return make_c1(
        fs, chunk_id=chunk_id, modality="video", codec="video/mp4",
        audio=b"not-a-real-video-blob-\x00\x01\x02\x03",
        blob_ref=f"raw/pilot/{chunk_id}.mp4",
    )


def _series_value(text: str, needle: str):
    """Return the float value of the first exposition line starting with ``needle``,
    or None if absent."""
    for ln in text.splitlines():
        if ln.startswith(needle) and not ln.startswith("#"):
            return float(ln.rsplit(" ", 1)[1])
    return None


_DIALECT_RE = re.compile(
    r'^dp_pipeline_dialect\{modality="(?P<modality>[^"]*)",'
    r'pipeline_version="(?P<pv>[^"]*)"\}\s+\S+$'
)


def _distinct_dialects(text: str) -> dict[str, set[str]]:
    """Model PromQL's view of the gauge: a metric is a SET of series keyed by its label
    set, so identical (modality, pipeline_version) series scraped from two replicas
    collapse. Returns modality -> set(pipeline_version)."""
    out: dict[str, set[str]] = {}
    for ln in text.splitlines():
        m = _DIALECT_RE.match(ln)
        if m:
            out.setdefault(m["modality"], set()).add(m["pv"])
    return out


def _alert_fires(text: str) -> set[str]:
    """Evaluate the replica-robust mixed-dialect alert
        count(count by (modality,pipeline_version) (dp_pipeline_dialect)) by (modality) > 1
    over an exposition text (or a concatenation of several replicas' texts): the inner
    count collapses identical (modality, pipeline_version) series (i.e. replicas that
    agree), so the alert fires strictly on modalities carrying >1 DISTINCT dialect."""
    return {mod for mod, pvs in _distinct_dialects(text).items() if len(pvs) > 1}


# ============================================================================ #
#  Exit #1 — every parent-side counter visible at ZERO before any traffic       #
# ============================================================================ #

def test_new_counters_visible_at_zero_before_any_traffic(client):
    text = client.get("/metrics").text
    # Seeded to 0 for each registered modality's primary content kind.
    assert 'dp_units_total{modality="audio",kind="transcript"} 0' in text
    assert 'dp_units_total{modality="video",kind="caption"} 0' in text
    assert 'dp_units_total{modality="image",kind="caption"} 0' in text
    assert 'dp_units_total{modality="text",kind="text"} 0' in text
    assert 'dp_empty_output_total{modality="audio",kind="transcript"} 0' in text
    assert 'dp_empty_output_total{modality="video",kind="caption"} 0' in text
    assert 'dp_partial_write_total{modality="video"} 0' in text
    assert 'dp_partial_write_total{modality="audio"} 0' in text
    # The UNLABELLED stage-side counters carry a single series each, so they too are
    # seeded to 0 — the EXIT criterion is "all new counters visible at zero", and a
    # declared-but-never-inc'd counter renders NOTHING in this registry (see
    # test_metrics.test_empty_histogram_and_counter_emit_nothing).
    assert "\ndp_caption_ungrounded_quote_total 0\n" in text
    assert "\ndp_ocr_redactions_total 0\n" in text
    assert "\ndp_ocr_frame_errors_total 0\n" in text
    # The LABELLED stage-side families (parse_fallback{pack,step}, truncated{pass},
    # scenario_mismatch{expected,seen}) and the histograms cannot be pre-seeded — no label
    # values to guess / no observation yet — so they are correctly ABSENT until first emit.
    assert "dp_video_parse_fallback_total" not in text
    assert "dp_content_chars" not in text
    # Their HELP/TYPE headers render too (a declared+seeded family is fully present).
    assert "# TYPE dp_units_total counter" in text
    assert "# TYPE dp_empty_output_total counter" in text
    assert "# TYPE dp_partial_write_total counter" in text
    # The dialect gauge is live from t=0 (pull-time, =1 per modality).
    assert 'dp_pipeline_dialect{modality="video",pipeline_version="vidproc-mock-v0"} 1' in text
    assert "# TYPE dp_pipeline_dialect gauge" in text


# ============================================================================ #
#  Parent-side counters increment on real traffic (survive the per-unit loop)   #
# ============================================================================ #

def test_units_and_content_chars_count_per_unit(client):
    """A 3-unit synthetic video chunk bumps dp_units_total by 3 and records 3 char
    observations — the parent-side accounting the design puts in ingest_core's loop."""
    c1 = _bogus_video_c1(client.fake_storage, chunk_id="vt-1")
    resp = client.post("/ingest", json=c1)
    assert resp.status_code == 200, resp.text
    n = len(resp.json()["record_ids"])
    assert n == 3  # SYNTHETIC_KEYFRAMES

    text = client.get("/metrics").text
    assert _series_value(text, 'dp_units_total{modality="video",kind="caption"}') == n
    assert "# TYPE dp_content_chars histogram" in text
    assert _series_value(
        text, 'dp_content_chars_count{modality="video",kind="caption"}') == n
    # Non-empty mock captions → no empty-output for video.
    assert _series_value(
        text, 'dp_empty_output_total{modality="video",kind="caption"}') == 0


def test_empty_output_counts_all_modalities_not_just_audio(client, monkeypatch):
    """The generalized empty-output counter fires for any modality whose written unit
    has empty content.text — the audio-only guard is gone. Forced here with a video
    captioner that returns empty strings."""
    from app.vision import mock as vision_mock

    def _blank(vs, keyframes, c1):
        from app.vision.result import KeyframeCaption
        return [KeyframeCaption(index=k.index, caption="", ocr_text=None) for k in keyframes]

    monkeypatch.setattr(vision_mock, "caption", _blank)
    c1 = _bogus_video_c1(client.fake_storage, chunk_id="ve-1")
    resp = client.post("/ingest", json=c1)
    assert resp.status_code == 200, resp.text
    n = len(resp.json()["record_ids"])
    text = client.get("/metrics").text
    assert _series_value(
        text, 'dp_empty_output_total{modality="video",kind="caption"}') == n


# ============================================================================ #
#  Partial-write failure semantics (caveat A-4)                                 #
# ============================================================================ #

def _blob_then_failing_post_transport(blob_ref: str, blob: bytes, fail_from_post: int):
    """A MockTransport that serves the blob and fails /context POSTs from the
    ``fail_from_post``-th (1-indexed) onward with a 500 — so a multi-unit chunk writes
    unit 0 durably and then fails on unit 1."""
    state = {"posts": 0}

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/raw/blobs":
            return httpx.Response(200, content=blob,
                                  headers={"content-type": "application/octet-stream"})
        if request.method == "POST" and request.url.path == "/context/records":
            state["posts"] += 1
            if state["posts"] >= fail_from_post:
                return httpx.Response(500, json={"error": "boom"})
            rid = json.loads(request.content)["record_id"]
            return httpx.Response(200, json={"ok": True, "record_id": rid})
        return httpx.Response(404, json={"error": "unhandled"})

    return httpx.MockTransport(handle), state


def test_partial_write_counter_on_mid_chunk_storage_failure(monkeypatch, tmp_path):
    """A storage blip on unit 1 (after unit 0 is durably written) increments
    dp_partial_write_total and surfaces the transient 502 — the ONLY signal that a
    caption may sit in /context without its sibling record (A-4)."""
    monkeypatch.setenv("ASR_BACKEND", "mock")
    monkeypatch.setenv("STORAGE_URL", "http://storage.test")
    monkeypatch.setenv("DP_VAR_DIR", str(tmp_path / "var"))
    from app.main import create_app

    fs = FakeStorage()
    c1 = _bogus_video_c1(fs, chunk_id="pw-1")
    blob = fs.blobs[c1["blob_ref"]]
    transport, _state = _blob_then_failing_post_transport(c1["blob_ref"], blob, fail_from_post=2)

    app = create_app()
    app.state.storage._transport = transport
    with TestClient(app) as c:
        resp = c.post("/ingest", json=c1)
        # unit 1's write failed → transient ProcessingError → 502 at the inline boundary.
        assert resp.status_code == 502, resp.text
        text = c.get("/metrics").text

    assert _series_value(text, 'dp_partial_write_total{modality="video"}') == 1
    # Exactly the one unit that WAS durably written is counted (post-write accounting).
    assert _series_value(text, 'dp_units_total{modality="video",kind="caption"}') == 1


def test_full_chunk_failure_is_not_a_partial_write(monkeypatch, tmp_path):
    """A failure on the FIRST unit (nothing durable yet) is a clean full failure, not a
    partial write — the counter stays 0."""
    monkeypatch.setenv("ASR_BACKEND", "mock")
    monkeypatch.setenv("STORAGE_URL", "http://storage.test")
    monkeypatch.setenv("DP_VAR_DIR", str(tmp_path / "var"))
    from app.main import create_app

    fs = FakeStorage()
    c1 = _bogus_video_c1(fs, chunk_id="pw-2")
    blob = fs.blobs[c1["blob_ref"]]
    transport, _state = _blob_then_failing_post_transport(c1["blob_ref"], blob, fail_from_post=1)

    app = create_app()
    app.state.storage._transport = transport
    with TestClient(app) as c:
        resp = c.post("/ingest", json=c1)
        assert resp.status_code == 502, resp.text
        text = c.get("/metrics").text
    assert _series_value(text, 'dp_partial_write_total{modality="video"}') == 0


# ============================================================================ #
#  Exit #2 — /health reports the dialect + the freeze flag                       #
# ============================================================================ #

def test_health_reports_video_dialect_and_freeze_off_by_default(client):
    body = client.get("/health").json()
    assert body["video_pipeline_version"] == "vidproc-mock-v0"
    assert body["dialect_frozen"] is False


def test_health_reflects_dialect_freeze_flag(monkeypatch, tmp_path):
    monkeypatch.setenv("ASR_BACKEND", "mock")
    monkeypatch.setenv("STORAGE_URL", "http://storage.test")
    monkeypatch.setenv("DP_VAR_DIR", str(tmp_path / "var"))
    monkeypatch.setenv("DP_DIALECT_FREEZE", "1")
    from app.main import create_app

    app = create_app()
    with TestClient(app) as c:
        assert c.get("/health").json()["dialect_frozen"] is True


# ============================================================================ #
#  Exit #3 — the dialect gauge + the two-dialect alert expression               #
# ============================================================================ #

def test_single_replica_never_fires_the_alert(client):
    """One replica resolves exactly one dialect per modality → the alert never fires."""
    text = client.get("/metrics").text
    dialects = _distinct_dialects(text)
    assert dialects["video"] == {"vidproc-mock-v0"}
    assert _alert_fires(text) == set()


def test_two_dialect_fleet_trips_the_alert_expression(monkeypatch, tmp_path):
    """A rolling deploy: two replicas resolve two video dialects. Prometheus, scraping
    both, sees `count by (modality) (dp_pipeline_dialect) > 1` for video → the alert
    fires (exactly the drain-and-replace signal, D-14). Audio/image/text agree across
    the replicas → they never fire."""
    monkeypatch.setenv("ASR_BACKEND", "mock")
    monkeypatch.setenv("STORAGE_URL", "http://storage.test")
    monkeypatch.setenv("DP_VAR_DIR", str(tmp_path / "var"))
    from app.main import create_app

    app = create_app()  # one process; the gauge reads VIDEO_BACKEND fresh at each render

    monkeypatch.setenv("VIDEO_BACKEND", "mock")
    replica_a = app.state.metrics.render()  # video → vidproc-mock-v0
    monkeypatch.setenv("VIDEO_BACKEND", "vlm")
    replica_b = app.state.metrics.render()  # video → vidproc-vlm-v0

    # Each replica alone is clean.
    assert _alert_fires(replica_a) == set()
    assert _alert_fires(replica_b) == set()

    # The fleet (Prometheus scraping both) trips for video ONLY.
    fleet = replica_a + "\n" + replica_b
    assert _distinct_dialects(fleet)["video"] == {"vidproc-mock-v0", "vidproc-vlm-v0"}
    assert _alert_fires(fleet) == {"video"}


# ============================================================================ #
#  Exit #4 — a graph run with resources=None (the isolation shape) never raises  #
# ============================================================================ #

def test_video_graph_run_with_resources_none_does_not_raise():
    """Under INGEST_ISOLATION=subprocess the child runs the graph with no app-level
    resources handle (metrics=None). run_graph must tolerate resources=None."""
    from app.config import get_settings
    from app.stagegraph import StageContext, resolve, run_graph, stages_for

    settings = get_settings()
    resolved = resolve("video", stages_for("video"), settings)
    c1 = {"chunk_id": "iso-1", "codec": "video/mp4",
          "t_start": "2026-07-20T00:00:00Z", "t_end": "2026-07-20T00:00:05Z"}
    ctx = StageContext(c1=c1, blob=b"not-a-real-video", settings=settings,
                       span_seconds=5.0, resources=None)
    units = asyncio.run(run_graph(resolved, ctx))
    assert units  # synthetic keyframes → mock captions, no raise, no metrics touched


def test_video_processor_process_is_the_child_entry_and_survives_none_resources():
    """GraphProcessor.process() is exactly what the isolation child calls (resources
    default to None → wrapped to metrics=None). It must not raise."""
    from app.config import get_settings
    from app.processing.processors.video import VideoProcessor

    c1 = {"chunk_id": "iso-2", "codec": "video/mp4",
          "t_start": "2026-07-20T00:00:00Z", "t_end": "2026-07-20T00:00:05Z"}
    units = VideoProcessor().process(c1, b"not-a-real-video", get_settings(), 5.0)
    assert units


# ============================================================================ #
#  DP_DIALECT_FREEZE — serve the stale receipt, do not reprocess                 #
# ============================================================================ #

def _make_app(monkeypatch, fs, var_dir, extra_env=None):
    monkeypatch.setenv("ASR_BACKEND", "mock")
    monkeypatch.setenv("STORAGE_URL", "http://storage.test")
    monkeypatch.setenv("DP_VAR_DIR", var_dir)
    for k, v in (extra_env or {}).items():
        monkeypatch.setenv(k, v)
    from app.main import create_app
    app = create_app()
    app.state.storage._transport = fs.transport()
    return app


def test_dialect_freeze_serves_stale_receipt_without_reprocess(monkeypatch, tmp_path):
    """The flip-window contract (D-14): after a dialect bump, a redelivery of a chunk
    processed under the OLD dialect —
      * WITH DP_DIALECT_FREEZE=1 → serves the stored (stale) record_ids verbatim, no
        reprocess, no new /context writes (`_current_pv` returns None);
      * WITHOUT freeze → reprocesses (forks record_ids under the new dialect).

    The freeze branch runs FIRST: serving a stale receipt does not touch the journal, so
    the stored receipt is still the v0 one when the no-freeze branch then reprocesses and
    overwrites it.
    """
    var = str(tmp_path / "var")
    fs = FakeStorage()
    c1 = _bogus_video_c1(fs, chunk_id="fz-1")

    # --- App 1: process at the ORIGINAL dialect (vidproc-mock-v0) → journal receipt.
    app1 = _make_app(monkeypatch, fs, var)
    with TestClient(app1) as c:
        r1 = c.post("/ingest", json=c1)
        assert r1.status_code == 200, r1.text
        orig_ids = r1.json()["record_ids"]
    posts_after_1 = len(fs.record_posts)

    # --- Simulate a deployed dialect bump: the video dialect is now vidproc-mock-v1.
    from app.processing.processors.video import VideoProcessor
    monkeypatch.setattr(VideoProcessor, "pipeline_version",
                        lambda self, settings: "vidproc-mock-v1")

    # --- App 2 (fresh in-mem dedup, shared journal, FREEZE armed): serves stale ids and
    #     does NOT reprocess — even though the current dialect (v1) != the stored one.
    app2 = _make_app(monkeypatch, fs, var, extra_env={"DP_DIALECT_FREEZE": "1"})
    with TestClient(app2) as c:
        r2 = c.post("/ingest", json=c1)
        assert r2.status_code == 200, r2.text
        served_ids = r2.json()["record_ids"]
    assert served_ids == orig_ids                 # stale receipt served verbatim
    assert len(fs.record_posts) == posts_after_1  # NO new writes — no reprocess

    # --- App 3 (fresh in-mem dedup, shared journal, NO freeze): reprocesses under v1.
    #     Explicitly clear the freeze env App 2 set (monkeypatch.setenv persists).
    app3 = _make_app(monkeypatch, fs, var, extra_env={"DP_DIALECT_FREEZE": "0"})
    with TestClient(app3) as c:
        r3 = c.post("/ingest", json=c1)
        assert r3.status_code == 200, r3.text
        new_ids = r3.json()["record_ids"]
    assert new_ids != orig_ids                    # forked to the new dialect
    assert len(fs.record_posts) > posts_after_1   # it reprocessed (re-posted)


# ============================================================================ #
#  The blob sha256 verify (now off the event loop) still rejects corrupt bytes  #
# ============================================================================ #

def test_offloaded_sha256_still_rejects_corrupt_blob(monkeypatch, tmp_path):
    """Moving the hash to a threadpool must be behaviour-preserving: a blob whose bytes
    don't match the declared blob_sha256 is still a terminal 502."""
    monkeypatch.setenv("ASR_BACKEND", "mock")
    monkeypatch.setenv("STORAGE_URL", "http://storage.test")
    monkeypatch.setenv("DP_VAR_DIR", str(tmp_path / "var"))
    monkeypatch.setenv("VERIFY_BLOB_SHA256", "1")
    from app.main import create_app

    fs = FakeStorage()
    c1 = make_c1(fs, chunk_id="sha-1")          # blob_sha256 = sha256(SAMPLE_AUDIO)
    fs.blobs[c1["blob_ref"]] = b"corrupted-bytes-that-do-not-match"  # tamper post-hoc

    app = create_app()
    app.state.storage._transport = fs.transport()
    with TestClient(app) as c:
        resp = c.post("/ingest", json=c1)
    assert resp.status_code == 502, resp.text
    assert "sha256 mismatch" in resp.text


# ============================================================================ #
#  VLM circuit breaker — the fast-fail-before-ffmpeg state machine              #
# ============================================================================ #

class _Clock:
    """A hand-cranked monotonic clock for deterministic breaker tests."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def test_circuit_opens_after_threshold_consecutive_failures():
    from app.vision.circuit import CircuitBreaker, CLOSED, OPEN
    clk = _Clock()
    cb = CircuitBreaker(threshold=3, cooldown_s=10.0, clock=clk)
    assert cb.state == CLOSED and cb.allow() is True
    cb.record_failure(); cb.record_failure()
    assert cb.state == CLOSED and cb.allow() is True   # 2 < 3
    cb.record_failure()                                # 3rd → OPEN
    assert cb.state == OPEN
    assert cb.allow() is False                         # fast-fail before ffmpeg


def test_circuit_success_resets_the_consecutive_counter():
    from app.vision.circuit import CircuitBreaker, CLOSED
    cb = CircuitBreaker(threshold=3, cooldown_s=10.0, clock=_Clock())
    cb.record_failure(); cb.record_failure()
    cb.record_success()                                # a reachable reply resets it
    assert cb.consecutive_failures == 0
    cb.record_failure(); cb.record_failure()
    assert cb.state == CLOSED and cb.allow() is True   # not yet 3 in a row


def test_circuit_half_opens_after_cooldown_and_admits_one_probe():
    from app.vision.circuit import CircuitBreaker, OPEN, HALF_OPEN, CLOSED
    clk = _Clock()
    cb = CircuitBreaker(threshold=1, cooldown_s=10.0, clock=clk)
    cb.record_failure()
    assert cb.state == OPEN and cb.allow() is False
    clk.advance(9.9)
    assert cb.allow() is False                         # cooldown not elapsed
    clk.advance(0.2)                                   # 10.1s total
    assert cb.allow() is True                          # promoted to HALF_OPEN, one probe
    assert cb.state == HALF_OPEN
    assert cb.allow() is False                         # the single probe is already out
    cb.record_success()                                # probe reached the server
    assert cb.state == CLOSED and cb.allow() is True


def test_circuit_half_open_failure_reopens_and_restarts_cooldown():
    from app.vision.circuit import CircuitBreaker, OPEN
    clk = _Clock()
    cb = CircuitBreaker(threshold=1, cooldown_s=10.0, clock=clk)
    cb.record_failure()
    clk.advance(10.1)
    assert cb.allow() is True                          # HALF_OPEN probe
    cb.record_failure()                                # probe still dead → reopen
    assert cb.state == OPEN
    assert cb.allow() is False                         # cooldown restarted from now
    clk.advance(10.1)
    assert cb.allow() is True                           # eligible again


def test_circuit_half_open_stale_probe_self_heals():
    """A probe whose outcome is never recorded (a caller that forgot its try/finally, or
    died) must not wedge the breaker HALF_OPEN forever. A probe older than one cooldown
    is stale, and a fresh probe is re-admitted — so a broken caller can't permanently
    fast-fail a recovered endpoint."""
    from app.vision.circuit import CircuitBreaker, HALF_OPEN
    clk = _Clock()
    cb = CircuitBreaker(threshold=1, cooldown_s=10.0, clock=clk)
    cb.record_failure()
    clk.advance(10.1)
    assert cb.allow() is True                           # probe admitted, never reported
    assert cb.state == HALF_OPEN
    assert cb.allow() is False                          # in-flight probe still fresh
    clk.advance(9.9)
    assert cb.allow() is False                          # not yet stale (< cooldown)
    clk.advance(0.2)                                    # 10.1s since admission
    assert cb.allow() is True                           # stale probe → re-admit (self-heal)


def test_breaker_for_shares_one_instance_per_key():
    from app.vision import circuit
    circuit.reset_all()
    try:
        a = circuit.breaker_for("http://vl.test:8000", threshold=2, cooldown_s=5.0)
        b = circuit.breaker_for("http://vl.test:8000")
        other = circuit.breaker_for("http://ocr.test:9000")
        assert a is b                                  # same key → shared state
        assert a is not other                          # a captioner outage ≠ OCR outage
        a.record_failure(); a.record_failure()
        assert b.state == "open"                        # state is shared
        assert other.state == "closed"
    finally:
        circuit.reset_all()


def test_breaker_rejects_bad_threshold():
    from app.vision.circuit import CircuitBreaker
    with pytest.raises(ValueError):
        CircuitBreaker(threshold=0)
