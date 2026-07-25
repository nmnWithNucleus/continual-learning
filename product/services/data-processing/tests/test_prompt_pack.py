"""WS-D — the prompt pack, cfg_tag and the OUTPUT_AFFECTING classification (D-13).

Every WS-D "pack half" exit criterion is asserted here:
  * PACK_DIGEST stable across a whitespace-only edit, changes on any semantic edit;
  * unknown VIDEO_CLIP_PROMPT resolves to the pinned default in select() AND version_tag();
  * a prompt edit does NOT re-key the headless (mock) corpus;
  * OUTPUT_AFFECTING | OPERATIONAL_ONLY == VisionSettings fields (fails until classified);
  * one byte of a .prompt.md forks pipeline_version → record_id end to end;
  * `python -m app.vision.prompts show` prints the exact wire text; relock bumps + archives.
"""
from __future__ import annotations

import dataclasses
import json
import shutil
from pathlib import Path

import pytest

from app.pipeline import compute_record_id
from app.vision import prompts as P
from app.vision.prompts import (
    PACK_DIGEST,
    PACK_VERSION,
    PROMPT_SOURCE,
    PromptPackError,
    all_packs,
    compute_digest,
    get,
    load_registry,
    normalise,
    parse_prompt,
    render,
    select,
    version_tag,
)
from app.vision.config import VisionSettings, get_vision_settings, prompt_dir_fingerprint
from app.vision.version import (
    OPERATIONAL_ONLY,
    OUTPUT_AFFECTING,
    assert_fields_classified,
    cfg_tag,
    unclassified_fields,
)

PACKAGED = Path(P.__file__).resolve().parent
_ALL = {
    "screen-clip-v1", "screen-clip-idle-v1", "screen-clip-single-v1",
    "screen-ocr-v1", "camera-clip-v1", "per-frame-v0",
}


def _copy_pack(dst: Path, *, with_lock: bool = True) -> Path:
    dst.mkdir(parents=True, exist_ok=True)
    for f in PACKAGED.glob("*.prompt.md"):
        shutil.copy(f, dst)
    names = ["routes.json", "schemas.json"] + (["LOCK.json"] if with_lock else [])
    for name in names:
        shutil.copy(PACKAGED / name, dst)
    return dst


def _digest(directory: Path) -> str:
    packs, routes, _ = load_registry(directory)
    return compute_digest(packs, routes)


def _copy_subset(dst: Path, pack_names, *, routes=None) -> Path:
    dst.mkdir(parents=True, exist_ok=True)
    for name in pack_names:
        shutil.copy(PACKAGED / f"{name}.prompt.md", dst)
    shutil.copy(PACKAGED / "schemas.json", dst)
    if routes is None:
        shutil.copy(PACKAGED / "routes.json", dst)
    else:
        (dst / "routes.json").write_text(json.dumps(routes))
    return dst


def _bump(value):
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    if isinstance(value, float):
        return value + 1.0
    return str(value) + "-x"


@pytest.fixture()
def vs():
    return get_vision_settings()


# ============================================================ classification (D-13)
def test_output_affecting_partitions_every_field():
    assert set(OUTPUT_AFFECTING) | set(OPERATIONAL_ONLY) == set(VisionSettings.__dataclass_fields__)
    assert set(OUTPUT_AFFECTING).isdisjoint(OPERATIONAL_ONLY)
    assert_fields_classified()
    missing, dup, unknown = unclassified_fields()
    assert not missing and not dup and not unknown


def test_an_unclassified_field_fails_the_completeness_check(monkeypatch):
    """The mechanism that FAILS the build until a newly added knob is classified: drop a
    field from the allowlist and the assertion must fire (D-13)."""
    import app.vision.version as V

    monkeypatch.setattr(V, "OUTPUT_AFFECTING", V.OUTPUT_AFFECTING[1:])  # forget 'pipeline'
    with pytest.raises(AssertionError):
        V.assert_fields_classified()


