"""OCR post-processing (assemble/redact) — pure/headless, no network, no GPU.

Rebuilt for the DP rebuild: the v0 backend resolver (`_resolve`/`select`/`version_tag`)
and the ppocr sidecar client died with the env-selected seam (the engine is
`servers/ocr`, the stage is `app/stages/video/screentext.py`, identity is the stage's
Backend + the manifest's sha pins). What remains here is the KEPT pure pipeline:
redact (the access control) + assign_role + render — now under EXPLICIT keyword pins
(L4), exercised at the screentext stage's pinned values.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.vision.budget import ocr_cap, truncate_word
from app.vision.clip_types import OcrRead, OcrRegion
from app.vision.ocr import assemble, redact
from app.vision.ocr.assemble import ROLES, assign_role, render

_FIXTURES = Path(__file__).parent / "fixtures" / "ocr_truth"

# The screentext stage's code pins (app/stages/video/screentext.py) — the one live
# configuration; passed explicitly, the way the stage calls render.
PINS = dict(min_conf=0.60, min_chars=4, dedup_ratio=0.92, chars_per_second=6.0)


def _render(reads, span_seconds, **over):
    kw = {**PINS, **over}
    return render(reads, span_seconds, **kw)


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
    # A zero-area / no-geometry bbox must default to the content region, NOT fall
    # through cx=cy=0 into the titlebar band.
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
    line, n = _render([read], 120.0)
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
    line, _ = _render([read], 60.0)
    assert "\n" not in line and "\r" not in line and "\t" not in line
    assert "first line second line third" in line


def test_render_within_chunk_dedup():
    # Same string read on two frames -> the second is dropped (>= dedup_ratio).
    reads = [
        OcrRead(t_offset_s=0.0, regions=(_region("Re: Q3 deck review with the team"),)),
        OcrRead(t_offset_s=5.0, regions=(_region("Re: Q3 deck review with the team"),)),
    ]
    line, _ = _render(reads, 60.0)
    assert line.count("Re: Q3 deck review") == 1


def test_render_empty_reads_is_empty_string():
    line, n = _render([], 10.0)
    assert line == "" and n == 0


def test_render_redacts_and_counts_through_the_pipeline():
    read = OcrRead(t_offset_s=0.0, regions=(
        _region(f"key is {SECRET_CASES['sk_key']} keep private"),
    ))
    line, n = _render([read], 60.0)
    assert n == 1 and redact.REDACTED in line and SECRET_CASES["sk_key"] not in line


def test_render_truncates_on_word_boundary_at_budget():
    # ocr_cap = round(6 * span). At span=6 -> 36 chars.
    read = OcrRead(t_offset_s=0.0, regions=(
        _region("alpha bravo charlie delta echo foxtrot golf hotel india"),
    ))
    line, _ = _render([read], 6.0)
    assert len(line) <= 36
    assert not line.endswith(" ")
    # never a mid-word cut: the truncated text ends on a whole token.
    assert line.split(" ")[-1] in "+0s main: alpha bravo charlie delta echo foxtrot golf hotel india".split(" ")


def test_render_keeps_model_supplied_roles_and_coerces_off_vocab_ones():
    # A valid supplied role is kept; an off-vocab role resolves from the bbox — a
    # degenerate zero bbox lands "main", never "titlebar" (the guard path).
    read = OcrRead(t_offset_s=0.0, regions=(
        _region("Reply to Sarah here", bbox=(0.0, 0.0, 0.0, 0.0), role="compose"),
        _region("Meeting notes for Q3 review", bbox=(0.0, 0.0, 0.0, 0.0), role="body"),
    ))
    line, _ = _render([read], 60.0)
    assert "compose: Reply to Sarah here" in line
    assert "main: Meeting notes for Q3 review" in line
    assert "titlebar:" not in line


# ============================ budget helpers (D-11) =============================

@pytest.mark.parametrize("span,expected", [(10.0, 60), (60.0, 360), (0.0, 0), (2.5, 15)])
def test_ocr_cap_at_the_pinned_rate(span, expected):
    assert ocr_cap(span, PINS["chars_per_second"]) == expected


def test_truncate_word_boundaries():
    assert truncate_word("hello world foobar", 100) == "hello world foobar"  # under budget
    assert truncate_word("hello world foobar", 8) == "hello"                 # back off to space
    assert truncate_word("anything", 0) == ""                                # no budget
    assert truncate_word("supercalifragilistic", 5) == "super"               # single long word: hard cut


def test_assemble_reexports_the_shared_budget_math():
    # The v0 local ocr_cap/truncate_word stub collapsed into app/vision/budget (its own
    # recorded follow-up L-2): one implementation, one truncation behavior fleet-wide.
    assert assemble.ocr_cap is ocr_cap
    assert assemble.truncate_word is truncate_word
