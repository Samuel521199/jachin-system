from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VOICE_SERVER = ROOT / "voice_server"
if str(VOICE_SERVER) not in sys.path:
    sys.path.insert(0, str(VOICE_SERVER))

from services.voice_understanding import VoiceUnderstandingCorrector


def assert_stt_only(result: dict) -> None:
    understanding = result["understanding"]
    assert understanding["voice_layer_scope"] == "stt_only"
    assert understanding["selected"] == {}
    assert understanding["task_candidates"] == []
    assert understanding["reply_plan"] == {}
    assert result["reply_plan"] == {}
    assert result["user_message"] == ""
    assert result["user_message_source"] == ""
    assert result["needs_confirmation"] is False


def entity_names(result: dict) -> set[str]:
    return {str(item.get("canonical")) for item in result["understanding"]["entity_candidates"]}


def test_voice_understanding_keeps_lark_alias_as_plain_stt() -> None:
    result = VoiceUnderstandingCorrector().correct("\u6253\u5f00LUCK")

    assert result["corrected_text"] == "\u6253\u5f00LUCK"
    assert entity_names(result) == set()
    assert_stt_only(result)


def test_voice_understanding_keeps_chat_statement_as_plain_stt() -> None:
    raw = "\u4f60\u8bf4\u7684\u90fd\u662f\u5bf9\u7684"
    result = VoiceUnderstandingCorrector().correct(raw)

    assert result["corrected_text"] == raw
    assert "Lark" not in entity_names(result)
    assert_stt_only(result)


def test_voice_understanding_keeps_contact_and_app_text_plain() -> None:
    result = VoiceUnderstandingCorrector().correct("\u5728LARK \u7ed9Neil\u53d1\u6d88\u606f")

    assert result["corrected_text"] == "\u5728LARK \u7ed9Neil\u53d1\u6d88\u606f"
    assert entity_names(result) == set()
    assert_stt_only(result)


def test_voice_understanding_keeps_recorded_hotword_aliases_plain() -> None:
    result = VoiceUnderstandingCorrector().correct("\u5728\u80cc\u4e66\u7ed9\u4e00\u5206\u53d1\u6d88\u606f\u5185\u5bb9\u662f\u4eca\u5929\u51e0\u70b9\u5f00\u4f1a")

    assert result["corrected_text"] == "\u5728\u80cc\u4e66\u7ed9\u4e00\u5206\u53d1\u6d88\u606f\u5185\u5bb9\u662f\u4eca\u5929\u51e0\u70b9\u5f00\u4f1a"
    assert entity_names(result) == set()
    assert_stt_only(result)


def test_voice_understanding_keeps_neil_alias_plain() -> None:
    result = VoiceUnderstandingCorrector().correct("\u518dLUCK \u7ed9\u4f60\u7528\u6cd5\u6d88\u606f\u5185\u5bb9\u662f\u540c\u6b65\u4e00\u4e0b")

    assert result["corrected_text"] == "\u518dLUCK \u7ed9\u4f60\u7528\u6cd5\u6d88\u606f\u5185\u5bb9\u662f\u540c\u6b65\u4e00\u4e0b"
    assert entity_names(result) == set()
    assert_stt_only(result)


def test_voice_understanding_keeps_project_alias_plain() -> None:
    result = VoiceUnderstandingCorrector().correct("\u5e2e\u6211\u770b\u4e00\u4e0bCHARGE")

    assert result["corrected_text"] == "\u5e2e\u6211\u770b\u4e00\u4e0bCHARGE"
    assert entity_names(result) == set()
    assert_stt_only(result)


def test_voice_understanding_rejects_calculator_noise_as_chrome_entity() -> None:
    result = VoiceUnderstandingCorrector().correct("\u5e2e\u6211\u6253\u5f00\u8ba1\u7b97\u5668\u7b97\u4e00\u4e0b40*90")

    assert result["corrected_text"] == "\u5e2e\u6211\u6253\u5f00\u8ba1\u7b97\u5668\u7b97\u4e00\u4e0b40*90"
    assert "Chrome" not in entity_names(result)
    assert_stt_only(result)
