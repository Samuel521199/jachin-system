from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VOICE_SERVER = ROOT / "voice_server"
if str(VOICE_SERVER) not in sys.path:
    sys.path.insert(0, str(VOICE_SERVER))

from services.voice_understanding import VoiceUnderstandingCorrector


def test_candidate_parser_corrects_lark_command() -> None:
    result = VoiceUnderstandingCorrector().correct("打开LUCK")

    selected = result["understanding"]["selected"]
    assert result["corrected_text"] == "打开Lark"
    assert selected["intent"] == "open_app"
    assert selected["slots"]["app"] == "Lark"


def test_candidate_parser_keeps_chat_statement_as_no_task() -> None:
    result = VoiceUnderstandingCorrector().correct("你说的都是对的")

    selected = result["understanding"]["selected"]
    assert result["corrected_text"] == "你说的都是对的"
    assert selected["intent"] == "no_task"


def test_candidate_parser_uses_contact_anchor_without_false_app_route() -> None:
    result = VoiceUnderstandingCorrector().correct("找到威廉")

    selected = result["understanding"]["selected"]
    assert result["corrected_text"] == "找到Vivian"
    assert selected["intent"] == "find_contact"
    assert selected["slots"]["contact"] == "Vivian"
    assert result["needs_confirmation"] is True


def test_send_message_with_weak_contact_asks_clarification_instead_of_guessing_vivian() -> None:
    result = VoiceUnderstandingCorrector().correct("打开LUCK 帮我给你发一条消息")

    selected = result["understanding"]["selected"]
    assert result["corrected_text"] == "打开Lark帮我给你发一条消息"
    assert selected["type"] == "clarification_required"
    assert selected["intent"] == "send_message"
    assert selected["slots"] == {"app": "Lark"}
    assert "contact" in selected["missing_slots"]
    assert selected["can_execute"] is False
    assert selected["question"]


def test_send_message_with_company_name_like_noise_does_not_autofill_vivian() -> None:
    result = VoiceUnderstandingCorrector().correct("打开LARK 帮我给EASY 发一条消息")

    selected = result["understanding"]["selected"]
    assert selected["type"] == "clarification_required"
    assert selected["intent"] == "send_message"
    assert selected["slots"] == {"app": "Lark"}
    assert "contact" in selected["missing_slots"]
    assert result["corrected_text"] == "打开Lark帮我给EASY 发一条消息"


def test_low_quality_send_message_audio_asks_clarification_without_rewriting_entities() -> None:
    raw = "请你打开那个帮我给路车发一掉休息"
    result = VoiceUnderstandingCorrector().correct(raw)

    selected = result["understanding"]["selected"]
    assert result["corrected_text"] == raw
    assert selected["type"] == "clarification_required"
    assert selected["intent"] == "send_message"
    assert "contact" in selected["missing_slots"]
    assert "message_content" in selected["missing_slots"]
    assert selected["slots"] == {}


def test_send_message_with_contact_but_no_content_asks_for_message_content() -> None:
    result = VoiceUnderstandingCorrector().correct("在LARK 给Neil发消息")

    selected = result["understanding"]["selected"]
    assert selected["type"] == "clarification_required"
    assert selected["intent"] == "send_message"
    assert selected["slots"] == {"app": "Lark", "contact": "Neil"}
    assert selected["missing_slots"] == ["message_content"]
    assert "内容" in selected["question"]


def test_candidate_parser_recognizes_exact_company_english_names() -> None:
    result = VoiceUnderstandingCorrector().correct("帮我找DANIEL")

    selected = result["understanding"]["selected"]
    assert result["corrected_text"] == "找到Daniel"
    assert selected["intent"] == "find_contact"
    assert selected["slots"]["contact"] == "Daniel"


def test_candidate_parser_uses_whole_word_boundaries_for_english_names() -> None:
    result = VoiceUnderstandingCorrector().correct("帮我找PATRICK")

    selected = result["understanding"]["selected"]
    assert result["corrected_text"] == "找到Patrick"
    assert selected["intent"] == "find_contact"
    assert selected["slots"]["contact"] == "Patrick"


def test_candidate_parser_rejects_short_ascii_fragments_as_entities() -> None:
    result = VoiceUnderstandingCorrector().correct("帮我找IS")

    selected = result["understanding"]["selected"]
    assert selected["intent"] == "no_task"
    assert result["understanding"]["entity_candidates"] == []


def test_candidate_parser_does_not_route_short_mixed_noise_to_lark() -> None:
    result = VoiceUnderstandingCorrector().correct("打开K 龙")

    selected = result["understanding"]["selected"]
    assert selected["intent"] == "no_task"
    assert "Lark" not in str(selected.get("slots") or {})


def test_candidate_parser_allows_weak_english_contact_with_find_action_but_requires_confirmation() -> None:
    result = VoiceUnderstandingCorrector().correct("\u5e2e\u6211\u627eGOLDEN")

    selected = result["understanding"]["selected"]
    assert selected["type"] == "task_requires_confirmation"
    assert selected["intent"] == "find_contact"
    assert selected["slots"]["contact"] == "Gordon"
    assert result["needs_confirmation"] is True


def test_candidate_parser_maps_charge_to_jachin_project_context() -> None:
    result = VoiceUnderstandingCorrector().correct("帮我看一下CHARGE")

    selected = result["understanding"]["selected"]
    assert selected["intent"] == "open_project"
    assert selected["slots"]["project"] == "Jachin"
    assert result["corrected_text"] == "帮我看一下Jachin"


def test_candidate_parser_maps_stt_hotword_aliases_before_reply_plan() -> None:
    result = VoiceUnderstandingCorrector().correct("在背书给一分发消息内容是今天几点开会")

    selected = result["understanding"]["selected"]
    assert selected["type"] == "task_requires_confirmation"
    assert selected["intent"] == "send_message"
    assert selected["slots"] == {"app": "Lark", "contact": "Ethan"}
    assert result["reply_plan"]["reply_intent"] == "confirm_external_action"


def test_candidate_parser_maps_neil_stt_alias_without_leaving_tail_noise() -> None:
    result = VoiceUnderstandingCorrector().correct("再LUCK 给你用法消息内容是同步一下")

    selected = result["understanding"]["selected"]
    assert selected["type"] == "task_requires_confirmation"
    assert selected["slots"] == {"app": "Lark", "contact": "Neil"}
    assert result["corrected_text"] == "再Lark给Neil消息内容是同步一下"


def test_candidate_parser_does_not_match_short_initial_contact_inside_long_noise() -> None:
    result = VoiceUnderstandingCorrector().correct("\u6253\u5f00KK KNELT")

    selected = result["understanding"]["selected"]
    assert selected["intent"] == "no_task"
    assert "KK" not in str(selected.get("slots") or {})
