"""WS-H — the offline eval harness: the scorers, the arm fork, and the two safety guards.

The scorers in ``scripts/prompt_ab.py`` are pure functions that decide a ratified gate
(O-8), so they get the same regression treatment as any other decision-bearing code. What
is asserted here:

  * the **widened grounding scorer** (addendum edit #2) — all named ≥4-char strings, not
    only double-quoted spans, with the narrow-vs-wide gap demonstrated on a caption that a
    quote-only check passes and the widened one fails;
  * the **pre-registered O-8 rule**, at all four corners of its decision table, plus the two
    ways it must refuse to rule (mock captioner, unlabelled corpus);
  * the **deterministic OCR corruption** the propagation arm is built on;
  * **arm assembly** — an assembled arm dir is a loadable registry whose clip default is the
    arm's pack, and two arms whose pack text is identical still fork, because ``arm.json``
    lands in ``prompt_dir_fingerprint``;
  * the two safety properties WS-H is responsible for: the experimental packs are INERT to
    the production registry (they must not move ``PACK_DIGEST`` or appear in
    ``all_packs()``), and ``DP_OFFLINE_EVAL=1`` makes ``app/main.py`` refuse to serve.

Headless: no network, no GPU, no ffmpeg (the committed chunkset carries no blobs).
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = SERVICE_ROOT / "scripts"
SMOKE = SERVICE_ROOT / "tests" / "fixtures" / "chunksets" / "smoke-v1"


def _load(name: str):
    """Load a ``scripts/`` module by path — ``scripts/`` is deliberately not a package."""
    spec = importlib.util.spec_from_file_location(f"_wsh_{name}", SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


ab = _load("prompt_ab")
cs = _load("capture_chunkset")


# ============================================================ the widened grounding scorer

def test_named_strings_finds_unquoted_names():
    """The whole point of the widening: a caption that quotes NOTHING still names things."""
    caption = ("Visual Studio Code — editing executor.py. The person is typing in "
               "run_graph while node-7 logs scroll in a Terminal pane.")
    found = {s.casefold() for s in ab.named_strings(caption)}
    assert "visual studio code" in found      # a merged multi-token name, not three
    assert "executor.py" in found
    assert "node-7" in found
    assert "run_graph" in found


def test_named_strings_ignores_sentence_initial_ordinary_words():
    """A capitalised English word at the start of a sentence is capitalisation, not a name —
    the false-positive class that would make the widened counter unreadable."""
    caption = ("The person is reading a document. Two paragraphs are visible. This window "
               "has focus and the content is stable.")
    assert ab.named_strings(caption) == []


def test_named_strings_does_not_merge_across_punctuation():
    """"...in Gmail. Sarah replied" must not become the invented phrase "Gmail Sarah"."""
    found = {s.casefold() for s in ab.named_strings("Composing in Gmail. Sarah replied later.")}
    assert found == {"gmail", "sarah"}


def test_widened_scorer_catches_what_the_quote_only_check_misses():
    """The measured 32.6 % class, as an assertion: a caption whose only QUOTED span is
    grounded, but which also states an unquoted name that is nowhere in the OCR text. The
    narrow rate reads a clean 0.0; the widened rate does not."""
    ocr = '+0s titlebar: Inbox (12) - Gmail · +2s main: Re: Q3 deck review'
    caption = ('Gmail — composing a reply. The person is writing to Priya about the '
               '"Q3 deck review" thread in Gmail.')
    g = ab.grounding(caption, ocr)
    assert g["quoted"] == 1 and g["quoted_ungrounded"] == 0        # quote-only check: clean
    assert g["named_ungrounded"] >= 1                              # widened check: not clean
    assert any(s.casefold() == "priya" for s in g["ungrounded_examples"])


def test_is_grounded_is_lenient_and_phrase_aware():
    ocr = "+0s titlebar: executor.py - dataprocessing - Visual Studio Code"
    assert ab.is_grounded("Visual Studio Code", ocr)      # whole phrase
    assert ab.is_grounded("visual   studio code", ocr)    # case + whitespace folded
    assert ab.is_grounded("executor.py dataprocessing", ocr)  # per-token fallback
    assert not ab.is_grounded("Sublime Text", ocr)


def test_entity_recall_and_app_correct():
    # 2 of 3: "Q3" is in the denominator (a labeller's short entity is never dropped) and
    # is genuinely absent from this caption.
    assert ab.entity_recall("Gmail — composing a reply to Sarah.", ["Gmail", "Sarah", "Q3"]) == (2, 3)
    assert ab.entity_recall("Gmail — the Q3 deck thread.", ["Gmail", "Q3"]) == (2, 2)
    assert ab.entity_recall("", ["Gmail"]) == (0, 1)
    assert ab.app_correct("Gmail", "Gmail — Inbox")
    assert ab.app_correct("Visual Studio Code", "visual studio code")
    assert not ab.app_correct("unknown", "Gmail")
    assert not ab.app_correct("", "Gmail")


def test_change_verb_rate_vocabulary():
    assert ab.has_change_verb("The person is scrolling through a long article.")
    assert not ab.has_change_verb("A window is present and the content is stable.")


# ============================================================ deterministic OCR corruption

def test_corrupt_text_is_deterministic_and_reports_its_swaps():
    text = "+0s titlebar: executor.py - dataprocessing - Visual Studio Code"
    a, swaps_a = ab.corrupt_text(text, 0.30, salt="chunk-1")
    b, swaps_b = ab.corrupt_text(text, 0.30, salt="chunk-1")
    assert a == b and swaps_a == swaps_b            # same salt -> byte-identical
    assert a != text and swaps_a                    # something WAS falsified
    for swap in swaps_a:
        assert swap["false"] not in text            # the false string is genuinely new
        assert swap["false"] in a                   # and it is what the model will read
        assert len(swap["false"]) == len(swap["true"])   # same shape -> same plausibility


def test_corrupt_text_zero_fraction_is_a_noop():
    text = "+0s main: Re: Q3 deck review"
    assert ab.corrupt_text(text, 0.0, salt="x") == (text, [])


def test_corrupt_text_salt_changes_the_falsification():
    text = "+0s titlebar: executor.py - dataprocessing - Visual Studio Code"
    assert ab.corrupt_text(text, 0.5, "chunk-1")[0] != ab.corrupt_text(text, 0.5, "chunk-2")[0]


# ============================================================ the pre-registered O-8 rule

def _arm(recall=None, propagation=None):
    return {"named_entity_recall": recall, "propagation_rate": propagation}


@pytest.mark.parametrize("lift_recall,prop,expected", [
    (0.60, 0.05, "SHIP A (injection)"),     # lift 0.60 > 0.25, propagation 0.05 < 0.10
    (0.20, 0.05, "SHIP D (screen-clip-hint-v1)"),   # lift fails
    (0.60, 0.40, "SHIP D (screen-clip-hint-v1)"),   # propagation fails
    (0.20, 0.40, "SHIP D (screen-clip-hint-v1)"),   # both fail
])
def test_o8_decision_table(lift_recall, prop, expected):
    scores = {"injected": _arm(lift_recall), "blind": _arm(0.0), "hint": _arm(0.3),
              "injected-corrupt": _arm(lift_recall, prop)}
    assert ab.o8_verdict(scores, backend="vlm")["verdict"] == expected


def test_o8_threshold_is_strict_at_the_boundary():
    """`> 0.25`, not `>=` — a gate that ties must not ship the thing it was gating."""
    scores = {"injected": _arm(0.25), "blind": _arm(0.0),
              "injected-corrupt": _arm(0.25, 0.0)}
    assert ab.o8_verdict(scores, backend="vlm")["verdict"] == "SHIP D (screen-clip-hint-v1)"


def test_o8_refuses_to_rule_under_the_mock_captioner():
    """A mock captioner reads no prompt, so every arm is identical by construction and the
    rule would 'decide' from a corpus that cannot answer. It must return UNDECIDED."""
    scores = {"injected": _arm(0.0), "blind": _arm(0.0), "injected-corrupt": _arm(0.0, 0.0)}
    assert ab.o8_verdict(scores, backend="mock")["verdict"] == "UNDECIDED"


def test_o8_refuses_to_rule_without_labels_or_without_the_corrupted_arm():
    unlabelled = {"injected": _arm(None), "blind": _arm(None)}
    assert ab.o8_verdict(unlabelled, backend="vlm")["verdict"] == "UNDECIDED"
    no_corrupt = {"injected": _arm(0.9), "blind": _arm(0.0)}
    v = ab.o8_verdict(no_corrupt, backend="vlm")
    assert v["verdict"] == "UNDECIDED" and "propagation" in v["why"]


# ============================================================ arm assembly + the fork

def test_assembled_arm_dir_is_a_loadable_registry_defaulting_to_the_arm_pack(tmp_path):
    from app.vision.prompts import load_registry

    arm = ab.ARMS["blind"]
    d = ab.assemble_arm_dir(arm, tmp_path / "blind")
    packs, routes, _schemas = load_registry(d)
    assert "screen-clip-blind-v1" in packs
    assert packs["screen-clip-blind-v1"].role == "clip"
    assert routes["family_defaults"]["clip"] == "screen-clip-blind-v1"
    # the blind pack must not reference the OCR block — that IS the arm.
    assert "[[ocr_block]]" not in packs["screen-clip-blind-v1"].user
    assert "[[ocr_block]]" in packs["screen-clip-v1"].user       # the control still does


def test_the_hint_arm_pack_still_receives_the_ocr_block(tmp_path):
    from app.vision.prompts import load_registry

    d = ab.assemble_arm_dir(ab.ARMS["hint"], tmp_path / "hint")
    packs, _routes, _ = load_registry(d)
    assert "[[ocr_block]]" in packs["screen-clip-hint-v1"].user


def test_arms_with_identical_pack_text_still_fork(tmp_path):
    """``injected`` and ``injected-corrupt`` render the SAME pack. Without ``arm.json`` they
    would share a ``pipeline_version`` and their records would collide on one id."""
    from app.vision.config import prompt_dir_fingerprint

    a = ab.assemble_arm_dir(ab.ARMS["injected"], tmp_path / "a")
    b = ab.assemble_arm_dir(ab.ARMS["injected-corrupt"], tmp_path / "b")
    assert {p.name for p in a.glob("*.prompt.md")} == {p.name for p in b.glob("*.prompt.md")}
    assert prompt_dir_fingerprint(str(a)) != prompt_dir_fingerprint(str(b))


def test_prompt_dir_fingerprint_is_path_independent(tmp_path):
    """The arm's dialect must be reproducible on another machine, so the fingerprint must
    depend on the dir's CONTENTS and not on the temp path it happens to live at."""
    from app.vision.config import prompt_dir_fingerprint

    a = ab.assemble_arm_dir(ab.ARMS["blind"], tmp_path / "one")
    b = ab.assemble_arm_dir(ab.ARMS["blind"], tmp_path / "two")
    assert prompt_dir_fingerprint(str(a)) == prompt_dir_fingerprint(str(b))