# ============================================================ cfg_tag
def test_cfg_tag_shape_and_determinism(vs):
    tag = cfg_tag(vs)
    assert tag.startswith("#") and len(tag) == 9
    assert cfg_tag(get_vision_settings()) == cfg_tag(get_vision_settings())


def test_cfg_tag_forks_on_every_output_affecting_knob(vs):
    base = cfg_tag(vs)
    # prompt_dir_fingerprint is derived once at import ("" here); every OTHER
    # output-affecting knob, when nudged, must fork the clip dialect.
    for name in OUTPUT_AFFECTING:
        if name == "prompt_dir_fingerprint":
            continue
        changed = dataclasses.replace(vs, **{name: _bump(getattr(vs, name))})
        assert cfg_tag(changed) != base, f"{name} should fork cfg_tag"


def test_cfg_tag_stable_on_operational_and_legacy_knobs(vs):
    base = cfg_tag(vs)
    for name in OPERATIONAL_ONLY:
        changed = dataclasses.replace(vs, **{name: _bump(getattr(vs, name))})
        assert cfg_tag(changed) == base, f"{name} must NOT fork cfg_tag"


# ============================================================ digest stability / fork
def test_digest_stable_across_crlf(tmp_path):
    d = _copy_pack(tmp_path / "p")
    base = _digest(d)
    for f in d.glob("*.prompt.md"):
        f.write_bytes(f.read_text().replace("\n", "\r\n").encode("utf-8"))
    assert _digest(d) == base


def test_digest_stable_across_trailing_newline(tmp_path):
    d = _copy_pack(tmp_path / "p")
    base = _digest(d)
    f = d / "screen-clip-v1.prompt.md"
    f.write_text(f.read_text() + "\n\n\n")
    assert _digest(d) == base


def test_digest_stable_across_trailing_spaces(tmp_path):
    d = _copy_pack(tmp_path / "p")
    base = _digest(d)
    f = d / "screen-clip-v1.prompt.md"
    f.write_text(f.read_text().replace("Never guess a brand.", "Never guess a brand.   "))
    assert _digest(d) == base


def test_digest_changes_on_system_text_edit(tmp_path):
    d = _copy_pack(tmp_path / "p")
    base = _digest(d)
    f = d / "screen-clip-v1.prompt.md"
    f.write_text(f.read_text().replace("Report what the PERSON", "Report what the USER"))
    assert _digest(d) != base


def test_digest_changes_on_decode_param_edit(tmp_path):
    d = _copy_pack(tmp_path / "p")
    base = _digest(d)
    f = d / "screen-clip-v1.prompt.md"
    f.write_text(f.read_text().replace("max_tokens: 512", "max_tokens: 400"))
    assert _digest(d) != base


def test_digest_changes_on_schema_edit(tmp_path):
    d = _copy_pack(tmp_path / "p")
    base = _digest(d)
    schemas = json.loads((d / "schemas.json").read_text())
    schemas["clip-json-v1"]["properties"]["extra"] = {"type": "string"}
    (d / "schemas.json").write_text(json.dumps(schemas))
    assert _digest(d) != base


def test_digest_changes_on_route_or_label_edit(tmp_path):
    d = _copy_pack(tmp_path / "p")
    base = _digest(d)
    routes = json.loads((d / "routes.json").read_text())
    routes["scenario_labels"]["screen-mac"] = "a Mac desktop"
    (d / "routes.json").write_text(json.dumps(routes))
    assert _digest(d) != base


def test_module_digest_matches_pure_recompute():
    assert PACK_DIGEST == compute_digest(all_packs(), P.routes())
    assert _ALL == set(all_packs())


def test_normalise_ignores_line_endings_and_trailing_ws():
    schemas = json.loads((PACKAGED / "schemas.json").read_text())
    txt = (PACKAGED / "screen-clip-v1.prompt.md").read_text()
    a = parse_prompt(txt, schemas)
    b = parse_prompt(txt.replace("\n", "\r\n"), schemas)
    assert normalise(a) == normalise(b)


