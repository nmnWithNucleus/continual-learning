"""Unit tests for the zero-dependency Prometheus metrics registry (app/metrics.py).

Pure + headless — no model, GPU, or extra package. Proves the exposition format is
valid Prometheus text: correct HELP/TYPE headers, label escaping, cumulative
histogram buckets (_bucket/_sum/_count), and pull-time gauge sources.
"""
from __future__ import annotations

import math

from app.metrics import Metrics


def _lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln]


def test_counter_render_and_aggregation():
    m = Metrics()
    m.declare_counter("dp_ingest_total", "ingest events", ["modality", "result"])
    m.inc("dp_ingest_total", {"modality": "audio", "result": "accepted"})
    m.inc("dp_ingest_total", {"modality": "audio", "result": "accepted"}, 2)
    m.inc("dp_ingest_total", {"modality": "video", "result": "processed"})
    out = m.render()
    assert "# HELP dp_ingest_total ingest events" in out
    assert "# TYPE dp_ingest_total counter" in out
    assert 'dp_ingest_total{modality="audio",result="accepted"} 3' in out
    assert 'dp_ingest_total{modality="video",result="processed"} 1' in out
    # trailing newline (scrapers are lenient but the format ends with one)
    assert out.endswith("\n")


def test_gauge_set_overwrites():
    m = Metrics()
    m.declare_gauge("dp_inflight", "in-flight chunks")
    m.set("dp_inflight", 5)
    m.set("dp_inflight", 3)
    assert "dp_inflight 3" in m.render()
    assert "dp_inflight 5" not in m.render()


def test_histogram_cumulative_buckets_sum_count():
    m = Metrics()
    m.declare_histogram("dp_proc_seconds", "proc latency", ["modality"],
                        buckets=[0.1, 1.0, 10.0])
    for v in (0.05, 0.5, 5.0, 50.0):
        m.observe("dp_proc_seconds", v, {"modality": "audio"})
    out = m.render()
    assert "# TYPE dp_proc_seconds histogram" in out
    # Cumulative: <=0.1 -> 1, <=1 -> 2, <=10 -> 3, <=+Inf -> 4
    assert 'dp_proc_seconds_bucket{modality="audio",le="0.1"} 1' in out
    assert 'dp_proc_seconds_bucket{modality="audio",le="1"} 2' in out
    assert 'dp_proc_seconds_bucket{modality="audio",le="10"} 3' in out
    assert 'dp_proc_seconds_bucket{modality="audio",le="+Inf"} 4' in out
    assert 'dp_proc_seconds_sum{modality="audio"} 55.55' in out
    assert 'dp_proc_seconds_count{modality="audio"} 4' in out
    # +Inf bucket count always equals _count (every obs falls in it)
    assert "+Inf" in out


def test_histogram_inf_bucket_always_present_even_if_not_declared():
    m = Metrics()
    m.declare_histogram("h", "h", buckets=[1.0])
    m.observe("h", 0.5)
    out = m.render()
    assert 'h_bucket{le="1"} 1' in out
    assert 'h_bucket{le="+Inf"} 1' in out


def test_label_value_escaping():
    m = Metrics()
    m.declare_counter("dp_weird_total", "weird", ["path"])
    m.inc("dp_weird_total", {"path": 'a"b\\c\nd'})
    line = next(l for l in _lines(m.render()) if l.startswith("dp_weird_total{"))
    # backslash, double-quote, newline all escaped
    assert line == 'dp_weird_total{path="a\\"b\\\\c\\nd"} 1'


def test_pull_time_gauge_source_scalar_and_labelled():
    m = Metrics()
    depth = {"v": 0}
    m.add_gauge_source("dp_queue_depth", "queue depth", lambda: depth["v"])
    m.add_gauge_source(
        "dp_missing_total", "missing per modality",
        lambda: [(("audio",), 0), (("video",), 2)], labelnames=["modality"],
    )
    depth["v"] = 9  # changed AFTER registration — read at render time
    out = m.render()
    assert "dp_queue_depth 9" in out
    assert 'dp_missing_total{modality="audio"} 0' in out
    assert 'dp_missing_total{modality="video"} 2' in out


def test_broken_gauge_source_never_breaks_the_scrape():
    m = Metrics()
    m.declare_counter("ok_total", "ok")
    m.inc("ok_total")

    def _boom():
        raise RuntimeError("source blew up")

    m.add_gauge_source("bad", "bad", _boom)
    out = m.render()  # must not raise
    assert "ok_total 1" in out
    assert "bad" not in out  # the broken source is simply skipped


def test_unknown_label_set_raises():
    m = Metrics()
    m.declare_counter("c_total", "c", ["a"])
    try:
        m.inc("c_total", {"b": "x"})
    except KeyError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected KeyError for wrong label set")


def test_empty_histogram_and_counter_emit_nothing():
    m = Metrics()
    m.declare_histogram("never_observed", "h")
    m.declare_counter("never_inc_total", "c", ["x"])
    out = m.render()
    assert "never_observed" not in out
    assert "never_inc_total" not in out


def test_declaration_is_idempotent():
    m = Metrics()
    m.declare_counter("c_total", "first help")
    m.declare_counter("c_total", "second help")  # ignored — first wins
    m.inc("c_total")
    assert "# HELP c_total first help" in m.render()


def test_float_formatting():
    m = Metrics()
    m.declare_gauge("g", "g")
    m.set("g", 3.0)          # exact int -> no ".0"
    assert "\ng 3\n" in m.render() or m.render().strip().endswith("g 3")
    m.set("g", 2.5)
    assert "g 2.5" in m.render()
    m.set("g", math.inf)
    assert "g +Inf" in m.render()


def test_graph_stage_latency_is_emitted_per_stage(client):
    """M8 win: the stage graph emits per-STAGE latency (asr/…) not just the coarse
    'process' stage — the intra-pipeline granularity the charter asks for."""
    from tests.conftest import make_c1
    client.post("/ingest", json=make_c1(client.fake_storage, chunk_id="gs-1"))
    text = client.get("/metrics").text
    assert 'dp_graph_stage_seconds_count{modality="audio",stage="asr"}' in text
    # The coarse whole-processor stage is still emitted (metric contract unchanged).
    assert 'dp_stage_seconds_count{modality="audio",stage="process"}' in text