def test_every_arm_assembles(tmp_path):
    for name, arm in ab.ARMS.items():
        if arm.pipeline != "clip":
            continue
        ab.assemble_arm_dir(arm, tmp_path / name)


# ============================================================ the experimental packs are INERT

def test_experimental_packs_exist_but_are_not_in_the_production_registry():
    """The load-bearing safety property of WS-H's pack placement.

    ``PACK_DIGEST`` is a digest over EVERY loaded pack, so an experimental pack dropped into
    the flat registry dir would fork ``pipeline_version`` for every production caption — for
    an experiment that never ran. ``load_registry``'s glob is non-recursive, so the
    subdirectory is invisible to it. If someone later 'fixes' the path to match §11's
    literal wording, this test is what tells them what it costs."""
    from app.vision import prompts as P

    experimental = Path(P.__file__).resolve().parent / "experimental"
    assert (experimental / "screen-clip-blind-v1.prompt.md").is_file()
    assert (experimental / "screen-clip-hint-v1.prompt.md").is_file()

    assert set(P.all_packs()) == {
        "screen-clip-v1", "screen-clip-idle-v1", "screen-clip-single-v1",
        "screen-ocr-v1", "camera-clip-v1", "per-frame-v0",
    }
    for pack_id in ("screen-clip-blind-v1", "screen-clip-hint-v1"):
        with pytest.raises(KeyError):
            P.get(pack_id)

    # and the packaged digest is exactly the digest of the packaged FLAT dir.
    packs, routes, _ = P.load_registry(Path(P.__file__).resolve().parent)
    assert P.compute_digest(packs, routes) == P.PACK_DIGEST
    assert json.loads((Path(P.__file__).resolve().parent / "LOCK.json").read_text())[
        "pack_digest"] == P.PACK_DIGEST