# ============================================================ resolution (unknown->default)
def test_unknown_clip_prompt_resolves_to_default_in_both(monkeypatch, vs):
    monkeypatch.setenv("VIDEO_CLIP_PROMPT", "does-not-exist")
    assert select(vs).id == P.family_default("clip") == "screen-clip-v1"
    assert version_tag(vs) == f"@screen-clip-v1.p{PACK_VERSION}.{PACK_DIGEST}"
    assert "does-not-exist" not in version_tag(vs)


def test_explicit_clip_prompt_override_selects_that_pack(monkeypatch, vs):
    monkeypatch.setenv("VIDEO_CLIP_PROMPT", "screen-clip-single-v1")
    assert select(vs).id == "screen-clip-single-v1"
    assert version_tag(vs) == f"@screen-clip-single-v1.p{PACK_VERSION}.{PACK_DIGEST}"


def test_scenario_routes_to_pack(vs):
    assert select(dataclasses.replace(vs, scenario="camera")).id == "camera-clip-v1"
    assert select(dataclasses.replace(vs, scenario="screen-browser")).id == "screen-clip-v1"


def test_unknown_scenario_falls_to_family_default(vs):
    assert select(dataclasses.replace(vs, scenario="mars")).id == "screen-clip-v1"


def test_get_unknown_id_raises():
    with pytest.raises(KeyError):
        get("no-such-pack")


def test_routes_referencing_absent_pack_fails_loud_at_load(tmp_path):
    """A VIDEO_PROMPT_DIR override that drops the pack routes.json still references must
    FAIL LOUD at import (the module's own 'a bad drop-in fails loud' promise), never as a
    later select()/version_tag() divergence that crashes a worker mid-run."""
    d = _copy_subset(tmp_path / "p", ["camera-clip-v1", "screen-ocr-v1"])  # packaged routes -> screen-clip-v1
    with pytest.raises(PromptPackError):
        load_registry(d)


def test_coherent_override_without_the_default_loads(tmp_path):
    routes = {
        "scenarios": {"camera": "camera-clip-v1"},
        "family_defaults": {"clip": "camera-clip-v1", "ocr": "screen-ocr-v1"},
        "scenario_labels": {"camera": "a camera view"},
    }
    d = _copy_subset(tmp_path / "p", ["camera-clip-v1", "screen-ocr-v1"], routes=routes)
    packs, r, _ = load_registry(d)  # no raise — routes only references loaded packs
    assert set(packs) == {"camera-clip-v1", "screen-ocr-v1"}


def test_family_default_never_returns_an_absent_pack(monkeypatch):
    monkeypatch.setattr(P, "_ROUTES", {**P.routes(), "family_defaults": {"clip": "ghost-pack"}})
    fd = P.family_default("clip")
    assert fd in P.all_packs() and P.all_packs()[fd].role == "clip"


def test_select_and_version_tag_never_diverge_on_a_bad_default(monkeypatch, vs):
    """select() (renders at RUN) and version_tag() (stamps at ACCEPT) must always agree on a
    real, loadable pack — even if routes.json's default is broken, both fall through to the
    same loaded clip pack, so ACCEPT can't stamp a dialect RUN cannot render."""
    monkeypatch.setattr(P, "_ROUTES", {"scenarios": {}, "family_defaults": {"clip": "ghost"}})
    sel = select(vs).id
    assert sel in P.all_packs()
    assert version_tag(vs) == f"@{sel}.p{PACK_VERSION}.{PACK_DIGEST}"


def test_parse_non_numeric_decode_param_raises_typed():
    bad = "---\nid: x\nrole: clip\nmax_tokens: lots\n---\n[system]\na\n\n[user]\nb\n"
    with pytest.raises(PromptPackError):
        parse_prompt(bad, {})


def test_non_clip_override_is_rejected_to_the_default(monkeypatch, vs):
    """A VIDEO_CLIP_PROMPT naming a KNOWN but non-clip pack (an ocr pack, the legacy
    fingerprint record) must not be rendered by the clip primary — it falls to the clip
    default, so a misconfig can't hand the captioner an OCR template."""
    for wrong in ("screen-ocr-v1", "per-frame-v0"):
        monkeypatch.setenv("VIDEO_CLIP_PROMPT", wrong)
        assert select(vs).id == "screen-clip-v1"
        assert version_tag(vs) == f"@screen-clip-v1.p{PACK_VERSION}.{PACK_DIGEST}"


