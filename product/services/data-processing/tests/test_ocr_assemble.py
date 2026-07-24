"""WS-C seam: OCR post-processing (assemble/redact), the backend resolver, and the ppocr
client helpers — all pure/headless, no network, no GPU, mock default.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.vision.clip_types import OcrRead, OcrRegion
from app.vision.ocr import assemble, ppocr, redact
from app.vision.ocr import _resolve, select, version_tag
from app.vision.ocr.assemble import ROLES, assign_role, ocr_cap, render, truncate_word
from app.vision.ocr.config import OcrConfig

_FIXTURES = Path(__file__).parent / "fixtures" / "ocr_truth"


def _cfg(**over) -> OcrConfig:
    base = dict(
        ocr_backend="mock", min_conf=0.60, min_chars=4, dedup_ratio=0.92,
        ocr_chars_per_second=6.0, max_events=3, idle_peak=8, layout_peak=40,
        floor_s=120.0, frame_width=1728, url="http://ocr.test", timeout=15.0,
        model_sha_det="", model_sha_rec="", ep="cpu", model="", api_key="", max_tokens=900,
    )
    base.update(over)
    return OcrConfig(**base)


def _region(text, bbox=(0.1, 0.4, 0.9, 0.45), conf=0.95, role=""):
    return OcrRegion(text=text, role=role, bbox=bbox, conf=conf)


# ============================ redaction (D-07 step 4) ============================

# The six shapes the exit criteria pin, each -> [redacted:secret] exactly once.
SECRET_CASES = {
    "aws_key": "AKIAIOSFODNN7EXAMPLE",
    "sk_key": "sk-abcdEFGH1234ijklMNOP5678qrst",
    "ghp_token": "ghp_16CharsAtLeast0123456789abcdefABCD",
    "base64_blob": "dGhpcyBpcyBhIHZlcnkgbG9uZyBiYXNlNjQgYmxvYg==",
    "pem_header": "-----BEGIN RSA PRIVATE KEY-----",
    "luhn_card": "4111 1111 1111 1111",
}


@pytest.mark.parametrize("name,secret", sorted(SECRET_CASES.items()))
def test_redaction_each_secret_case(name, secret):
    text = f"user typed {secret} into the field"
    out, n = redact.redact(text)
    assert n == 1, f"{name}: expected exactly one redaction"
    assert redact.REDACTED in out
    assert secret not in out, f"{name}: raw secret leaked through"


def test_redaction_counts_every_span_and_is_deterministic():
    text = f"{SECRET_CASES['aws_key']} and {SECRET_CASES['sk_key']} and {SECRET_CASES['ghp_token']}"
    out1, n1 = redact.redact(text)
    out2, n2 = redact.redact(text)
    assert (out1, n1) == (out2, n2)         # deterministic
    assert n1 == 3
    assert out1.count(redact.REDACTED) == 3


def test_redaction_slack_and_masked_field():
    out, n = redact.redact("token xoxb-2401-ABCDEF123456 password ••••••••")
    assert n == 2 and out.count(redact.REDACTED) == 2


def test_redaction_masked_field_precision():
    # Real masked fields ARE redacted...
    for masked in ("password ••••••••", "PIN: ●●●●●●", "Password: ********"):
        out, n = redact.redact(masked)
        assert n == 1 and redact.REDACTED in out, masked
    # ...but ordinary on-screen markdown / comment asterisks are LEFT ALONE (the
    # false-positive class a bare [*]{3,} rule would over-redact).
    for benign in ("note ***important*** here", "# *** section ***", "banner /*****/ end", "a ** b"):
        out, n = redact.redact(benign)
        assert n == 0 and out == benign, benign


def test_redaction_leaves_ordinary_text_and_non_luhn_numbers():
    # A 16-digit build id that FAILS the Luhn check must not be scrubbed.
    text = "build 1234567890123456 shipped to prod at 09:41"
    out, n = redact.redact(text)
    assert n == 0 and out == text


# ============================ region role (D-07 step 2) =========================

def _load(name):
    return json.loads((_FIXTURES / name).read_text())


@pytest.mark.parametrize("fixture", ["gmail_compose.json", "terminal_scroll.json"])
def test_assign_role_matches_fixture_ground_truth(fixture):
    data = _load(fixture)
    for r in data["regions"]:
        got = assign_role(tuple(r["bbox"]))
        assert got == r["expect_role"], f'{fixture}: {r["text"]!r} -> {got}, want {r["expect_role"]}'
        assert got in ROLES


def test_assign_role_degenerate_bbox_is_main():
    # A zero-area / no-geometry bbox (e.g. the vlm arm's sentinel) must default to the
    # content region, NOT fall through cx=cy=0 into the titlebar band.
    assert assign_role((0.0, 0.0, 0.0, 0.0)) == "main"
    assert assign_role((0.5, 0.5, 0.5, 0.5)) == "main"   # zero width+height, centered
    assert assign_role((0.8, 0.9, 0.2, 0.1)) == "main"   # inverted (x1<x0)


def test_assign_role_covers_every_band():
    cases = {
        "titlebar": (0.0, 0.0, 0.4, 0.03),
        "notification": (0.86, 0.05, 0.99, 0.10),
        "tab": (0.02, 0.08, 0.12, 0.11),
        "toolbar": (0.60, 0.08, 0.72, 0.11),
        "statusbar": (0.60, 0.97, 0.95, 0.99),
        "sidebar": (0.02, 0.40, 0.15, 0.44),
        "dialog": (0.40, 0.45, 0.60, 0.55),
        "compose": (0.30, 0.85, 0.60, 0.90),
        "main": (0.30, 0.40, 0.80, 0.45),
    }
    for want, bbox in cases.items():
        assert assign_role(bbox) == want, f"{bbox} -> want {want}"


# ============================ render pipeline (D-07 2/3/5/6, D-12) ==============

def test_render_conf_and_minchars_gates_from_fixture():
    data = _load("gmail_compose.json")
    read = OcrRead(t_offset_s=data["frame"]["t_offset_s"],
                   regions=tuple(_region(r["text"], tuple(r["bbox"]), r["conf"]) for r in data["regions"]))
    # span=120 -> 720-char budget, so this test isolates the conf/min-chars/role gates
    # from the (separately tested) budget truncation.
    line, n = render([read], _cfg(), span_seconds=120.0)
    kept = [r for r in data["regions"] if r["keep"]]
    dropped = [r for r in data["regions"] if not r["keep"]]
    for r in kept:
        assert r["text"] in line
    for r in dropped:
        assert r["text"] not in line
    # self-anchored: carries a +Ns offset and a role word.
    assert "+0s" in line
    assert any(f"{role}:" in line for role in ROLES)


def test_render_is_single_line_even_with_embedded_newlines():
    read = OcrRead(t_offset_s=0.0, regions=(
        _region("first line\nsecond line\tthird", bbox=(0.1, 0.4, 0.9, 0.45)),
    ))
    line, _ = render([read], _cfg(), span_seconds=60.0)
    assert "\n" not in line and "\r" not in line and "\t" not in line
    assert "first line second line third" in line


def test_render_within_chunk_dedup():
    # Same string read on two frames -> the second is dropped (>= dedup_ratio).
    reads = [
        OcrRead(t_offset_s=0.0, regions=(_region("Re: Q3 deck review with the team"),)),
        OcrRead(t_offset_s=5.0, regions=(_region("Re: Q3 deck review with the team"),)),
    ]
    line, _ = render(reads, _cfg(), span_seconds=60.0)
    assert line.count("Re: Q3 deck review") == 1


def test_render_empty_reads_is_empty_string():
    line, n = render([], _cfg(), span_seconds=10.0)
    assert line == "" and n == 0


def test_render_redacts_and_counts_through_the_pipeline():
    read = OcrRead(t_offset_s=0.0, regions=(
        _region(f"key is {SECRET_CASES['sk_key']} keep private"),
    ))
    line, n = render([read], _cfg(), span_seconds=60.0)
    assert n == 1 and redact.REDACTED in line and SECRET_CASES["sk_key"] not in line


def test_render_truncates_on_word_boundary_at_budget():
    # ocr_cap = round(6 * span). At span=6 -> 36 chars.
    read = OcrRead(t_offset_s=0.0, regions=(
        _region("alpha bravo charlie delta echo foxtrot golf hotel india"),
    ))
    line, _ = render([read], _cfg(), span_seconds=6.0)
    assert len(line) <= 36
    assert not line.endswith(" ")
    # never a mid-word cut: the truncated text ends on a whole token.
    assert line.split(" ")[-1] in "+0s main: alpha bravo charlie delta echo foxtrot golf hotel india".split(" ")


# ============================ budget helpers (D-11) =============================

@pytest.mark.parametrize("span,expected", [(10.0, 60), (60.0, 360), (0.0, 0), (2.5, 15)])
def test_ocr_cap(span, expected):
    assert ocr_cap(span, _cfg()) == expected


def test_truncate_word_boundaries():
    assert truncate_word("hello world foobar", 100) == "hello world foobar"  # under budget
    assert truncate_word("hello world foobar", 8) == "hello"                 # back off to space
    assert truncate_word("anything", 0) == ""                                # no budget
    assert truncate_word("supercalifragilistic", 5) == "super"               # single long word: hard cut


# ============================ backend resolver (D-06) ===========================

def test_resolver_known_backends():
    assert _resolve(_cfg(ocr_backend="mock")) == "mock"
    assert _resolve(_cfg(ocr_backend="ppocr")) == "ppocr"
    assert _resolve(_cfg(ocr_backend="vlm")) == "vlm"
    assert _resolve(_cfg(ocr_backend="off")) == "off"


def test_unknown_backend_is_off_in_both_resolvers():
    unknown = _cfg(ocr_backend="pp-ocr-typo")
    off = _cfg(ocr_backend="off")
    # select(): both None
    assert select(unknown) is None and select(off) is None
    # version_tag(): identical, and NON-empty (screentext feeds the caption -> R1)
    assert version_tag(unknown) == version_tag(off) == "+ocr-off-v1"
    assert version_tag(unknown) != ""


def test_version_tags_are_all_nonempty_and_distinct():
    tags = {b: version_tag(_cfg(ocr_backend=b)) for b in ("off", "mock", "ppocr", "vlm")}
    assert all(tags.values())                       # none empty (R1)
    assert len(set(tags.values())) == 4             # distinct dialects
    assert tags["ppocr"] == "+ocr-ppv6-cpu-v1"      # the ratified production token


def test_select_returns_modules():
    assert select(_cfg(ocr_backend="mock")).__name__.endswith("ocr.mock")
    assert select(_cfg(ocr_backend="ppocr")).__name__.endswith("ocr.ppocr")


# ============================ ppocr client helpers =============================

def test_ppocr_health_mismatch_pure():
    cfg = _cfg(model_sha_det="detAAA", model_sha_rec="recBBB")
    assert ppocr._health_mismatch({"ok": True, "model_sha_det": "detAAA", "model_sha_rec": "recBBB"}, cfg) is None
    assert "not ok" in ppocr._health_mismatch({"ok": False}, cfg)
    assert "model_sha_det mismatch" in ppocr._health_mismatch(
        {"ok": True, "model_sha_det": "WRONG", "model_sha_rec": "recBBB"}, cfg)
    # Unpinned shas ('') skip the compare but still require ok.
    assert ppocr._health_mismatch({"ok": True}, _cfg()) is None


def test_ppocr_assert_health_over_mock_transport(monkeypatch):
    cfg = _cfg(ocr_backend="ppocr", model_sha_det="D1", model_sha_rec="R1", url="http://ocr.test")
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        assert request.url.path == "/health"
        return httpx.Response(200, json={"ok": True, "model_sha_det": "D1", "model_sha_rec": "R1",
                                         "ort_version": "1.20.0", "ep": "cpu"})

    monkeypatch.setattr(ppocr, "make_client",
                        lambda c: httpx.Client(transport=httpx.MockTransport(handler)))
    ppocr._HEALTH_VERIFIED.clear()
    ppocr.assert_health(cfg)                 # matches -> no raise
    ppocr.assert_health(cfg)                 # cached -> no second call
    assert calls["n"] == 1


def test_ppocr_assert_health_raises_on_sha_mismatch(monkeypatch):
    cfg = _cfg(ocr_backend="ppocr", model_sha_det="EXPECTED", model_sha_rec="R1", url="http://ocr.test")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "model_sha_det": "SWAPPED", "model_sha_rec": "R1"})

    monkeypatch.setattr(ppocr, "make_client",
                        lambda c: httpx.Client(transport=httpx.MockTransport(handler)))
    ppocr._HEALTH_VERIFIED.clear()
    with pytest.raises(RuntimeError, match="mismatch"):
        ppocr.assert_health(cfg)


def test_ppocr_read_normalizes_bbox_and_assigns_role_downstream(monkeypatch):
    cfg = _cfg(ocr_backend="ppocr", url="http://ocr.test")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/ocr"
        return httpx.Response(200, json={
            "regions": [
                {"text": "Inbox - Gmail", "bbox": [0, 0, 691, 32], "conf": 0.97},
                {"text": "hello world body", "bbox": [173, 486, 1555, 540], "conf": 0.9},
            ],
            "engine": "ppocrv6", "model_sha_det": "D", "model_sha_rec": "R",
        })

    from app.vision.clip_types import Frame
    client = httpx.Client(transport=httpx.MockTransport(handler))
    frame = Frame(index=0, t_offset_s=0.0, jpeg_lo=None, jpeg_hi=b"\xff\xd8\xff\xd9not-a-real-jpeg")
    read = ppocr.read(cfg, frame, client, "chunk-1")
    # pixel bbox normalized to 0..1 (no real jpeg dims -> config-width + 16:10 estimate).
    assert all(0.0 <= c <= 1.0 for r in read.regions for c in r.bbox)
    line, _ = render([read], cfg, span_seconds=60.0)
    assert "Inbox - Gmail" in line and "titlebar:" in line


def test_vlm_read_role_handling(monkeypatch):
    from app.vision.clip_types import Frame
    from app.vision.ocr import vlm

    cfg = _cfg(ocr_backend="vlm", url="http://ocr.test", model="qwen")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        content = (
            '{"regions":[{"role":"compose","text":"Reply to Sarah here"},'
            '{"role":"body","text":"Meeting notes for Q3 review"}]}'
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    frame = Frame(index=0, t_offset_s=0.0, jpeg_lo=None, jpeg_hi=b"\xff\xd8jpeg")
    read = vlm.read(cfg, frame, client, "chunk-1")
    line, _ = render([read], cfg, span_seconds=60.0)
    # a valid model role is kept; an off-vocab role ("body") resolves to "main", NEVER
    # "titlebar" (the zero-bbox degenerate-guard path).
    assert "compose: Reply to Sarah here" in line
    assert "main: Meeting notes for Q3 review" in line
    assert "titlebar:" not in line