def test_experimental_packs_parse_and_use_only_available_placeholders():
    """``prompts.render`` RAISES on a placeholder with no context key, and the captioner
    supplies a fixed set — a typo'd placeholder would 500 every chunk of that arm."""
    from app.vision.prompts import parse_prompt, render
    from app.vision import prompts as P

    packaged = Path(P.__file__).resolve().parent
    schemas = json.loads((packaged / "schemas.json").read_text("utf-8"))
    context = {"span_s": "10", "scenario_label": "a macOS desktop", "n": 4,
               "offsets": "0.0, 2.0", "ocr_block": "(none)", "words_lo": 21, "words_hi": 27}
    for name in ("screen-clip-blind-v1", "screen-clip-hint-v1"):
        spec = parse_prompt((packaged / "experimental" / f"{name}.prompt.md").read_text("utf-8"),
                            schemas)
        assert spec.id == name and spec.role == "clip" and spec.schema_name == "clip-json-v1"
        rendered = render(spec, **context)
        assert "[[" not in rendered.system and "[[" not in rendered.user
        # the marker vlm._build_messages splits on, so the images land before the task.
        assert "Reply with ONE JSON" in spec.user


# ============================================================ the DP_OFFLINE_EVAL boot guard

def test_offline_eval_flag_makes_the_serving_app_refuse_to_boot(monkeypatch):
    """The flag that unlocks pack overrides is the flag that prevents serving (§11 WS-H)."""
    from app.main import create_app

    monkeypatch.setenv("DP_OFFLINE_EVAL", "1")
    with pytest.raises(RuntimeError, match="DP_OFFLINE_EVAL"):
        create_app()


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off"])
def test_falsey_offline_eval_values_still_serve(monkeypatch, value):
    from app.main import create_app

    monkeypatch.setenv("DP_OFFLINE_EVAL", value)
    assert create_app() is not None


