from __future__ import annotations

import json
import re

from l3_node.mission_intent_schema import MissionTaskType
from l3_node.semantic_slot_parser import parse_mission_intent
from l3_node.voice_entity_correction import correct_voice_entities, teach_alias
from l3_node.voice_language_normalizer import normalize_voice_language_input
from l3_node.voice_risk_gate import decide_secondary_recognition


def _visible_reply(text: str | None) -> str:
    return re.sub(r"\n*\s*<!-- jachin-ui:[\s\S]*?-->\s*$", "", str(text or "")).strip()


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


def test_voice_language_normalizer_corrects_entities_with_voice_evidence() -> None:
    result = normalize_voice_language_input(
        "open lock 给 kneel 发送消息内容是 hello",
        channel="websocket_terminal",
        voice_context={
            "source": "desktop_voice_companion",
            "voice_raw_stt_text": "open lock 给 kneel 发送消息内容是 hello",
            "voice_stt_confidence": 0.74,
        },
    )

    assert result.is_voice is True
    assert result.normalized_text == "open Lark 给 Neil 发送消息内容是 hello"
    assert [(c.kind, c.original, c.canonical) for c in result.correction.corrections] == [
        ("app", "lock", "Lark"),
        ("contact", "kneel", "Neil"),
    ]


def test_voice_language_normalizer_does_not_force_hotword_when_primary_asr_is_clear() -> None:
    result = normalize_voice_language_input(
        "let us discuss the roadmap today",
        channel="websocket_terminal",
        voice_context={
            "source": "desktop_voice_companion",
            "voice_raw_stt_text": "let us discuss the roadmap today",
            "voice_stt_confidence": 0.95,
            "voice_asr_alternatives": [
                {"text": "let us discuss the roadmap today", "confidence": 0.95},
                {"text": "open Lark", "confidence": 0.67},
            ],
        },
    )

    assert result.normalized_text == "let us discuss the roadmap today"
    assert result.selected_candidate["text"] == "let us discuss the roadmap today"
    alt = next(item for item in result.asr_candidates if item["text"] == "open Lark")
    assert alt["hotword_gate"] == "allow"
    assert alt["hotword_used_for_selection"] is False


def test_voice_language_normalizer_uses_hotword_alternative_when_primary_is_missing_slot() -> None:
    result = normalize_voice_language_input(
        "open",
        channel="websocket_terminal",
        voice_context={
            "source": "desktop_voice_companion",
            "voice_raw_stt_text": "open",
            "voice_stt_confidence": 0.61,
            "voice_asr_alternatives": [
                {"text": "open", "confidence": 0.61},
                {"text": "open Lark", "confidence": 0.72},
            ],
        },
    )

    assert result.normalized_text == "open Lark"
    assert result.selected_candidate["text"] == "open Lark"
    assert result.selected_candidate["hotword_used_for_selection"] is True
    assert result.selected_candidate["selection_reason"] == "alternative_candidate_has_clearer_action_slot"


