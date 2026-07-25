#!/usr/bin/env python3
# =============================================================================
# test_score.py -- unit tests for the O-2 scorer (lenient recall + focus CER).
#   Run:  python3 -m pytest sidecars/ocr/bakeoff/test_score.py -q
# =============================================================================
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import score as S  # noqa: E402


def test_norm_folds_case_whitespace_and_fullwidth():
    assert S.norm_tight("Build_C2（x, y）") == S.norm_tight("build_c2(x,y)")
    assert S.norm_tight("A  B\tC\n") == "abc"


def test_substring_recall_is_lenient_not_exact():
    # exact equality would fail on the space drop + full-width paren; lenient passes
    ocr = "class Screentextstage(Stage): return build_c2（chunk_id,pipeline_version)"
    keys = ["class ScreentextStage(Stage):", "return build_c2(chunk_id, pipeline_version)"]
    r = S.score_recall(keys, ocr)
    assert r["substring"] == 1.0
    assert r["fuzzy"] == 1.0


def test_fuzzy_catches_single_substitution_substring_misses():
    # one substituted char breaks substring but not fuzzy>=0.85
    key = ["invoice 48201 overdue"]           # 19 non-space chars
    ocr = "the invoice 482O1 overdue notice"   # '0' -> 'O'
    r = S.score_recall(key, ocr)
    assert r["substring"] == 0.0
    assert r["fuzzy"] == 1.0


def test_true_miss_counts_as_miss():
    r = S.score_recall(["totally absent string"], "unrelated text here")
    assert r["substring"] == 0.0 and r["fuzzy"] == 0.0
    assert r["misses"] == ["totally absent string"]


def test_short_keys_are_dropped():
    r = S.score_recall(["abcd", "abcde"], "xyz")  # only the >=5-char key counts
    assert r["n_keys"] == 1


def test_cer_zero_on_perfect_focus():
    regions = [{"text": "hello world", "bbox": [10, 10, 200, 40]}]
    cer = S.score_cer("hello world", regions, [0, 0, 1000, 1000])
    assert cer == 0.0


def test_cer_ignores_regions_outside_focus_box():
    regions = [
        {"text": "focus body text", "bbox": [10, 10, 200, 40]},
        {"text": "CHROME NAVBAR JUNK", "bbox": [5000, 5000, 5200, 5040]},  # outside
    ]
    cer = S.score_cer("focus body text", regions, [0, 0, 1000, 1000])
    assert cer == 0.0


def test_levenshtein_basic():
    assert S.levenshtein("kitten", "sitting") == 3
    assert S.levenshtein("", "abc") == 3
    assert S.levenshtein("abc", "abc") == 0