# ============================================================ mock corpus not re-keyed
def test_prompt_edit_does_not_change_the_mock_dialect(tmp_path, vs):
    """The mock backend's prompt_tag is "" (D2's clipcap contract), so the mock dialect is
    ``base + "" + cfg_tag(vs)`` and cfg_tag never folds PACK_DIGEST. A packaged prompt edit
    changes the pack digest but MUST NOT re-key the headless corpus."""
    d = _copy_pack(tmp_path / "p")
    dg0 = _digest(d)
    mock0 = "vidclip-mock-v1" + "" + cfg_tag(vs)
    f = d / "screen-clip-v1.prompt.md"
    f.write_text(f.read_text().replace("Report what the PERSON", "Report DIFFERENTLY here"))
    dg1 = _digest(d)
    mock1 = "vidclip-mock-v1" + "" + cfg_tag(vs)
    assert dg1 != dg0        # the pack digest DID change
    assert mock1 == mock0    # the mock dialect did NOT
    assert PACK_DIGEST not in cfg_tag(vs)


def test_prompt_dir_fingerprint_empty_in_packaged_mode(vs):
    assert vs.prompt_dir_fingerprint == ""
    assert PROMPT_SOURCE == "packaged"


# ============================================================ one byte -> record_id (E2E)
def test_one_byte_prompt_edit_forks_record_id(tmp_path, vs):
    d = _copy_pack(tmp_path / "p")

    def dialect(directory: Path) -> str:
        packs, routes, _ = load_registry(directory)
        digest = compute_digest(packs, routes)
        prompt_tag = f"@screen-clip-v1.p{PACK_VERSION}.{digest}"     # vlm backend prompt_tag
        return "vidclip-vlm-v1" + prompt_tag + cfg_tag(vs)           # backend.PIPELINE_VERSION stand-in

    pv0 = dialect(d)
    rid0 = compute_record_id("chunk-1", pv0, "")
    f = d / "screen-clip-v1.prompt.md"
    f.write_text(f.read_text().replace("never describe them one at a time.",
                                       "never describe them one at a time, ever."))
    pv1 = dialect(d)
    rid1 = compute_record_id("chunk-1", pv1, "")
    assert pv1 != pv0
    assert rid1 != rid0


# ============================================================ render
def test_render_fills_placeholders_and_leaves_no_markers():
    r = render(get("screen-clip-v1"), span_s="60", scenario_label="a macOS desktop",
               n=12, offsets="0.0, 5.0", ocr_block="+0.0s [titlebar] Inbox",
               words_lo=128, words_hi=160)
    assert "60 seconds of a macOS desktop" in r.user
    assert "128-160 words" in r.user
    assert '{"app":' in r.user          # literal JSON braces preserved
    assert "[[" not in r.system and "[[" not in r.user


def test_render_missing_context_raises():
    with pytest.raises(KeyError):
        render(get("screen-clip-v1"), span_s="60")  # missing scenario_label / n / ...


def test_render_ignores_extra_context_and_packs_without_placeholders():
    r = render(get("per-frame-v0"), unused="ignored")
    assert "[[" not in r.system and "[[" not in r.user


# ============================================================ parse
def test_parse_round_trips_a_clip_pack():
    schemas = json.loads((PACKAGED / "schemas.json").read_text())
    spec = parse_prompt((PACKAGED / "screen-clip-v1.prompt.md").read_text(), schemas)
    assert spec.id == "screen-clip-v1" and spec.role == "clip"
    assert spec.max_tokens == 512 and spec.temperature == 0.0
    assert spec.schema_name == "clip-json-v1" and spec.schema["type"] == "object"
    assert "[[span_s]]" in spec.user           # the TEMPLATE keeps its placeholders


def test_parse_schema_none_pack():
    spec = parse_prompt((PACKAGED / "per-frame-v0.prompt.md").read_text(), {})
    assert spec.schema_name == "" and spec.schema == {}