# ============================================================ the committed chunkset

def test_smoke_chunkset_loads_and_carries_no_binaries():
    manifest = cs.load_chunkset(SMOKE)
    assert manifest["mode"] == "headless"
    assert manifest["count"] == len(manifest["chunks"]) == 12
    assert manifest["has_blobs"] is False
    assert not list(SMOKE.rglob("*.mp4"))          # house rule 5: commit no binaries
    for entry in manifest["chunks"]:
        assert entry["blob"] is None
        truth = entry["truth"]
        assert truth["app"] and truth["entities"] and truth["ocr_regions"]
        c1 = entry["c1_obj"]
        assert c1["contract"] == "C1" and c1["modality"] == "video"
        assert c1["t_start"].endswith("Z") and c1["t_end"].endswith("Z")


def test_smoke_chunkset_c1_envelopes_validate():
    from app import schemas

    for entry in cs.load_chunkset(SMOKE)["chunks"]:
        assert schemas.validate_c1(entry["c1_obj"]) == []


def test_synthetic_truth_matches_what_the_filter_chain_draws():
    """The label cannot drift from the pixels: both come from the same ``Screen``."""
    for screen in cs.SCREENS:
        truth = cs.truth_for(screen, with_secret=False)
        chain = cs.synth_filters(screen, 10.0, with_secret=False)
        assert truth["app"] == screen.app
        drawn = {r["text"] for r in truth["ocr_regions"]}
        assert screen.title in drawn and screen.subject in drawn
        assert cs._esc(screen.title) in chain


# ============================================================ the gates

def test_run_checks_flags_a_dialect_collision_and_an_over_budget_block():
    good = {"daylog": {"available": True, "blocks_over_excerpt": 0},
            "errors": 0, "record_id_recomputes": True, "pipeline_version": "pv-a",
            "ungrounded_named_rate": 0.1}
    clash = {**good, "pipeline_version": "pv-a"}
    by_name = {n: c["ok"] for c in ab.run_checks({"a": good, "b": clash}, max_ungrounded=None)
               for n in [c["name"]]}
    assert by_name["arms fork (no dialect collision)"] is False

    over = {**good, "pipeline_version": "pv-b",
            "daylog": {"available": True, "blocks_over_excerpt": 3}}
    by_name = {c["name"]: c["ok"] for c in ab.run_checks({"a": good, "b": over},
                                                         max_ungrounded=None)}
    assert by_name["day-log blocks within EXCERPT_CHARS"] is False
    assert by_name["no chunk errors"] is True


def test_run_checks_optional_ungrounded_bound():
    arm = {"daylog": {"available": True, "blocks_over_excerpt": 0}, "errors": 0,
           "record_id_recomputes": True, "pipeline_version": "pv", "ungrounded_named_rate": 0.4}
    checks = {c["name"]: c["ok"] for c in ab.run_checks({"a": arm}, max_ungrounded=0.2)}
    assert checks["ungrounded named rate <= 0.2"] is False


# ============================================================ the oracle / blind judge

og = _load("oracle_gemini")


def test_no_credential_is_a_clean_honest_exit(monkeypatch):
    for var in ("VERTEX_API_KEY", "GEMINI_API_KEY", "VERTEX_ACCESS_TOKEN", "VERTEX_PROJECT"):
        monkeypatch.delenv(var, raising=False)
    assert og.resolve_creds() is None
    with pytest.raises(SystemExit) as exc:
        og._require_creds()
    assert exc.value.code == 4


