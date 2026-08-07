"""The prompt pack under the DP rebuild — digest, resolution, render, CLI.

The kept subject (plan §6): the pack registry, its normalised content digest, the
scenario resolution and the relock/show tooling. The v0 dialect half (cfg_tag,
OUTPUT_AFFECTING classification, version_tag suffixes, VIDEO_CLIP_PROMPT /
VIDEO_PROMPT_DIR env overrides) died with the no-knobs law (L4): identity is now the
clipcap stage's ``Backend`` vB, welded to the pack by the ``PACK_DIGEST_PIN``
registration gate (tested in tests/test_clipcap.py).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

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
)

PACKAGED = Path(P.__file__).resolve().parent
_ALL = {
    "screen-clip-v1", "screen-clip-idle-v1", "screen-clip-single-v1",
    "screen-ocr-v1", "camera-clip-v1",
}
# The legacy per-frame-v0 pack was removed at Stage G demolition; the two tests that
# used it as a schema-less / placeholder-free example now build inline fixtures, so the
# parser coverage does not depend on a retired pack.
_SCHEMALESS_PROMPT = (
    "---\nid: legacy-x\nrole: legacy\nschema: none\n---\n"
    "[system]\nA static describer with no placeholders.\n\n"
    "[user]\nDescribe the frame in two lines and nothing else.\n"
)


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


def test_packaged_source_only():
    # The VIDEO_PROMPT_DIR override is dead (L4): packs load from the package dir,
    # full stop; a prompt change reaches production only as a code change + vB bump.
    assert PROMPT_SOURCE == "packaged"


def test_normalise_ignores_line_endings_and_trailing_ws():
    schemas = json.loads((PACKAGED / "schemas.json").read_text())
    txt = (PACKAGED / "screen-clip-v1.prompt.md").read_text()
    a = parse_prompt(txt, schemas)
    b = parse_prompt(txt.replace("\n", "\r\n"), schemas)
    assert normalise(a) == normalise(b)


# ============================================================ resolution (scenario pins)
def test_scenario_routes_to_pack():
    assert select("screen-mac").id == "screen-clip-v1"
    assert select("screen-browser").id == "screen-clip-v1"
    assert select("camera").id == "camera-clip-v1"


def test_unknown_scenario_falls_to_family_default():
    assert select("mars").id == P.family_default("clip") == "screen-clip-v1"


def test_get_unknown_id_raises():
    with pytest.raises(KeyError):
        get("no-such-pack")


def test_routes_referencing_absent_pack_fails_loud_at_load(tmp_path):
    """A registry whose routes.json references a pack that is not on disk must FAIL
    LOUD at load — never as a later select() crash mid-run."""
    d = _copy_subset(tmp_path / "p", ["camera-clip-v1", "screen-ocr-v1"])
    with pytest.raises(PromptPackError):
        load_registry(d)


def test_coherent_subset_registry_loads(tmp_path):
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


def test_non_clip_route_is_rejected_to_the_default(monkeypatch):
    # A route that names a KNOWN but non-clip pack (an ocr pack, the legacy
    # fingerprint record) must not be rendered by the captioner.
    monkeypatch.setattr(P, "_ROUTES", {
        **P.routes(),
        "scenarios": {"screen-mac": "screen-ocr-v1"},
    })
    assert select("screen-mac").id == "screen-clip-v1"


# ============================================================ parse
def test_parse_round_trips_a_clip_pack():
    schemas = json.loads((PACKAGED / "schemas.json").read_text())
    spec = parse_prompt((PACKAGED / "screen-clip-v1.prompt.md").read_text(), schemas)
    assert spec.id == "screen-clip-v1" and spec.role == "clip"
    assert spec.max_tokens == 512 and spec.temperature == 0.0
    assert spec.schema_name == "clip-json-v1" and spec.schema["type"] == "object"
    assert "[[span_s]]" in spec.user           # the TEMPLATE keeps its placeholders


def test_parse_schema_none_pack():
    spec = parse_prompt(_SCHEMALESS_PROMPT, {})
    assert spec.schema_name == "" and spec.schema == {}


def test_parse_unknown_schema_raises():
    with pytest.raises(PromptPackError):
        parse_prompt("---\nid: x\nrole: clip\nschema: nope\n---\n[system]\na\n\n[user]\nb\n", {})


def test_parse_missing_user_section_raises():
    with pytest.raises(PromptPackError):
        parse_prompt("---\nid: x\nrole: clip\n---\n[system]\nonly system\n", {})


def test_parse_non_numeric_decode_param_raises_typed():
    bad = "---\nid: x\nrole: clip\nmax_tokens: lots\n---\n[system]\na\n\n[user]\nb\n"
    with pytest.raises(PromptPackError):
        parse_prompt(bad, {})


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
    spec = parse_prompt(_SCHEMALESS_PROMPT, {})
    r = render(spec, unused="ignored")
    assert "[[" not in r.system and "[[" not in r.user


# ============================================================ CLI: show + relock
def test_show_prints_exact_wire_text(capsys):
    from app.vision.prompts import show

    rc = show.main(["--pack", "screen-clip-v1", "--span", "60", "--frames", "12"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "You are a screen-recording annotator" in out
    assert "60 seconds of a macOS desktop" in out
    assert "128-160 words" in out                # the clipcap CAPTION_RATE=16 band
    assert "[[" not in out                       # every placeholder is filled
    assert PACK_DIGEST in out and f"p{PACK_VERSION}" in out


def test_show_ocr_pack(capsys):
    from app.vision.prompts import show

    assert show.main(["--pack", "screen-ocr-v1"]) == 0
    out = capsys.readouterr().out
    assert "You transcribe text that is visible" in out
    assert "[[" not in out


def test_show_scenario_flag_renders_that_scenarios_label(capsys):
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
    # wire text is reconstructable from p1.json alone.
    assert archived["routes"]["family_defaults"]["clip"] == "screen-clip-v1"


# ============================================================ load discipline
def test_load_registry_from_dir_matches_packaged(tmp_path):
    d = _copy_pack(tmp_path / "p")
    packs, routes, _ = load_registry(d)
    assert set(packs) == _ALL
    assert compute_digest(packs, routes) == PACK_DIGEST
