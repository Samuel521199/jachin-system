from __future__ import annotations

import json

from l3_node.mission_intent_schema import MissionTaskType
from l3_node.semantic_slot_parser import parse_mission_intent
from l3_node.voice_entity_correction import correct_voice_entities, teach_alias
from l3_node.voice_risk_gate import decide_secondary_recognition


def test_dynamic_lexicon_extends_app_and_contact_aliases(tmp_path, monkeypatch) -> None:
    import l3_node.voice_entity_correction as vec

    lexicon = tmp_path / "domain_lexicon.json"
    lexicon.write_text(
        json.dumps(
            {
                "apps": {"Notion": {"aliases": ["motion"], "active": True}},
                "contacts": {"Neil": ["kneel"]},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(vec, "_lexicon_paths", lambda: [lexicon])

    correction = correct_voice_entities("open motion \u7ed9 kneel \u53d1\u9001\u6d88\u606f\u5185\u5bb9\u662f hello")

    assert correction.corrected_text == "open Notion \u7ed9 Neil \u53d1\u9001\u6d88\u606f\u5185\u5bb9\u662f hello"
    assert [(c.kind, c.original, c.canonical) for c in correction.corrections] == [
        ("app", "motion", "Notion"),
        ("contact", "kneel", "Neil"),
    ]


def test_teach_alias_persists_without_touching_synced_lexicon(tmp_path, monkeypatch) -> None:
    import l3_node.voice_entity_correction as vec

    monkeypatch.setattr(vec, "_repo_root", lambda: tmp_path)

    path = teach_alias("contact", "Ada", "eight da")
    data = json.loads(path.read_text(encoding="utf-8"))

    assert path == tmp_path / "data" / "voice" / "user_aliases.json"
    assert data["contacts"]["Ada"]["aliases"] == ["eight da"]
    assert data["contacts"]["Ada"]["active"] is True


def test_suspect_tokens_expose_unresolved_slot_candidates() -> None:
    correction = correct_voice_entities("\u6253\u5f00 xqlark \u7ed9 unknownperson \u53d1\u9001\u6d88\u606f\u5185\u5bb9\u662f hi")

    assert correction.suspect_tokens
    assert any(s.kind in {"app", "contact"} for s in correction.suspect_tokens)
    assert all(s.candidates for s in correction.suspect_tokens)


def test_high_risk_secondary_recognition_triggers_on_low_confidence_and_suspects(monkeypatch) -> None:
    monkeypatch.setenv("JACHIN_STT_CLOUD_FALLBACK", "1")

    decision = decide_secondary_recognition(
        text="\u7ed9 v \u8587 m \u53d1\u9001\u6d88\u606f \u5185\u5bb9\u662f \u5220\u9664\u6587\u4ef6",
        confidence=0.52,
        suspect_tokens=[{"token": "v \u8587 m"}, {"token": "\u5220\u9664\u6587\u4ef6"}],
        intent_task_type="lark_message_send",
    )

    assert decision.should_run is True
    assert decision.risk_level == "high"
    assert decision.preferred_provider == "cloud_asr"
    assert "high_risk_intent" in decision.reasons
    assert "low_stt_confidence" in decision.reasons


def test_chinese_negation_guard_blocks_lark_send_execution() -> None:
    intent = parse_mission_intent("\u4e0d\u8981\u7ed9 Vivian \u53d1\u6d88\u606f \u5185\u5bb9\u662f \u660e\u5929\u518d\u8bf4")

    assert intent.task_type == MissionTaskType.UNKNOWN
    assert "negated_send_or_delivery" in intent.reasoning
    assert "clarification" in intent.missing_slots



def test_alias_lifecycle_deactivate_and_bulk_import(tmp_path, monkeypatch) -> None:
    import l3_node.voice_entity_correction as vec

    monkeypatch.setattr(vec, "_repo_root", lambda: tmp_path)

    vec.bulk_import_aliases([
        {"kind": "app", "canonical": "Notion", "aliases": ["motion", "notion"]},
        {"kind": "contact", "canonical": "Ada", "aliases": ["eight da"]},
    ])
    before = vec.list_user_aliases()
    assert "motion" in before["apps"]["Notion"]["aliases"]
    assert "eight da" in before["contacts"]["Ada"]["aliases"]

    vec.deactivate_alias("app", "Notion", "motion")
    after = vec.list_user_aliases()
    assert "motion" not in after["apps"]["Notion"]["aliases"]
    assert after["apps"]["Notion"]["updated_at"] > 0