def test_credentials_resolve_to_the_right_endpoint(monkeypatch):
    monkeypatch.setenv("VERTEX_API_KEY", "k")
    creds = og.resolve_creds()
    assert creds.mode == "apikey" and "generativelanguage.googleapis.com" in creds.url("m")
    assert "x-goog-api-key" in creds.headers()

    monkeypatch.delenv("VERTEX_API_KEY")
    monkeypatch.setenv("VERTEX_ACCESS_TOKEN", "t")
    monkeypatch.setenv("VERTEX_PROJECT", "p")
    monkeypatch.setenv("VERTEX_LOCATION", "europe-west4")
    creds = og.resolve_creds()
    assert creds.mode == "vertex"
    assert creds.url("m").startswith("https://europe-west4-aiplatform.googleapis.com/v1/projects/p/")
    assert creds.headers()["Authorization"] == "Bearer t"


def test_call_gemini_payload_shape_and_tolerant_parse(monkeypatch):
    """The wire shape, offline: system_instruction + inline_data image parts + JSON response
    mode, and a fenced reply still parses (a frontier model fences often enough that a
    strict loads() would silently drop good rows)."""
    import httpx

    captured: dict = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"candidates": [{"content": {"parts": [{"text":
                    '```json\n{"winner": "a", "why": "ok"}\n```'}]}}],
                    "usageMetadata": {"promptTokenCount": 1200, "candidatesTokenCount": 90}}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update(url=url, payload=json)
        return _Resp()

    monkeypatch.setattr(httpx, "post", fake_post)
    out, usage = og.call_gemini(og.Creds("apikey", "k"), "gemini-2.5-pro", og.JUDGE_SYSTEM,
                                [og._part_image(b"\xff\xd8jpeg"), {"text": "A vs B"}])
    assert out == {"winner": "a", "why": "ok"}
    assert usage == {"input": 1200, "output": 90}
    payload = captured["payload"]
    assert payload["system_instruction"]["parts"][0]["text"] == og.JUDGE_SYSTEM
    assert payload["generationConfig"] == {"temperature": 0, "responseMimeType": "application/json"}
    assert "inline_data" in payload["contents"][0]["parts"][0]


def test_judge_blinding_is_deterministic_and_balanced():
    """Presentation order must be reproducible (no RNG) AND uncorrelated with the arms —
    an un-blinded LLM judge is position-biased enough to be worthless."""
    assert (og.presentation_order("c1", "injected", "blind")
            == og.presentation_order("c1", "injected", "blind"))
    first = [og.presentation_order(f"c{i}", "injected", "blind")[0] for i in range(200)]
    assert 70 <= first.count("injected") <= 130     # not a constant, not degenerate


# ============================================================ end to end (subprocess)

def test_harness_refuses_to_run_without_the_offline_eval_flag():
    env = {**os.environ, "DP_OFFLINE_EVAL": "0"}
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "prompt_ab.py"), "--chunkset", str(SMOKE)],
        env=env, capture_output=True, text=True, cwd=str(SERVICE_ROOT))
    assert proc.returncode == 2
    assert "DP_OFFLINE_EVAL" in proc.stderr


@pytest.mark.parametrize("arms", ["injected,blind"])
def test_two_arms_run_end_to_end_and_fork(arms, tmp_path):
    """The integration proof, through the REAL graph: two arms over the committed chunkset
    produce the design's two-record set, distinct dialects, and recomputable record_ids."""
    env = {**os.environ, "DP_OFFLINE_EVAL": "1", "VIDEO_BACKEND": "mock",
           "VIDEO_OCR_BACKEND": "mock", "ASR_BACKEND": "mock"}
    report = tmp_path / "report.json"
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "prompt_ab.py"), "--chunkset", str(SMOKE),
         "--arms", arms, "--limit", "3", "--check", "--json", str(report)],
        env=env, capture_output=True, text=True, cwd=str(SERVICE_ROOT))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(report.read_text())

    assert set(data["arms"]) == {"injected", "blind"}
    versions = {name: s["pipeline_version"] for name, s in data["arms"].items()}
    assert len(set(versions.values())) == 2, versions
    for name, s in data["arms"].items():
        assert s["errors"] == 0
        assert s["records_per_chunk"] == 2.0        # D-05: caption + ocr, always
        assert s["record_id_recomputes"] is True
        assert len(s["record_ids"]) == 2 and len(set(s["record_ids"])) == 2
    assert all(c["ok"] for c in data["checks"])
    assert data["arms"]["injected"]["daylog"]["available"] is True
    # a mock-backend run must never be allowed to look like a quality measurement.
    assert any("VIDEO_BACKEND=mock" in a for a in data["advisories"])
