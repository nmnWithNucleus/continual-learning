"""Translation stage — the TRANSLATE_BACKEND=off|mock|whisper switch.

Hermetic: mock ASR + mock translation (no GPU, no faster-whisper). Proves:
  * a target that differs from the detected language appends ONE extra
    discriminator="translation" record beside the untouched primary (2 records);
  * the translation record is schema-valid: kind='transcript', language=target,
    distinct deterministic record_id, segments in the exact 4-key shape;
  * the primary record is UNCHANGED (same id as with translation off) — translation is
    additive, never a mutation, and does NOT tag pipeline_version;
  * off / empty-target / same-language / whisper+non-'en' all emit no translation record.
"""
from __future__ import annotations

from app import schemas
from app.asr import mock as mock_asr
from app.pipeline import compute_record_id
from tests.conftest import make_c1

_PV = mock_asr.PIPELINE_VERSION  # "asr-mock-v0" — translation does NOT fork it


def _enable_mock_translate(monkeypatch, target="fr"):
    monkeypatch.setenv("TRANSLATE_BACKEND", "mock")
    monkeypatch.setenv("TRANSLATE_TARGET", target)


# ---- Active: primary + one translation sidecar -------------------------------

def test_mock_translation_appends_sidecar_record(client, monkeypatch):
    _enable_mock_translate(monkeypatch, target="fr")  # mock ASR detects 'en' != 'fr'
    c1 = make_c1(client.fake_storage, chunk_id="tr-on")
    resp = client.post("/ingest", json=c1)
    assert resp.status_code == 200

    record_ids = resp.json()["record_ids"]
    assert len(record_ids) == 2
    assert len(set(record_ids)) == 2  # distinct
    # Primary is the UNCHANGED undiarized/untranslated id; sidecar folds "translation".
    assert record_ids[0] == compute_record_id("tr-on", _PV, "")
    assert record_ids[1] == compute_record_id("tr-on", _PV, "translation")

    primary, translation = client.fake_storage.record_posts
    assert schemas.validate_c2(primary) == [] and schemas.validate_c2(translation) == []

    # Primary untouched.
    assert primary["content"]["kind"] == "transcript"
    assert primary["content"]["language"] == "en"

    # Translation record.
    assert translation["content"]["kind"] == "transcript"
    assert translation["content"]["language"] == "fr"
    assert translation["content"]["text"].startswith("[mock translation -> fr]")
    assert translation["pipeline_version"] == _PV  # shares pv; NOT forked
    assert translation["enrichments"] == {
        "speakers": [], "faces": [], "places": [], "objects": [],
    }
    # Segments are the exact 4-key shape with speaker null.
    for seg in translation["content"]["segments"]:
        assert set(seg) == {"t_start", "t_end", "text", "speaker"}
        assert seg["speaker"] is None


def test_translation_does_not_change_primary_id(client, monkeypatch):
    # Same chunk, translation on -> primary id identical to translation off.
    _enable_mock_translate(monkeypatch, target="fr")
    c1 = make_c1(client.fake_storage, chunk_id="tr-primary")
    resp = client.post("/ingest", json=c1)
    assert resp.json()["record_ids"][0] == compute_record_id("tr-primary", _PV, "")


# ---- Inactive paths: exactly one record --------------------------------------

def test_translation_off_by_default(client, monkeypatch):
    monkeypatch.delenv("TRANSLATE_BACKEND", raising=False)
    monkeypatch.delenv("TRANSLATE_TARGET", raising=False)
    c1 = make_c1(client.fake_storage, chunk_id="tr-off")
    assert len(client.post("/ingest", json=c1).json()["record_ids"]) == 1


def test_translation_needs_a_target(client, monkeypatch):
    monkeypatch.setenv("TRANSLATE_BACKEND", "mock")
    monkeypatch.setenv("TRANSLATE_TARGET", "")  # no target -> off
    c1 = make_c1(client.fake_storage, chunk_id="tr-notarget")
    assert len(client.post("/ingest", json=c1).json()["record_ids"]) == 1


def test_translation_skipped_when_target_equals_detected(client, monkeypatch):
    _enable_mock_translate(monkeypatch, target="en")  # mock ASR detects 'en'
    c1 = make_c1(client.fake_storage, chunk_id="tr-same")
    assert len(client.post("/ingest", json=c1).json()["record_ids"]) == 1


def test_whisper_non_english_target_degrades_to_off(client, monkeypatch):
    # Whisper task=translate is English-only; a non-'en' target is a misconfig that
    # degrades to off (logged) rather than 500-ing the ingest or mistranslating.
    monkeypatch.setenv("TRANSLATE_BACKEND", "whisper")
    monkeypatch.setenv("TRANSLATE_TARGET", "fr")
    c1 = make_c1(client.fake_storage, chunk_id="tr-whisper-fr")
    resp = client.post("/ingest", json=c1)
    assert resp.status_code == 200
    assert len(resp.json()["record_ids"]) == 1  # no translation record