def test_voice_language_normalizer_maps_spoken_yes_to_pending_resume(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.contracts import DecisionContract, RiskLevel, ToolPolicy, WorkOrder
    from l3_node.cognitive_kernel.pending_confirmation import save_pending_confirmation

    contract = DecisionContract(
        decision_id="decision-voice-confirm",
        turn_id="turn-voice-confirm",
        task_type="app_control",
        goal="open Lark",
        selected_workflow="work_order_role_dispatcher",
        selected_roles=["AppControlExecutorAgent"],
        risk_level=RiskLevel.LOW,
        tool_policy=ToolPolicy(
            allowed_tools=["mcp:windows_open_app"],
            risk_level=RiskLevel.LOW,
            requires_confirmation=True,
            confirmation_reason="entity correction requires confirmation",
        ),
        execution_allowed=False,
        clarification_question="你是不是要打开 Lark？",
    )
    work_order = WorkOrder(
        work_order_id="work-voice-confirm",
        decision_id=contract.decision_id,
        role_agent="AppControlExecutorAgent",
        task="open Lark",
        inputs={"tool": "mcp:windows_open_app", "target": {"name": "Lark"}},
        tool_policy=contract.tool_policy,
    )
    save_pending_confirmation(
        contract=contract,
        work_order=work_order,
        session_id="voice-session",
        channel="websocket_terminal",
    )

    result = normalize_voice_language_input(
        "对，就是这个",
        session_id="voice-session",
        channel="websocket_terminal",
        voice_context={"voice_raw_stt_text": "对，就是这个", "source": "desktop_voice_companion"},
    )

    assert result.pending_confirmation_detected is True
    assert result.normalized_text == "确认执行"


def test_voice_input_adapter_feeds_goal_interpreter_and_decomposer(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    import asyncio

    from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn
    from l3_node.cognitive_kernel.pipeline import build_cognitive_turn_context

    ctx = asyncio.run(
        build_cognitive_turn_context(
            run_id="voice-adapter-mainline",
            user_input="open lock 给 kneel 发送消息内容是 hello",
            channel="websocket_terminal",
            session_id="voice-session",
            prior_messages=[],
            desktop_companion_context={
                "source": "desktop_voice_companion",
                "voice_raw_stt_text": "open lock 给 kneel 发送消息内容是 hello",
                "voice_stt_confidence": 0.86,
            },
        )
    )

    assert ctx.envelope.source.value == "voice"
    assert ctx.envelope.raw_text == "open lock 给 kneel 发送消息内容是 hello"
    assert ctx.envelope.normalized_text == "open Lark 给 Neil 发送消息内容是 hello"
    assert ctx.input_adaptation is not None
    assert ctx.input_adaptation.changed is True

    plan = plan_cognitive_turn(ctx, emit_non_execution_closure=False)

    assert plan.goal_interpretation is not None
    assert plan.goal_interpretation.constraints["input_source"] == "voice"
    assert plan.review_summary.task_type == "message_delivery"
    assert plan.review_summary.target["app"] == "Lark"
    assert plan.review_summary.target["recipients"] == ["Neil"]
    assert plan.review_summary.target["message"] == "hello"
    assert plan.work_orders
    assert all(work.inputs.get("input_context", {}).get("source") == "voice" for work in plan.work_orders)
    assert any(work.role_agent == "MessageExecutorAgent" for work in plan.work_orders)


def test_voice_open_log_is_lark_app_control_without_chat_clarification(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    import asyncio

    from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn
    from l3_node.cognitive_kernel.pipeline import build_cognitive_turn_context

    ctx = asyncio.run(
        build_cognitive_turn_context(
            run_id="voice-open-log-lark",
            user_input="\u6253\u5f00 log",
            channel="websocket_terminal",
            session_id="voice-session",
            prior_messages=[],
            desktop_companion_context={
                "source": "desktop_voice_companion",
                "voice_raw_stt_text": "\u6253\u5f00 log",
                "voice_stt_confidence": 0.86,
            },
        )
    )
    plan = plan_cognitive_turn(ctx, emit_non_execution_closure=False)

    assert ctx.envelope.normalized_text == "\u6253\u5f00 Lark"
    assert plan.review_summary.task_type == "app_control"
    assert plan.review_summary.top_intent == "open_app"
    assert plan.review_summary.target["name"] == "Lark"
    assert plan.review_summary.needs_clarification is False
    assert plan.work_orders
    assert plan.work_orders[0].inputs["work_order_input"] == '{"app": "Lark"}'


def test_pending_app_slot_reply_fills_and_resumes_lark() -> None:
    from l3_node.cognitive_kernel.contracts import RiskLevel, ToolPolicy, WorkOrder
    from l3_node.cognitive_kernel.direct_mainline import _fill_pending_app_slot, _resolve_pending_app_slot_reply

    work_order = WorkOrder(
        work_order_id="work-missing-app",
        decision_id="decision-missing-app",
        role_agent="AppControlExecutorAgent",
        task="open_app",
        inputs={"tool": "mcp:windows_open_app", "intent": "open_app", "target": {}, "work_order_input": '{"app": ""}'},
        tool_policy=ToolPolicy(
            allowed_tools=["mcp:windows_open_app"],
            risk_level=RiskLevel.LOW,
            requires_confirmation=True,
            confirmation_reason="\u4f60\u60f3\u64cd\u4f5c\u54ea\u4e2a\u5e94\u7528\uff1f",
        ),
    )

    assert _resolve_pending_app_slot_reply("Lock", work_order) == "Lark"
    assert _resolve_pending_app_slot_reply("log", work_order) == "Lark"

    _fill_pending_app_slot(work_order, app_name="Lark", heard_as="Lock")

    assert work_order.inputs["target"]["name"] == "Lark"
    assert work_order.inputs["target"]["source"] == "pending_slot_reply"
    assert work_order.inputs["work_order_input"] == '{"app": "Lark"}'
    assert work_order.task == "open_app Lark"


def test_pending_app_slot_reply_learns_confirmed_alias(tmp_path, monkeypatch) -> None:
    from l3_node.cognitive_kernel.contracts import RiskLevel, ToolPolicy, WorkOrder
    from l3_node.cognitive_kernel.direct_mainline import _fill_pending_app_slot
    from l3_node.voice_entity_correction import list_user_aliases

    monkeypatch.setenv("JACHIN_VOICE_USER_ALIASES_PATH", str(tmp_path / "voice_aliases.json"))
    work_order = WorkOrder(
        work_order_id="work-missing-app-alias",
        decision_id="decision-missing-app-alias",
        role_agent="AppControlExecutorAgent",
        task="open_app",
        inputs={"tool": "mcp:windows_open_app", "intent": "open_app", "target": {}, "work_order_input": '{"app": ""}'},
        tool_policy=ToolPolicy(allowed_tools=["mcp:windows_open_app"], risk_level=RiskLevel.LOW),
    )

    _fill_pending_app_slot(work_order, app_name="Lark", heard_as="Lock")

    aliases = list_user_aliases()
    assert "Lock" in aliases["apps"]["Lark"]["aliases"]


def test_pending_message_slot_reply_does_not_learn_shortcut_as_alias(tmp_path, monkeypatch) -> None:
    from l3_node.cognitive_kernel.contracts import RiskLevel, ToolPolicy, WorkOrder
    from l3_node.cognitive_kernel.direct_mainline import _fill_pending_message_slot
    from l3_node.voice_entity_correction import list_user_aliases

    monkeypatch.setenv("JACHIN_VOICE_USER_ALIASES_PATH", str(tmp_path / "voice_aliases.json"))
    work_order = WorkOrder(
        work_order_id="work-message-shortcut",
        decision_id="decision-message-shortcut",
        role_agent="MessageExecutorAgent",
        task="message_send",
        inputs={
            "tool": "mcp:windows_lark_send_message",
            "intent": "message_send",
            "target": {"type": "lark_message", "recipients": [], "message": "hello"},
            "work_order_input": '{"recipients_json": "[]", "message": "hello", "max_attempts": 2}',
        },
        tool_policy=ToolPolicy(allowed_tools=["mcp:windows_lark_send_message"], risk_level=RiskLevel.LOW),
    )

    _fill_pending_message_slot(work_order, patch={"recipient": "Neil"}, heard_as="A")

    aliases = list_user_aliases()
    assert aliases["contacts"] == {}


def test_pending_message_recipient_choice_fills_builtin_contacts() -> None:
    from l3_node.cognitive_kernel.contracts import RiskLevel, ToolPolicy, WorkOrder
    from l3_node.cognitive_kernel.direct_mainline import (
        _fill_pending_message_slot,
        _resolve_pending_message_slot_reply,
    )

    work_order = WorkOrder(
        work_order_id="work-missing-recipient",
        decision_id="decision-missing-recipient",
        role_agent="MessageExecutorAgent",
        task="message_send",
        inputs={
            "tool": "mcp:windows_lark_send_message",
            "intent": "message_send",
            "target": {"type": "lark_message", "app": "Lark", "recipients": [], "message": "你好"},
            "work_order_input": '{"recipients_json": "[]", "message": "你好", "max_attempts": 2}',
        },
        tool_policy=ToolPolicy(
            allowed_tools=["mcp:windows_lark_send_message"],
            risk_level=RiskLevel.LOW,
        ),
    )

    assert _resolve_pending_message_slot_reply("1", work_order) == {"recipient": "Neil"}
    assert _resolve_pending_message_slot_reply("A", work_order) == {"recipient": "Neil"}
    assert _resolve_pending_message_slot_reply("Neil", work_order) == {"recipient": "Neil"}
    assert _resolve_pending_message_slot_reply("2", work_order) == {"recipient": "Vivian"}
    assert _resolve_pending_message_slot_reply("C", work_order) == {"recipient": "测试备注冒烟草稿"}

    _fill_pending_message_slot(work_order, patch={"recipient": "测试备注冒烟草稿"}, heard_as="C")

    assert work_order.inputs["target"]["recipients"] == ["测试备注冒烟草稿"]
    assert "测试备注冒烟草稿" in work_order.inputs["work_order_input"]
    assert '"message": "你好"' in work_order.inputs["work_order_input"]


def test_pending_message_recipient_choices_can_come_from_contact_config(tmp_path, monkeypatch) -> None:
    import json

    from l3_node.cognitive_kernel.contracts import RiskLevel, ToolPolicy, WorkOrder
    from l3_node.cognitive_kernel.direct_mainline import _resolve_pending_message_slot_reply

    contact_path = tmp_path / "message_contacts.json"
    contact_path.write_text(
        json.dumps(
            {
                "version": 1,
                "contacts": [
                    {
                        "name": "老张",
                        "kind": "person",
                        "aliases": ["张哥"],
                        "shortcut_number": "1",
                        "shortcut_letter": "A",
                        "enabled": True,
                    },
                    {
                        "name": "研发群",
                        "kind": "group",
                        "aliases": ["研发"],
                        "shortcut_number": "2",
                        "shortcut_letter": "B",
                        "enabled": True,
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("JACHIN_MESSAGE_CONTACTS_PATH", str(contact_path))

    work_order = WorkOrder(
        work_order_id="work-config-recipient",
        decision_id="decision-config-recipient",
        role_agent="MessageExecutorAgent",
        task="message_send",
        inputs={
            "tool": "mcp:windows_lark_send_message",
            "work_order_input": '{"recipients_json": "[]", "message": "你好", "max_attempts": 2}',
        },
        tool_policy=ToolPolicy(
            allowed_tools=["mcp:windows_lark_send_message"],
            risk_level=RiskLevel.LOW,
        ),
    )

    assert _resolve_pending_message_slot_reply("1", work_order) == {"recipient": "老张"}
    assert _resolve_pending_message_slot_reply("B", work_order) == {"recipient": "研发群"}
    assert _resolve_pending_message_slot_reply("张哥", work_order) == {"recipient": "老张"}

def test_message_send_missing_recipient_asks_contact_choice_without_generic_confirmation(tmp_path, monkeypatch) -> None:
    import asyncio

    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.direct_mainline import pending_slot_reply_available, try_execute_cognitive_direct_plan
    from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn
    from l3_node.cognitive_kernel.pipeline import build_cognitive_turn_context

    async def run() -> tuple[str | None, str | None, list[tuple[str, str]]]:
        text = "\u53d1\u9001\u6d88\u606f\uff0c\u4f60\u597d\u3002"
        ctx = await build_cognitive_turn_context(
            run_id="message-missing-recipient-slot",
            user_input=text,
            channel="websocket_terminal",
            session_id="message-slot-session",
            prior_messages=[],
            desktop_companion_context={},
        )
        plan = plan_cognitive_turn(ctx, emit_non_execution_closure=False)
        assert plan.review_summary.top_intent == "message_send"
        assert plan.review_summary.target["message"] == "\u4f60\u597d"
        assert plan.work_orders

        calls: list[tuple[str, str]] = []

        async def fake_tool(tool: str, args: str, allowed_skills: list[str] | None):
            calls.append((tool, args))
            return '{"ok": true, "send_ok": true, "recipient": "Neil", "message_id": "fake"}'

        reply1 = await try_execute_cognitive_direct_plan(
            plan=plan,
            tools=[{"id": "mcp:windows_lark_send_message"}],
            allowed_skills=None,
            run_tool_func=fake_tool,
            user_input=text,
            session_id="message-slot-session",
            channel="websocket_terminal",
        )
        assert calls == []
        assert pending_slot_reply_available(
            user_input="Neil",
            session_id="message-slot-session",
            channel="websocket_terminal",
        )
        ctx2 = await build_cognitive_turn_context(
            run_id="message-missing-recipient-slot-choice",
            user_input="Neil",
            channel="websocket_terminal",
            session_id="message-slot-session",
            prior_messages=[],
            desktop_companion_context={},
        )
        plan2 = plan_cognitive_turn(ctx2, emit_non_execution_closure=False)
        reply2 = await try_execute_cognitive_direct_plan(
            plan=plan2,
            tools=[{"id": "mcp:windows_lark_send_message"}],
            allowed_skills=None,
            run_tool_func=fake_tool,
            user_input="Neil",
            session_id="message-slot-session",
            channel="websocket_terminal",
        )
        return reply1, reply2, calls

    reply, sent_reply, calls = asyncio.run(run())

    assert reply is not None
    assert "\u6211\u8fd8\u4e0d\u77e5\u9053\u8fd9\u6761\u6d88\u606f\u8981\u53d1\u7ed9\u8c01" in reply
    assert "\u786e\u8ba4\u6267\u884c" not in reply
    assert "1/A = Neil" in reply
    assert "jachin-ui:pending-confirmation" in reply
    assert '"interaction_kind":"slot_choice"' in reply
    assert '"slot":"recipient"' in reply
    assert '"label":"Neil"' in reply
    assert '"send_text":"Neil"' in reply
    assert _visible_reply(sent_reply) == "\u5df2\u53d1\u9001\u6d88\u606f\u7ed9 Neil\u3002"
    assert calls and calls[0][0] == "mcp:windows_lark_send_message"
    assert '"message": "\u4f60\u597d"' in calls[0][1]
    assert "Neil" in calls[0][1]


def test_voice_action_segment_wins_over_background_speech_for_message_send(tmp_path, monkeypatch) -> None:
    import asyncio

    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn
    from l3_node.cognitive_kernel.pipeline import build_cognitive_turn_context
    from l3_node.cognitive_kernel.direct_mainline import try_execute_cognitive_direct_plan
    from l3_node.voice_language_normalizer import extract_actionable_voice_segment

    mixed = "\u53d1\u9001\u6d88\u606f\uff0c\u4f60\u597d\u3002\u884c\uff0c\u5bf9\uff0c\u5c31\u770b\u90a3\u4e2a\u3002"
    extracted = extract_actionable_voice_segment(mixed)
    assert extracted["normalized_text"] == "\u53d1\u9001\u6d88\u606f\uff0c\u4f60\u597d"
    assert extracted["dropped_background_segments"]

    async def run():
        ctx = await build_cognitive_turn_context(
            run_id="voice-message-with-background-speech",
            user_input=mixed,
            channel="websocket_terminal",
            session_id="voice-background-session",
            prior_messages=[],
            desktop_companion_context={
                "source": "desktop_voice_companion",
                "voice_interaction_mode": "continuous_listen",
                "voice_raw_stt_text": mixed,
                "voice_stt_confidence": 0.86,
                "voice_speaker_verified": True,
            },
        )
        plan = plan_cognitive_turn(ctx, emit_non_execution_closure=False)
        calls: list[tuple[str, str]] = []

        async def fake_tool(tool: str, args: str, allowed_skills: list[str] | None):
            calls.append((tool, args))
            return '{"ok": true}'

        reply = await try_execute_cognitive_direct_plan(
            plan=plan,
            tools=[{"id": "mcp:windows_lark_send_message"}],
            allowed_skills=None,
            run_tool_func=fake_tool,
            user_input=mixed,
            session_id="voice-background-session",
            channel="websocket_terminal",
        )
        return ctx, plan, reply, calls

    ctx, plan, reply, calls = asyncio.run(run())

    assert ctx.envelope.source.value == "voice"
    assert ctx.envelope.normalized_text == "\u53d1\u9001\u6d88\u606f\uff0c\u4f60\u597d"
    assert plan.review_summary.top_intent == "message_send"
    assert plan.review_summary.task_type == "message_delivery"
    assert plan.review_summary.target["message"] == "\u4f60\u597d"
    assert plan.review_summary.needs_clarification is True
    assert "\u53d1\u7ed9\u8c01" in plan.review_summary.clarification_question
    assert reply is not None
    assert "\u6211\u8fd8\u4e0d\u77e5\u9053\u8fd9\u6761\u6d88\u606f\u8981\u53d1\u7ed9\u8c01" in reply
    assert "\u4f60\u662f\u60f3\u548c\u6211\u804a" not in reply
    assert calls == []


def test_voice_action_segment_wins_over_background_speech_for_open_app(tmp_path, monkeypatch) -> None:
    import asyncio

    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn
    from l3_node.cognitive_kernel.pipeline import build_cognitive_turn_context

    mixed = "\u6253\u5f00\u5fae\u4fe1\uff0c\u884c\uff0c\u5bf9\uff0c\u5c31\u770b\u90a3\u4e2a\u3002"

    async def run():
        ctx = await build_cognitive_turn_context(
            run_id="voice-open-wechat-with-background-speech",
            user_input=mixed,
            channel="websocket_terminal",
            session_id="voice-background-open-session",
            prior_messages=[],
            desktop_companion_context={
                "source": "desktop_voice_companion",
                "voice_interaction_mode": "continuous_listen",
                "voice_raw_stt_text": mixed,
                "voice_stt_confidence": 0.9,
                "voice_speaker_verified": True,
            },
        )
        plan = plan_cognitive_turn(ctx, emit_non_execution_closure=False)
        return ctx, plan

    ctx, plan = asyncio.run(run())

    assert ctx.envelope.normalized_text == "\u6253\u5f00WeChat"
    assert plan.review_summary.top_intent == "open_app"
    assert plan.review_summary.task_type == "app_control"
    assert plan.review_summary.target["name"] == "WeChat"


def test_voice_background_noise_alone_does_not_become_task(tmp_path, monkeypatch) -> None:
    import asyncio

    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.pipeline import build_cognitive_turn_context

    noise = "\u884c\uff0c\u5bf9\uff0c\u5c31\u770b\u90a3\u4e2a\u3002"

    async def run():
        return await build_cognitive_turn_context(
            run_id="voice-background-noise-alone",
            user_input=noise,
            channel="websocket_terminal",
            session_id="voice-background-noise-session",
            prior_messages=[],
            desktop_companion_context={
                "source": "desktop_voice_companion",
                "voice_interaction_mode": "continuous_listen",
                "voice_raw_stt_text": noise,
                "voice_stt_confidence": 0.72,
                "voice_speaker_verified": True,
            },
        )

    ctx = asyncio.run(run())

    guard = ctx.input_adaptation.desktop_companion_context.get("voice_false_trigger_guard") or {}
    assert guard.get("action") == "drop"
    assert guard.get("should_continue_planning") is False


def test_input_adapter_detects_im_and_hotkey_sources(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.input_adapter import adapt_input_for_cognitive_kernel

    im = adapt_input_for_cognitive_kernel(
        turn_id="turn-im",
        user_input="给 Neil 发消息 内容是 hello",
        channel="lark_im_dispatcher",
        session_id="im-session",
        desktop_companion_context={},
    )
    hotkey = adapt_input_for_cognitive_kernel(
        turn_id="turn-hotkey",
        user_input="打开计算器",
        channel="global_hotkey",
        session_id="hotkey-session",
        desktop_companion_context={},
    )

    assert im.source.value == "im"
    assert hotkey.source.value == "hotkey"
    assert im.modality_evidence["input_adapter"]["source"] == "im"
    assert hotkey.modality_evidence["input_adapter"]["source"] == "hotkey"


def test_low_confidence_voice_asks_raw_to_normalized_clarification() -> None:
    from l3_node.cognitive_kernel.contracts import AgentInputEnvelope, InputSource, RelevantMemoryBundle, StateSnapshot
    from l3_node.cognitive_kernel.review_board import run_review_board

    envelope = AgentInputEnvelope(
        turn_id="turn-low-confidence",
        source=InputSource.VOICE,
        raw_text="open lock",
        normalized_text="open Lark",
        channel="websocket_terminal",
        confidence=0.51,
        modality_evidence={
            "input_adapter": {"source": "voice", "raw_text": "open lock", "normalized_text": "open Lark", "changed": True},
            "voice_language_normalization": {"pending_confirmation_detected": False, "pending_cancellation_detected": False},
        },
    )
    state = StateSnapshot(snapshot_id="state-low-confidence", generated_at_ms=1, freshness_ms=1)
    memory = RelevantMemoryBundle(turn_id=envelope.turn_id)

    summary = run_review_board(envelope=envelope, state_snapshot=state, memory_bundle=memory)

    assert summary.needs_clarification is True
    assert "open lock" in summary.clarification_question
    assert "open Lark" in summary.clarification_question


def test_voice_recent_summary_reference_fills_message_from_memory_and_decomposes() -> None:
    from l3_node.cognitive_kernel.arbiter import arbitrate_review_summary
    from l3_node.cognitive_kernel.contracts import AgentInputEnvelope, InputSource, MemoryEvidence, RelevantMemoryBundle, StateSnapshot
    from l3_node.cognitive_kernel.review_board import run_review_board
    from l3_node.cognitive_kernel.task_decomposer import decompose_task

    envelope = AgentInputEnvelope(
        turn_id="turn-recent-summary",
        source=InputSource.VOICE,
        raw_text="message Neil: that summary",
        normalized_text="message Neil: that summary",
        channel="websocket_terminal",
        confidence=0.91,
        modality_evidence={"input_adapter": {"source": "voice", "raw_text": "message Neil: that summary", "normalized_text": "message Neil: that summary"}},
    )
    state = StateSnapshot(snapshot_id="state-recent-summary", generated_at_ms=1, freshness_ms=1)
    memory = RelevantMemoryBundle(
        turn_id=envelope.turn_id,
        recent_actions=[
            MemoryEvidence(
                memory_id="recent-summary-message",
                memory_type="recent_output",
                source="turn_closure",
                content=json.dumps({"message": "Jachin 最近完成了语音入口统一和 Evidence 链路增强。"}, ensure_ascii=False),
                confidence=0.9,
                confirmed_by_user=True,
            )
        ],
    )

    summary = run_review_board(envelope=envelope, state_snapshot=state, memory_bundle=memory)
    contract = arbitrate_review_summary(summary)
    plan = decompose_task(contract=contract, summary=summary)

    assert summary.task_type == "message_delivery"
    assert summary.target["recipients"] == ["Neil"]
    assert summary.target["message_source"] == "recent_memory"
    assert "语音入口统一" in summary.target["message"]
    assert [node.role_agent for node in plan.nodes] == ["AppControlExecutorAgent", "MessageExecutorAgent"]
    assert all(node.inputs.get("input_context", {}).get("source") == "voice" for node in plan.nodes)


def test_teach_alias_persists_without_touching_synced_lexicon(tmp_path, monkeypatch) -> None:
    import l3_node.voice_entity_correction as vec

    user_aliases = tmp_path / "user" / "voice_user_aliases.json"
    monkeypatch.setenv("JACHIN_VOICE_USER_ALIASES_PATH", str(user_aliases))

    path = teach_alias("contact", "Ada", "eight da")
    data = json.loads(path.read_text(encoding="utf-8"))

    assert path == user_aliases
    assert data["contacts"]["Ada"]["aliases"] == ["eight da"]
    assert data["contacts"]["Ada"]["active"] is True


def test_confirmed_entity_correction_syncs_voice_hotword_alias(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))
    monkeypatch.setenv("JACHIN_VOICE_USER_ALIASES_PATH", str(tmp_path / "user" / "voice_user_aliases.json"))

    import l3_node.voice_entity_correction as vec
    from l3_node.cognitive_kernel.contracts import RiskLevel, ToolPolicy, WorkOrder
    from l3_node.cognitive_kernel.entity_corrections import record_confirmed_entity_correction_from_work_order

    work_order = WorkOrder(
        work_order_id="work-sync-voice-alias",
        decision_id="decision-sync-voice-alias",
        role_agent="AppControlExecutorAgent",
        task="open Lark after voice confirmation",
        inputs={
            "tool": "mcp:windows_open_app",
            "target": {
                "type": "app",
                "name": "Lark",
                "heard_as": "lock",
                "candidate_alias": "lark",
                "requires_entity_confirmation": True,
            },
        },
        tool_policy=ToolPolicy(allowed_tools=["mcp:windows_open_app"], risk_level=RiskLevel.LOW),
    )

    assert record_confirmed_entity_correction_from_work_order(work_order=work_order, turn_id="turn-sync")
    aliases = vec.list_user_aliases()
    assert "lock" in aliases["apps"]["Lark"]["aliases"]
    hotwords = vec.export_hotwords()
    assert hotwords["lock"] >= 5


def test_builtin_voice_hotwords_include_core_mixed_language_entities() -> None:
    from l3_node.voice_entity_correction import export_hotwords

    hotwords = export_hotwords()

    for word in ("Lark", "WeChat", "Codex", "Qwen", "PMO", "Neil", "Vivian", "Jachin"):
        assert word in hotwords
    for alias in ("lock", "lucky", "wechat", "we chat", "code x", "q wen", "p m o", "kneel", "new", "smoke draft"):
        assert hotwords[alias] >= 5


def test_hotword_hits_make_voice_evidence_debuggable() -> None:
    from l3_node.voice_entity_correction import find_hotword_hits

    hits = find_hotword_hits("send hello to new in lock")
    words = {item["word"] for item in hits}

    assert {"new", "lock"}.issubset(words)
    assert any(item["kind"] == "contact" for item in hits)
    assert any(item["kind"] == "app" for item in hits)


def test_voice_language_normalizer_preserves_and_selects_asr_candidates() -> None:
    result = normalize_voice_language_input(
        "open book",
        channel="websocket_terminal",
        voice_context={
            "source": "desktop_voice_companion",
            "voice_raw_stt_text": "open book",
            "voice_stt_confidence": 0.68,
            "voice_asr_alternatives": [
                {"text": "open book", "confidence": 0.68, "source": "top"},
                {"text": "open lock", "confidence": 0.83, "source": "nbest"},
            ],
        },
    )

    assert result.is_voice is True
    assert result.input_text == "open lock"
    assert result.normalized_text == "open Lark"
    assert result.selected_candidate["text"] == "open lock"
    assert result.evidence["asr_candidate_count"] >= 2
    assert result.evidence["selected_candidate_score"] >= 0.9
    assert result.evidence["asr_candidate_scores"]
    assert result.evidence["speaker_trust"] == "unknown"


def test_voice_language_normalizer_preserves_clear_primary_asr_without_hotword_hijack() -> None:
    result = normalize_voice_language_input(
        "open book",
        channel="websocket_terminal",
        voice_context={
            "source": "desktop_voice_companion",
            "voice_raw_stt_text": "open book",
            "voice_stt_confidence": 0.91,
            "voice_asr_alternatives": [
                {"text": "open book", "confidence": 0.91, "source": "top"},
                {"text": "open lock", "confidence": 0.83, "source": "nbest"},
            ],
        },
    )

    assert result.input_text == "open book"
    assert result.normalized_text == "open book"
    assert result.selected_candidate["selection_reason"] == "preserve_high_confidence_primary_asr"
    assert all(not item.get("hotword_used_for_selection") for item in result.evidence["asr_candidate_scores"])


def test_voice_language_normalizer_prefers_neil_candidate_over_generic_mishear() -> None:
    result = normalize_voice_language_input(
        "\u53d1\u9001\u4f60\u597d\u7ed9\u5973\u53cb",
        channel="websocket_terminal",
        voice_context={
            "source": "desktop_voice_companion",
            "voice_raw_stt_text": "\u53d1\u9001\u4f60\u597d\u7ed9\u5973\u53cb",
            "voice_stt_confidence": 0.86,
            "voice_asr_alternatives": [
                {"text": "\u53d1\u9001\u4f60\u597d\u7ed9\u5973\u53cb", "confidence": 0.86},
                {"text": "\u53d1\u9001\u4f60\u597d\u7ed9Neil", "confidence": 0.82},
            ],
        },
    )

    assert result.input_text == "\u53d1\u9001\u4f60\u597d\u7ed9Neil"
    assert result.normalized_text == "\u53d1\u9001\u4f60\u597d\u7ed9Neil"
    assert result.selected_candidate["text"] == "\u53d1\u9001\u4f60\u597d\u7ed9Neil"


def test_voice_language_normalizer_corrects_new_to_neil_in_recipient_slot() -> None:
    result = normalize_voice_language_input(
        "send message hello to new",
        channel="websocket_terminal",
        voice_context={
            "source": "desktop_voice_companion",
            "voice_raw_stt_text": "send message hello to new",
            "voice_stt_confidence": 0.9,
            "voice_speaker_verified": True,
        },
    )

    assert result.normalized_text == "send message hello to Neil"
    assert any(c.kind == "contact" and c.original.lower() == "new" and c.canonical == "Neil" for c in result.correction.corrections)
    assert result.evidence["speaker_trust"] == "owner"
    assert any(item["canonical"] == "Neil" for item in result.evidence.get("learned_aliases", []))


def test_voice_language_normalizer_scores_owner_candidate_and_drops_rejected_candidate_score(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JACHIN_VOICE_USER_ALIASES_PATH", str(tmp_path / "user" / "voice_user_aliases.json"))

    owner_result = normalize_voice_language_input(
        "open book",
        channel="websocket_terminal",
        voice_context={
            "source": "desktop_voice_companion",
            "voice_raw_stt_text": "open book",
            "voice_stt_confidence": 0.81,
            "voice_speaker_verified": True,
            "voice_asr_alternatives": [
                {"text": "open book", "confidence": 0.68},
                {"text": "open lock", "confidence": 0.8},
            ],
        },
    )
    rejected_result = normalize_voice_language_input(
        "open lock",
        channel="websocket_terminal",
        voice_context={
            "source": "desktop_voice_companion",
            "voice_raw_stt_text": "open lock",
            "voice_stt_confidence": 0.86,
            "voice_speaker_rejected": True,
        },
    )

    assert owner_result.normalized_text == "open Lark"
    assert owner_result.evidence["speaker_trust"] == "owner"
    assert owner_result.selected_candidate["speaker_trust"] == "owner"
    assert rejected_result.evidence["speaker_trust"] == "rejected"
    assert rejected_result.selected_candidate["score"] < owner_result.selected_candidate["score"]


def test_voice_review_board_blocks_generic_recipient_but_keeps_message() -> None:
    from l3_node.cognitive_kernel.contracts import AgentInputEnvelope, InputSource, RelevantMemoryBundle, StateSnapshot
    from l3_node.cognitive_kernel.review_board import run_review_board

    envelope = AgentInputEnvelope(
        turn_id="turn-generic-recipient",
        source=InputSource.VOICE,
        raw_text="\u53d1\u9001\u4f60\u597d\u7ed9\u5973\u53cb",
        normalized_text="\u53d1\u9001\u4f60\u597d\u7ed9\u5973\u53cb",
        channel="websocket_terminal",
        confidence=0.82,
        modality_evidence={
            "input_adapter": {
                "source": "voice",
                "raw_text": "\u53d1\u9001\u4f60\u597d\u7ed9\u5973\u53cb",
                "normalized_text": "\u53d1\u9001\u4f60\u597d\u7ed9\u5973\u53cb",
            }
        },
    )

    summary = run_review_board(
        envelope=envelope,
        state_snapshot=StateSnapshot(snapshot_id="state-generic-recipient", generated_at_ms=1, freshness_ms=1),
        memory_bundle=RelevantMemoryBundle(turn_id=envelope.turn_id),
    )

    assert summary.task_type == "message_delivery"
    assert summary.target["recipients"] == []
    assert summary.target["message"] == "\u4f60\u597d"
    assert summary.target["blocked_reason"] == "ambiguous_generic_voice_recipient"
    assert summary.needs_clarification is True
    assert "Neil" in summary.clarification_question


def test_pending_message_slot_reply_emits_task_session_protocol(tmp_path, monkeypatch) -> None:
    import asyncio

    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.direct_mainline import try_execute_cognitive_direct_plan
    from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn
    from l3_node.cognitive_kernel.pipeline import build_cognitive_turn_context

    async def run() -> str:
        text = "\u53d1\u9001\u6d88\u606f\uff0c\u4f60\u597d"
        ctx = await build_cognitive_turn_context(
            run_id="task-session-missing-recipient",
            user_input=text,
            channel="websocket_terminal",
            session_id="task-session-slot",
            prior_messages=[],
            desktop_companion_context={},
        )
        plan = plan_cognitive_turn(ctx, emit_non_execution_closure=False)

        async def fake_tool(tool: str, args: str, allowed_skills: list[str] | None):
            raise AssertionError("slot gap should not execute yet")

        reply = await try_execute_cognitive_direct_plan(
            plan=plan,
            tools=[{"id": "mcp:windows_lark_send_message"}],
            allowed_skills=None,
            run_tool_func=fake_tool,
            user_input=text,
            session_id="task-session-slot",
            channel="websocket_terminal",
        )
        return reply or ""

    reply = asyncio.run(run())

    assert "jachin-ui:pending-confirmation" in reply
    assert "jachin-ui:task-session" in reply
    assert '"status":"waiting_user"' in reply
    assert '"current_step":"\u7b49\u5f85\u7528\u6237\u8865\u5145\u4fe1\u606f"' in reply


def test_adaptive_voice_gate_allows_pending_recipient_choice(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.contracts import DecisionContract, RiskLevel, ToolPolicy, WorkOrder
    from l3_node.cognitive_kernel.pending_confirmation import save_pending_confirmation
    from l3_node.voice_false_trigger_guard import evaluate_voice_false_trigger

    contract = DecisionContract(
        decision_id="decision-pending-recipient-gate",
        turn_id="turn-pending-recipient-gate",
        task_type="message_delivery",
        goal="send message",
        selected_workflow="work_order_role_dispatcher",
        selected_roles=["MessageExecutorAgent"],
        risk_level=RiskLevel.HIGH,
        tool_policy=ToolPolicy(allowed_tools=["mcp:windows_lark_send_message"], risk_level=RiskLevel.HIGH),
        execution_allowed=True,
    )
    work_order = WorkOrder(
        work_order_id="work-pending-recipient-gate",
        decision_id=contract.decision_id,
        role_agent="MessageExecutorAgent",
        task="message_send",
        inputs={
            "tool": "mcp:windows_lark_send_message",
            "target": {"type": "lark_message", "app": "Lark", "recipients": [], "message": "\u4f60\u597d"},
            "work_order_input": json.dumps({"recipients_json": "[]", "message": "\u4f60\u597d", "max_attempts": 2}, ensure_ascii=False),
        },
        tool_policy=ToolPolicy(allowed_tools=["mcp:windows_lark_send_message"], risk_level=RiskLevel.HIGH),
    )
    save_pending_confirmation(contract=contract, work_order=work_order, session_id="voice-slot-gate", channel="websocket_terminal")

    decision = evaluate_voice_false_trigger(
        "A",
        voice_context={
            "source": "desktop_voice_companion",
            "voice_interaction_mode": "continuous_listen",
            "voice_raw_stt_text": "A",
            "voice_stt_confidence": 0.41,
            "voice_speaker_verified": True,
            "session_id": "voice-slot-gate",
            "channel": "websocket_terminal",
        },
        run_id="turn-gate-a",
    )

    assert decision.action == "allow"
    assert decision.reason_code == "pending_task_slot_reply"
    assert decision.evidence["task_session"]["active"] is True


def test_adaptive_voice_gate_ignores_noise_without_clearing_pending_task(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.contracts import DecisionContract, RiskLevel, ToolPolicy, WorkOrder
    from l3_node.cognitive_kernel.pending_confirmation import load_pending_confirmation, save_pending_confirmation
    from l3_node.voice_false_trigger_guard import evaluate_voice_false_trigger

    contract = DecisionContract(
        decision_id="decision-pending-noise-gate",
        turn_id="turn-pending-noise-gate",
        task_type="message_delivery",
        goal="send message",
        selected_workflow="work_order_role_dispatcher",
        selected_roles=["MessageExecutorAgent"],
        risk_level=RiskLevel.HIGH,
        tool_policy=ToolPolicy(allowed_tools=["mcp:windows_lark_send_message"], risk_level=RiskLevel.HIGH),
        execution_allowed=True,
    )
    work_order = WorkOrder(
        work_order_id="work-pending-noise-gate",
        decision_id=contract.decision_id,
        role_agent="MessageExecutorAgent",
        task="message_send",
        inputs={
            "tool": "mcp:windows_lark_send_message",
            "target": {"type": "lark_message", "app": "Lark", "recipients": [], "message": "\u4f60\u597d"},
            "work_order_input": json.dumps({"recipients_json": "[]", "message": "\u4f60\u597d", "max_attempts": 2}, ensure_ascii=False),
        },
        tool_policy=ToolPolicy(allowed_tools=["mcp:windows_lark_send_message"], risk_level=RiskLevel.HIGH),
    )
    save_pending_confirmation(contract=contract, work_order=work_order, session_id="voice-noise-gate", channel="websocket_terminal")

    decision = evaluate_voice_false_trigger(
        "\u884c\uff0c\u5bf9\uff0c\u5c31\u770b\u90a3\u4e2a",
        voice_context={
            "source": "desktop_voice_companion",
            "voice_interaction_mode": "continuous_listen",
            "voice_raw_stt_text": "\u884c\uff0c\u5bf9\uff0c\u5c31\u770b\u90a3\u4e2a",
            "voice_stt_confidence": 0.8,
            "voice_speaker_verified": True,
            "session_id": "voice-noise-gate",
            "channel": "websocket_terminal",
        },
        run_id="turn-gate-noise",
    )

    assert decision.action == "drop"
    assert decision.reason_code in {"pending_task_background_noise_ignored", "background_noise_fragment", "filler_or_backchannel"}
    assert load_pending_confirmation(session_id="voice-noise-gate", channel="websocket_terminal") is not None


def test_adaptive_voice_gate_ignores_background_noise_during_active_task(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.voice_false_trigger_guard import evaluate_voice_false_trigger

    decision = evaluate_voice_false_trigger(
        "\u884c\uff0c\u5bf9\uff0c\u5c31\u770b\u90a3\u4e2a",
        voice_context={
            "source": "desktop_voice_companion",
            "voice_interaction_mode": "continuous_listen",
            "voice_raw_stt_text": "\u884c\uff0c\u5bf9\uff0c\u5c31\u770b\u90a3\u4e2a",
            "voice_stt_confidence": 0.86,
            "voice_speaker_verified": True,
            "voice_owner_track_accepted": True,
            "voice_owner_duration_ms": 900,
            "voice_total_duration_ms": 1000,
            "voice_active_task_context": {
                "active_tasks": [{"id": "task-open-wechat", "title": "open WeChat"}],
                "focused_task_id": "task-open-wechat",
                "source": "desktop_voice_active_task_context",
            },
        },
        run_id="turn-active-task-noise",
    )

    assert decision.action == "drop"
    assert decision.reason_code in {"active_task_background_noise_ignored", "background_noise_fragment", "filler_or_backchannel"}
    assert decision.evidence["active_execution"]["active"] is True


def test_confirmed_low_confidence_voice_correction_writes_unified_memory(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))
    monkeypatch.setenv("JACHIN_VOICE_USER_ALIASES_PATH", str(tmp_path / "user" / "voice_user_aliases.json"))

    from l3_node.cognitive_kernel.contracts import RiskLevel, ToolPolicy, WorkOrder
    from l3_node.cognitive_kernel.entity_corrections import (
        get_learned_app_correction,
        record_confirmed_entity_correction_from_input_context,
    )

    work_order = WorkOrder(
        work_order_id="work-confirm-low-confidence-context",
        decision_id="decision-confirm-low-confidence-context",
        role_agent="AppControlExecutorAgent",
        task="open Lark",
        inputs={
            "tool": "mcp:windows_open_app",
            "target": {"type": "app", "name": "Lark"},
            "input_context": {"source": "voice", "raw_text": "open lock", "normalized_text": "open Lark"},
        },
        tool_policy=ToolPolicy(allowed_tools=["mcp:windows_open_app"], risk_level=RiskLevel.LOW),
    )

    assert record_confirmed_entity_correction_from_input_context(work_order=work_order, turn_id="turn-context-confirm")
    learned = get_learned_app_correction("lock")
    assert learned["name"] == "Lark"
    assert learned["confirmation_count"] >= 1

    result = normalize_voice_language_input(
        "open lock",
        channel="websocket_terminal",
        voice_context={"source": "desktop_voice_companion", "voice_raw_stt_text": "open lock", "voice_stt_confidence": 0.88},
    )
    assert result.normalized_text == "open Lark"
    assert result.evidence["used_user_memory_alias"] is True


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

    monkeypatch.setenv("JACHIN_VOICE_USER_ALIASES_PATH", str(tmp_path / "user" / "voice_user_aliases.json"))

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