def test_parse_unknown_schema_raises():
    with pytest.raises(PromptPackError):
        parse_prompt("---\nid: x\nrole: clip\nschema: nope\n---\n[system]\na\n\n[user]\nb\n", {})


def test_parse_missing_user_section_raises():
    with pytest.raises(PromptPackError):
        parse_prompt("---\nid: x\nrole: clip\n---\n[system]\nonly system\n", {})


# ============================================================ CLI: show + relock
def test_show_prints_exact_wire_text(capsys):
    from app.vision.prompts import show

    rc = show.main(["--pack", "screen-clip-v1", "--span", "60", "--frames", "12"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "You are a screen-recording annotator" in out
    assert "60 seconds of a macOS desktop" in out
    assert "128-160 words" in out
    assert "[[" not in out                       # every placeholder is filled
    assert f"@screen-clip-v1.p{PACK_VERSION}.{PACK_DIGEST}" in out


def test_show_ocr_pack(capsys):
    from app.vision.prompts import show

    assert show.main(["--pack", "screen-ocr-v1"]) == 0
    out = capsys.readouterr().out
    assert "You transcribe text that is visible" in out
    assert "[[" not in out


def test_show_scenario_flag_renders_that_deployments_label(capsys):
    from app.vision.prompts import show

    assert show.main(["--pack", "screen-clip-v1", "--scenario", "screen-browser", "--span", "30"]) == 0
    out = capsys.readouterr().out
    assert "a web browser" in out
    assert "a macOS desktop" not in out


def test_relock_bumps_version_and_archives(tmp_path, capsys):
    from app.vision.prompts import relock

    d = _copy_pack(tmp_path / "p")            # carries LOCK.json at v1
    assert relock.main([str(d)]) == 0
    out = capsys.readouterr().out
    lock = json.loads((d / "LOCK.json").read_text())
    assert lock["pack_version"] == "2"
    assert lock["pack_digest"] == _digest(d)
    assert (d / "archive" / "p2.json").exists()
    assert "before:" in out and "after :" in out


def test_relock_initialises_at_v1_without_lock(tmp_path):
    from app.vision.prompts import relock

    d = _copy_pack(tmp_path / "p", with_lock=False)
    assert relock.main([str(d)]) == 0
    lock = json.loads((d / "LOCK.json").read_text())
    assert lock["pack_version"] == "1"
    assert (d / "archive" / "p1.json").exists()


def test_archived_pack_preserves_full_text_and_routes(tmp_path):
    from app.vision.prompts import relock

    d = _copy_pack(tmp_path / "p", with_lock=False)
    relock.main([str(d)])
    archived = json.loads((d / "archive" / "p1.json").read_text())
    sys_text = archived["packs"]["screen-clip-v1"]["system"]
    assert "You are a screen-recording annotator" in sys_text
    # routes.json is a digest input, so the archive carries it too -> the historical
    # dialect + wire text are reconstructable from p1.json alone.
    assert archived["routes"]["family_defaults"]["clip"] == "screen-clip-v1"


# ============================================================ load discipline
def test_load_registry_from_dir_matches_packaged(tmp_path):
    d = _copy_pack(tmp_path / "p")
    packs, routes, _ = load_registry(d)
    assert set(packs) == _ALL
    assert compute_digest(packs, routes) == PACK_DIGEST


def test_prompt_dir_fingerprint_pure_function(tmp_path):
    d = _copy_pack(tmp_path / "p")
    fp0 = prompt_dir_fingerprint(str(d))
    assert fp0 and prompt_dir_fingerprint(str(d)) == fp0        # non-empty, stable
    f = d / "screen-clip-v1.prompt.md"
    f.write_text(f.read_text() + "\nx")
    assert prompt_dir_fingerprint(str(d)) != fp0               # content-sensitive
    assert prompt_dir_fingerprint("") == ""                    # unset -> empty
    assert prompt_dir_fingerprint(str(tmp_path / "nope")) == ""  # missing dir -> empty
