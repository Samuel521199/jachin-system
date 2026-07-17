from __future__ import annotations

import json

from l3_node.mission_intent_schema import MissionTaskType
from l3_node.semantic_slot_parser import parse_mission_intent
from l3_node.voice_entity_correction import correct_voice_entities, teach_alias
from l3_node.voice_language_normalizer import normalize_voice_language_input
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
