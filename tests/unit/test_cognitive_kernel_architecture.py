import asyncio
import json
import re


def _visible_reply(text: str | None) -> str:
    return re.sub(r"\n*\s*<!-- jachin-ui:[\s\S]*?-->\s*$", "", str(text or "")).strip()


def _ctx(text, *, turn_id="ck-arch-1", source=None, active_window=None, risk_state=None, recent_actions=None, confidence=None):
    from l3_node.cognitive_kernel.contracts import (
        AgentInputEnvelope,
        InputSource,
        MemoryEvidence,
        RelevantMemoryBundle,
        StateSnapshot,
        TaskLedgerEntry,
    )
    from l3_node.cognitive_kernel.pipeline import CognitiveTurnContext

    source = source or InputSource.TEXT
    envelope = AgentInputEnvelope(
        turn_id=turn_id,
        source=source,
        raw_text=text,
        normalized_text=text,
        confidence=confidence,
    )
    state = StateSnapshot(
        snapshot_id=f"state-{turn_id}",
        generated_at_ms=1,
        freshness_ms=1,
        active_window=active_window or {},
        risk_state=risk_state or {"unsaved_documents": "unknown"},
    )
    memory = RelevantMemoryBundle(
        turn_id=turn_id,
        recent_actions=[
            MemoryEvidence(
                memory_id=f"mem-{idx}",
                memory_type="short_term_action",
                content=content,
                source="unit_test",
                confidence=0.9,
                relevance_reason="unit-test recent action",
            )
            for idx, content in enumerate(recent_actions or [])
        ],
        confidence=0.8,
    )
    return CognitiveTurnContext(
        envelope=envelope,
        state_snapshot=state,
        memory_bundle=memory,
        ledger_entry=TaskLedgerEntry(
            turn_id=turn_id,
            input_envelope=envelope,
            state_snapshot=state,
            memory_bundle=memory,
        ),
    )


def test_conversation_fallback_reply_is_natural_and_not_internal_error():
    from l3_node.agent_core import _conversation_fallback_reply_from_messages

    greeting_reply = _conversation_fallback_reply_from_messages([{"role": "user", "content": "你好"}])
    vague_reply = _conversation_fallback_reply_from_messages([{"role": "user", "content": "嗯"}])

    combined = greeting_reply + vague_reply
    assert "生成失败" not in combined
    assert "外部操作" not in combined
    assert "没有执行" not in combined
    assert "我在" in greeting_reply
    assert "聊天" in vague_reply or "具体任务" in vague_reply


def test_role_agent_registry_matches_memory_first_design_doc():
    from l3_node.cognitive_kernel.roles import get_default_role_registry

    expected_roles = {
        "AmbiguityResolverAgent",
        "AppAliasResolverAgent",
        "AppClosePlannerAgent",
        "AppControlExecutorAgent",
        "AppControlPlannerAgent",
        "AppLaunchPlannerAgent",
        "AppStateAgent",
        "AuditAgent",
        "BackgroundTaskAgent",
        "BrowserExecutorAgent",
        "CommunicationPlannerAgent",
        "ConfirmationAgent",
        "ConsistencyCheckAgent",
        "ConversationAgent",
        "CorrectionLearningAgent",
        "DesktopStateReadAgent",
        "EntityResolverAgent",
        "FileContextAgent",
        "FileExecutorAgent",
        "IntentAnalystAgent",
        "MemoryRecallAgent",
        "MemoryWriteAgent",
        "MessageExecutorAgent",
        "OsAutomationExecutorAgent",
        "PermissionAgent",
        "PreferenceAgent",
        "PrivacyAgent",
        "RecoveryAgent",
        "RetryPlannerAgent",
        "SafetyAgent",
        "UserFacingReplyAgent",
        "VerificationAgent",
        "VoiceEvidenceAgent",
        "WatcherAgent",
        "WindowContextAgent",
    }
    registry = get_default_role_registry()
    role_ids = {role.role_id for role in registry.list_roles()}

    assert expected_roles <= role_ids
    assert registry.get("SafetyAgent").permission_scope == "veto_and_confirmation_only"
    assert registry.get("AppControlExecutorAgent").permission_scope == "execute_only_with_work_order"
    assert registry.get("MemoryRecallAgent").can_execute_external_world is False
    assert registry.get("MemoryRecallAgent").requires_work_order is True
    assert registry.get("MemoryWriteAgent").can_execute_external_world is True
    assert registry.select_for_tool("core:local_memory_search").role_id == "MemoryRecallAgent"
    assert registry.select_for_tool("mcp:browser_click").role_id == "BrowserExecutorAgent"
    assert registry.select_for_tool("mcp:windows_window_close").role_id == "AppControlExecutorAgent"


def test_cognitive_kernel_prompt_is_doc_aligned_main_agent_boundary():
    from l3_node.cognitive_kernel.kernel_prompts import (
        build_cognitive_kernel_system_prompt,
        build_role_execution_system_prefix,
        build_user_facing_reply_agent_system_prompt,
    )

    kernel_prompt = build_cognitive_kernel_system_prompt()
    text_role_prompt = build_role_execution_system_prefix()
    reply_prompt = build_user_facing_reply_agent_system_prompt()

    assert "你是 Jachin 的认知内核" in kernel_prompt
    assert "不能直接调用会改变外部世界的工具" in kernel_prompt
    assert "DecisionContract -> WorkOrder" in kernel_prompt
    assert "VerificationAgent" in kernel_prompt
    assert "TurnClosure" in kernel_prompt

    assert "RoleExecutionAgent" in text_role_prompt
    assert "不是认知内核本身" in text_role_prompt
    assert "工具调用会被宿主转换为 DecisionContract -> WorkOrder" in text_role_prompt

    assert "UserFacingReplyAgent" in reply_prompt
    assert "不声称执行了未授权" in reply_prompt


def test_cognitive_turn_context_prompt_block_contains_kernel_system_prompt():
    block = _ctx("open calculator").prompt_block(max_chars=12000)

    assert "[Cognitive Kernel Context]" in block
    assert "memory_first_cognitive_kernel" in block
    assert "你是 Jachin 的认知内核" in block
    assert "External actions require DecisionContract and WorkOrder" in block


def test_review_board_uses_document_role_chain_for_short_close(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn

    result = plan_cognitive_turn(
        _ctx(
            "close",
            turn_id="ck-arch-doc-close-chain",
            active_window={"app_name": "Calculator", "title": "Calculator"},
            recent_actions=['{"target_name":"Calculator","execution_status":"success"}'],
        )
    )
    review_roles = {review.role_id for review in result.review_summary.reviews}
    selected_roles = set(result.decision_contract.selected_roles)

    assert {
        "MemoryRecallAgent",
        "DesktopStateReadAgent",
        "WindowContextAgent",
        "AppStateAgent",
        "AppClosePlannerAgent",
        "SafetyAgent",
        "PermissionAgent",
        "ConfirmationAgent",
    } <= review_roles
    assert {
        "AppControlExecutorAgent",
        "VerificationAgent",
        "AuditAgent",
        "RecoveryAgent",
        "RetryPlannerAgent",
        "MemoryWriteAgent",
        "UserFacingReplyAgent",
    } <= selected_roles
    assert result.work_orders[0].role_agent == "AppControlExecutorAgent"


def test_mainline_open_app_review_to_work_order(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn

    result = plan_cognitive_turn(_ctx("open calculator", turn_id="ck-arch-open"))

    assert result.review_summary.top_intent == "open_app"
    assert result.review_summary.target["name"] == "Calculator"
    assert result.decision_contract.task_type == "app_control"
    assert result.decision_contract.selected_workflow == "reviewed_app_control_workflow"
    assert result.decision_contract.execution_allowed is True
    assert result.work_orders
    assert result.work_orders[0].role_agent == "AppControlExecutorAgent"
    assert result.work_orders[0].inputs["tool"] == "mcp:windows_open_app"


def test_mainline_calculator_expression_review_to_work_order(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn

    result = plan_cognitive_turn(_ctx("打开计算器，计算99+100等于多少", turn_id="ck-arch-calc-expression"))

    assert result.review_summary.top_intent == "calculator_calculate"
    assert result.review_summary.target["name"] == "Calculator"
    assert result.review_summary.target["expression"] == "99+100"
    assert result.decision_contract.task_type == "calculator_calculate"
    assert result.decision_contract.selected_workflow == "reviewed_calculator_calculate_workflow"
    assert result.decision_contract.execution_allowed is True
    assert len(result.work_orders) == 2
    assert [wo.inputs["tool"] for wo in result.work_orders] == ["mcp:windows_open_app", "mcp:windows_calculator_calculate"]
    assert result.work_orders[0].inputs["decomposition_role"] == "prepare_calculator"
    assert result.work_orders[1].inputs["decomposition_role"] == "calculate_expression"
    assert result.work_orders[1].inputs["depends_on"] == [result.work_orders[0].inputs["decomposition_node_id"]]
    payload = json.loads(result.work_orders[1].inputs["work_order_input"])
    assert payload["expression"] == "99+100"
    assert result.task_dag is not None
    assert len(result.task_dag.nodes) == 2
    assert result.task_dag.nodes[1].depends_on == [result.task_dag.nodes[0].node_id]
    assert result.task_dag.nodes[1].recovery_policy["strategy"] == "clear_and_retype_expression"


def test_mainline_open_wechat_review_to_work_order(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn

    result = plan_cognitive_turn(_ctx("\u6253\u5f00\u5fae\u4fe1", turn_id="ck-arch-open-wechat"))

    assert result.review_summary.top_intent == "open_app"
    assert result.review_summary.target["name"] == "WeChat"
    assert result.decision_contract.task_type == "app_control"
    assert result.decision_contract.execution_allowed is True
    assert result.work_orders
    assert result.work_orders[0].role_agent == "AppControlExecutorAgent"
    assert result.work_orders[0].inputs["tool"] == "mcp:windows_open_app"


def test_mainline_open_common_apps_review_to_work_order(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn

    cases = [
        ("\u6253\u5f00\u5fae\u4fe1", "WeChat"),
        ("\u6253\u5f00\u9489\u9489", "DingTalk"),
        ("\u6253\u5f00\u4f01\u4e1a\u5fae\u4fe1", "WeCom"),
        ("\u6253\u5f00\u817e\u8baf\u4f1a\u8bae", "TencentMeeting"),
        ("\u6253\u5f00\u6d4f\u89c8\u5668", "Browser"),
        ("open Chrome", "Chrome"),
        ("open Edge", "Edge"),
        ("open Firefox", "Firefox"),
        ("open Cursor", "Cursor"),
        ("open VS Code", "VSCode"),
        ("open WPS", "WPS"),
        ("open Word", "Word"),
        ("open Excel", "Excel"),
        ("open PowerPoint", "PowerPoint"),
        ("open Notion", "Notion"),
        ("open Obsidian", "Obsidian"),
    ]
    for text, expected in cases:
        result = plan_cognitive_turn(_ctx(text, turn_id=f"ck-arch-open-{expected.lower()}"))
        assert result.review_summary.top_intent == "open_app"
        assert result.review_summary.target["name"] == expected
        assert result.decision_contract.task_type == "app_control"
        assert result.work_orders
        assert result.work_orders[0].role_agent == "AppControlExecutorAgent"
        assert result.work_orders[0].inputs["tool"] == "mcp:windows_open_app"


def test_mainline_short_close_resolves_active_window(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn

    result = plan_cognitive_turn(
        _ctx(
            "close",
            turn_id="ck-arch-close-active",
            active_window={"app_name": "Calculator", "title": "Calculator"},
        )
    )

    assert result.review_summary.top_intent == "close_app"
    assert result.review_summary.target == {"type": "app", "name": "Calculator", "source": "active_window"}
    assert result.decision_contract.execution_allowed is True
    assert result.work_orders[0].inputs["target"]["name"] == "Calculator"
    assert result.work_orders[0].inputs["tool"] == "mcp:windows_window_close"


def test_mainline_short_close_prefers_recent_app_when_active_is_control_surface(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn

    result = plan_cognitive_turn(
        _ctx(
            "\u5173\u95ed",
            turn_id="ck-arch-close-recent-browser",
            active_window={"app_name": "Jachin", "title": "Jachin Console"},
            recent_actions=['{"task":"open_app","target_app":"Browser"}'],
        )
    )

    assert result.review_summary.top_intent == "close_app"
    assert result.review_summary.target == {"type": "app", "name": "Browser", "source": "recent_action_memory"}
    assert result.decision_contract.execution_allowed is True
    assert result.work_orders[0].inputs["target"]["name"] == "Browser"
    assert result.work_orders[0].inputs["tool"] == "mcp:windows_window_close"


def test_mainline_short_close_uses_latest_recent_app_not_older_one(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn

    result = plan_cognitive_turn(
        _ctx(
            "\u5173\u95ed",
            turn_id="ck-arch-close-latest-app",
            active_window={"app_name": "Jachin", "title": "Jachin Console"},
            recent_actions=[
                '{"task":"open_app","target_app":"Browser"}',
                '{"task":"open_app","target_app":"WeChat"}',
            ],
        )
    )

    assert result.review_summary.top_intent == "close_app"
    assert result.review_summary.target == {"type": "app", "name": "WeChat", "source": "recent_action_memory"}
    assert result.decision_contract.execution_allowed is True
    assert result.work_orders[0].inputs["target"]["name"] == "WeChat"
    assert result.work_orders[0].inputs["tool"] == "mcp:windows_window_close"


def test_mainline_message_review_to_work_order(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn

    result = plan_cognitive_turn(_ctx("send to Neil: hello", turn_id="ck-arch-message"))

    assert result.review_summary.top_intent == "message_send"
    assert result.decision_contract.task_type == "message_delivery"
    assert len(result.work_orders) == 2
    assert [wo.role_agent for wo in result.work_orders] == ["AppControlExecutorAgent", "MessageExecutorAgent"]
    assert [wo.inputs["tool"] for wo in result.work_orders] == ["mcp:windows_open_app", "mcp:windows_lark_send_message"]
    assert result.work_orders[1].inputs["depends_on"] == [result.work_orders[0].inputs["decomposition_node_id"]]
    payload = json.loads(result.work_orders[1].inputs["work_order_input"])
    assert json.loads(payload["recipients_json"]) == ["Neil"]
    assert payload["message"] == "hello"
    assert result.task_dag is not None
    assert len(result.task_dag.nodes) == 2
    assert result.task_dag.nodes[0].capability == "app_control.open_or_focus"
    assert result.task_dag.nodes[1].capability == "message.send"


def test_mainline_message_review_handles_xiang_typo_to_work_order(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn

    result = plan_cognitive_turn(_ctx("打开lark像Neil发送一条消息，内容为你好", turn_id="ck-arch-message-xiang"))

    assert result.review_summary.top_intent == "message_send"
    assert result.decision_contract.task_type == "message_delivery"
    assert len(result.work_orders) == 2
    assert result.work_orders[1].role_agent == "MessageExecutorAgent"
    assert result.work_orders[1].inputs["tool"] == "mcp:windows_lark_send_message"
    payload = json.loads(result.work_orders[1].inputs["work_order_input"])
    assert json.loads(payload["recipients_json"]) == ["Neil"]
    assert payload["message"] == "你好"


def test_mainline_lark_voice_spelled_alias_open_app_to_work_order(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn

    result = plan_cognitive_turn(_ctx("打开 L A R K", turn_id="ck-arch-open-lark-spelled"))

    assert result.review_summary.top_intent == "open_app"
    assert result.review_summary.target["name"] == "Lark"
    assert result.decision_contract.execution_allowed is True
    assert result.work_orders
    assert result.work_orders[0].role_agent == "AppControlExecutorAgent"
    assert result.work_orders[0].inputs["tool"] == "mcp:windows_open_app"


def test_mainline_lark_open_app_real_chinese_is_not_message_delivery(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn

    for text in ("\u6253\u5f00 Lark", "\u6253\u5f00lark", "open lark"):
        result = plan_cognitive_turn(_ctx(text, turn_id=f"ck-arch-open-lark-{abs(hash(text))}"))

        assert result.review_summary.top_intent == "open_app"
        assert result.review_summary.task_type == "app_control"
        assert result.review_summary.target["name"] == "Lark"
        assert result.review_summary.needs_clarification is False
        assert result.decision_contract.execution_allowed is True
        assert [wo.inputs["tool"] for wo in result.work_orders] == ["mcp:windows_open_app"]


def test_mainline_lark_open_then_send_real_chinese_stays_message_delivery(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn

    result = plan_cognitive_turn(
        _ctx("\u6253\u5f00lark\u7ed9Neil\u53d1\u9001\u4f60\u597d", turn_id="ck-arch-open-lark-send-real-cn")
    )

    assert result.review_summary.top_intent == "message_send"
    assert result.review_summary.task_type == "message_delivery"
    assert result.review_summary.target["app"] == "Lark"
    assert result.review_summary.target["recipients"] == ["Neil"]
    assert result.review_summary.target["message"] == "\u4f60\u597d"
    assert [wo.inputs["tool"] for wo in result.work_orders] == ["mcp:windows_open_app", "mcp:windows_lark_send_message"]


def test_mainline_lark_voice_spelled_alias_message_to_work_order(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn

    result = plan_cognitive_turn(_ctx("打开 L，A，R，K 给 Neil 发送你好", turn_id="ck-arch-message-lark-spelled"))

    assert result.review_summary.top_intent == "message_send"
    assert result.review_summary.target["app"] == "Lark"
    assert result.review_summary.target["recipients"] == ["Neil"]
    assert result.review_summary.target["message"] == "你好"
    assert result.decision_contract.execution_allowed is True
    assert len(result.work_orders) == 2
    assert result.work_orders[0].role_agent == "AppControlExecutorAgent"
    assert result.work_orders[1].role_agent == "MessageExecutorAgent"
    assert result.work_orders[1].inputs["tool"] == "mcp:windows_lark_send_message"
    payload = json.loads(result.work_orders[1].inputs["work_order_input"])
    assert json.loads(payload["recipients_json"]) == ["Neil"]
    assert payload["message"] == "你好"


def test_mainline_lark_message_missing_slots_asks_clarification_before_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn

    result = plan_cognitive_turn(_ctx("打开 L A R K 发送消息", turn_id="ck-arch-message-lark-missing-slots"))

    assert result.review_summary.top_intent == "message_send"
    assert result.review_summary.target["app"] == "Lark"
    assert result.review_summary.needs_clarification is True
    assert "发给谁" in result.review_summary.clarification_question or "什么内容" in result.review_summary.clarification_question
    assert result.decision_contract.execution_allowed is False
    assert result.decision_contract.tool_policy.allowed_tools == ["mcp:windows_lark_send_message"]
    assert result.work_orders
    assert result.work_orders[-1].role_agent == "MessageExecutorAgent"
    assert result.closure is not None
    assert result.closure.final_user_message_intent == "ask_clarification"


def test_mainline_lark_asr_lock_candidate_requires_user_confirmation(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn

    result = plan_cognitive_turn(_ctx("打开 lock", turn_id="ck-arch-open-lock-candidate"))

    assert result.review_summary.top_intent == "open_app"
    assert result.review_summary.target["name"] == "Lark"
    assert result.review_summary.target["source"] == "entity_correction_candidate"
    assert result.review_summary.target["heard_as"] == "lock"
    assert result.review_summary.needs_clarification is True
    assert "是不是想操作 Lark" in result.review_summary.clarification_question
    assert result.decision_contract.execution_allowed is False
    assert result.decision_contract.tool_policy.requires_confirmation is True
    assert result.work_orders
    assert result.work_orders[0].role_agent == "AppControlExecutorAgent"
    assert result.work_orders[0].inputs["tool"] == "mcp:windows_open_app"
    assert result.closure is not None
    assert result.closure.final_user_message_intent == "ask_clarification"


def test_mainline_lark_asr_lock_message_candidate_preserves_slots_for_resume(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn

    result = plan_cognitive_turn(_ctx("打开 lock 给 Neil 发送你好", turn_id="ck-arch-message-lock-candidate"))

    assert result.review_summary.top_intent == "message_send"
    assert result.review_summary.target["app"] == "Lark"
    assert result.review_summary.target["source"] == "entity_correction_candidate"
    assert result.review_summary.target["recipients"] == ["Neil"]
    assert result.review_summary.target["message"] == "你好"
    assert result.decision_contract.execution_allowed is False
    assert result.decision_contract.tool_policy.requires_confirmation is True
    assert len(result.work_orders) == 2
    assert result.work_orders[0].role_agent == "AppControlExecutorAgent"
    assert result.work_orders[1].role_agent == "MessageExecutorAgent"
    assert result.work_orders[1].inputs["tool"] == "mcp:windows_lark_send_message"
    payload = json.loads(result.work_orders[1].inputs["work_order_input"])
    assert json.loads(payload["recipients_json"]) == ["Neil"]
    assert payload["message"] == "你好"


def test_pending_confirmation_accepts_plain_chinese_yes_no():
    from l3_node.cognitive_kernel.pending_confirmation import is_cancellation_text, is_confirmation_text

    assert is_confirmation_text("是")
    assert is_confirmation_text("是。")
    assert is_confirmation_text("是的")
    assert is_confirmation_text("对")
    assert is_cancellation_text("否")
    assert is_cancellation_text("否。")
    assert is_cancellation_text("不是")


def test_review_board_exposes_capability_semantic_candidates(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn

    result = plan_cognitive_turn(_ctx("open browser", turn_id="ck-arch-semantic-browser"))

    assert result.review_summary.semantic_candidates
    assert result.review_summary.capability_candidates
    assert any(c.get("target_patch", {}).get("name") in {"Browser", "Chrome", "Edge"} for c in result.review_summary.semantic_candidates)
    assert any(c.get("capability_id") or c.get("descriptor") for c in result.review_summary.capability_candidates)


def test_manifest_capability_registry_contributes_skill_descriptors():
    from l3_node.capability_semantic_registry import build_capability_registry

    registry = build_capability_registry([])
    ids = {item.id for item in registry}

    assert "com.jachin.skill.english-learning-assistant" in ids
    assert "com.jachin.skill.pmo-copilot" in ids


def test_semantic_candidates_rank_lock_to_lark(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn

    result = plan_cognitive_turn(_ctx("open lock", turn_id="ck-arch-semantic-lock"))

    candidates = result.review_summary.semantic_candidates
    assert candidates
    assert candidates[0]["target_patch"]["name"] == "Lark"
    assert candidates[0]["source"] == "entity_correction_candidate"
    assert result.review_summary.target["name"] == "Lark"


def test_task_decomposer_uses_manifest_metadata_decomposition():
    from l3_node.cognitive_kernel.contracts import DecisionContract, ReviewSummary, RiskLevel, ToolPolicy
    from l3_node.cognitive_kernel.task_decomposer import decompose_task

    summary = ReviewSummary(
        review_session_id="review-metadata",
        turn_id="turn-metadata",
        top_intent="open_app",
        task_type="app_control",
        target={"type": "app", "name": "Chrome", "source": "unit_test"},
        selected_roles=["AppControlExecutorAgent"],
        candidate_tools=["mcp:windows_open_app"],
        risk_level=RiskLevel.LOW,
        capability_candidates=[
            {
                "capability_id": "com.example.browser.open",
                "descriptor": {
                    "id": "com.example.browser.open",
                    "metadata": {
                        "decomposition": {
                            "nodes": [
                                {
                                    "id": "open_app",
                                    "goal": "Open $target.name from metadata",
                                    "role_agent": "AppControlExecutorAgent",
                                    "tool": "mcp:windows_open_app",
                                    "capability": "app_control.open_or_focus",
                                    "inputs": {"target": {"type": "app", "name": "$target.name"}},
                                    "work_order_input": {"app": "$target.name"},
                                    "verification_criteria": ["$target.name window is visible"],
                                    "recovery_policy": {"strategy": "metadata_retry", "max_attempts": 2},
                                }
                            ]
                        }
                    },
                },
            }
        ],
    )
    contract = DecisionContract(
        decision_id="decision-metadata",
        turn_id="turn-metadata",
        task_type="app_control",
        goal="open Chrome",
        selected_roles=["AppControlExecutorAgent"],
        risk_level=RiskLevel.LOW,
        tool_policy=ToolPolicy(allowed_tools=["mcp:windows_open_app"], risk_level=RiskLevel.LOW),
        execution_allowed=True,
        verification_criteria=["window visible"],
    )

    plan = decompose_task(contract=contract, summary=summary)

    assert len(plan.nodes) == 1
    assert plan.nodes[0].goal == "Open Chrome from metadata"
    assert plan.nodes[0].inputs["target"]["name"] == "Chrome"
    assert json.loads(plan.nodes[0].work_order_input)["app"] == "Chrome"
    assert plan.nodes[0].recovery_policy["strategy"] == "metadata_retry"
    assert "capability metadata decomposition" in plan.rationale[0]


def test_mainline_file_read_open_reveal_and_mutating_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn

    read_plan = plan_cognitive_turn(_ctx("read file README.md", turn_id="ck-arch-file-read"))
    assert read_plan.work_orders[0].inputs["tool"] == "core:fs_read"

    open_plan = plan_cognitive_turn(_ctx("open file README.md", turn_id="ck-arch-file-open"))
    assert open_plan.work_orders[0].inputs["tool"] == "mcp:windows_file_open"

    reveal_plan = plan_cognitive_turn(_ctx("reveal file README.md in explorer", turn_id="ck-arch-file-reveal"))
    assert reveal_plan.work_orders[0].inputs["tool"] == "mcp:windows_file_reveal_in_explorer"

    mutating_plan = plan_cognitive_turn(_ctx("delete file README.md", turn_id="ck-arch-file-delete"))
    assert mutating_plan.decision_contract.execution_allowed is False
    assert mutating_plan.decision_contract.tool_policy.requires_confirmation is True
    assert mutating_plan.work_orders
    assert mutating_plan.work_orders[0].status == "pending"

    write_plan = plan_cognitive_turn(_ctx('write file notes/today.txt content is "hello"', turn_id="ck-arch-file-write"))
    assert write_plan.work_orders[0].inputs["tool"] == "core:fs_write"
    assert write_plan.decision_contract.execution_allowed is False
    assert write_plan.decision_contract.tool_policy.requires_confirmation is True
    payload = json.loads(write_plan.work_orders[0].inputs["work_order_input"])
    assert payload["path"] == "notes/today.txt"
    assert payload["content"] == "hello"


def test_mainline_appcontrol_existing_work_order_executes_direct_adapter(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    from l3_client.local_mcps.windows_uia_mcp import server as windows_uia_server
    from l3_node.cognitive_kernel.dispatcher import dispatch_existing_work_order
    from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn

    calls = {"open": 0}

    def fake_open_app(app_name: str, args_json: str = "[]", out_dir: str = "") -> str:
        calls["open"] += 1
        return json.dumps(
            {
                "task": "windows_open_app",
                "ok": True,
                "detail": "window_opened",
                "evidence": {"active_window": app_name, "screenshot": "C:/tmp/calc.png"},
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(windows_uia_server, "windows_open_app", fake_open_app)
    plan = plan_cognitive_turn(_ctx("open calculator", turn_id="ck-arch-direct-open"), emit_non_execution_closure=False)

    async def _run():
        async def transport_should_not_run(_work_order):
            raise AssertionError("AppControlExecutor should use direct Windows UIA channel")

        result = await dispatch_existing_work_order(
            contract=plan.decision_contract,
            work_order=plan.work_orders[0],
            executor=transport_should_not_run,
        )
        assert result.verification.ok is True
        assert calls["open"] == 1

    asyncio.run(_run())


def test_mainline_calculator_direct_entry_executes_calculate_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    from l3_node.cognitive_kernel.direct_mainline import try_execute_cognitive_direct_plan
    from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn

    plan = plan_cognitive_turn(_ctx("打开计算器，计算99+100等于多少", turn_id="ck-arch-direct-calc"))
    assert [wo.inputs["tool"] for wo in plan.work_orders] == ["mcp:windows_open_app", "mcp:windows_calculator_calculate"]
    calls = {"run_tool": 0, "tools": []}

    def fake_run_tool(tool_id: str, work_order_input: str, allowed_skills=None):
        calls["run_tool"] += 1
        calls["tools"].append(tool_id)
        if tool_id == "mcp:windows_open_app":
            return json.dumps({"ok": True, "app": "Calculator", "detail": "window_opened"}, ensure_ascii=False)
        assert tool_id == "mcp:windows_calculator_calculate"
        payload = json.loads(work_order_input)
        assert payload["expression"] == "99+100"
        return json.dumps(
            {
                "task": "calculator",
                "ok": True,
                "detail": "result_verified_with_visual",
                "evidence": {"expr": "99+100", "expect": "199", "clipboard_norm": "199"},
            },
            ensure_ascii=False,
        )

    async def _run():
        reply = await try_execute_cognitive_direct_plan(
            plan=plan,
            tools=[{"id": "mcp:windows_open_app"}, {"id": "mcp:windows_calculator_calculate"}],
            allowed_skills=None,
            run_tool_func=fake_run_tool,
        )
        assert reply == "已用计算器计算：99+100=199。"
        assert calls["tools"] == ["mcp:windows_calculator_calculate"]

    asyncio.run(_run())


def test_mainline_message_direct_entry_uses_work_order_dispatcher(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))
    monkeypatch.setenv("JACHIN_DISABLE_ROLE_NATIVE_ADAPTERS", "1")

    from l3_node.cognitive_kernel.direct_mainline import try_execute_cognitive_direct_plan
    from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn

    plan = plan_cognitive_turn(_ctx("send to Neil: hello from direct mainline", turn_id="ck-arch-direct-message"))
    assert [wo.inputs["tool"] for wo in plan.work_orders] == ["mcp:windows_open_app", "mcp:windows_lark_send_message"]
    calls = {"run_tool": 0, "tools": []}

    def fake_run_tool(tool_id: str, work_order_input: str, allowed_skills=None):
        calls["run_tool"] += 1
        calls["tools"].append(tool_id)
        if tool_id == "mcp:windows_open_app":
            return json.dumps({"ok": True, "app": "Lark", "detail": "window_opened"}, ensure_ascii=False)
        assert tool_id == "mcp:windows_lark_send_message"
        payload = json.loads(work_order_input)
        assert json.loads(payload["recipients_json"]) == ["Neil"]
        assert payload["message"] == "hello from direct mainline"
        return json.dumps({"ok": True, "send_ok": True, "recipient": "Neil", "message_id": "m1"}, ensure_ascii=False)

    async def _run():
        reply = await try_execute_cognitive_direct_plan(
            plan=plan,
            tools=[{"id": "mcp:windows_open_app"}, {"id": "mcp:windows_lark_send_message"}],
            allowed_skills=None,
            run_tool_func=fake_run_tool,
        )
        assert _visible_reply(reply) == "已发送消息给 Neil。"
        assert calls["tools"] == ["mcp:windows_open_app", "mcp:windows_lark_send_message"]

    asyncio.run(_run())


def test_mainline_message_direct_entry_rejects_unverified_tool_success(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))
    monkeypatch.setenv("JACHIN_DISABLE_ROLE_NATIVE_ADAPTERS", "1")

    from l3_node.cognitive_kernel.direct_mainline import try_execute_cognitive_direct_plan
    from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn

    plan = plan_cognitive_turn(_ctx("打开lark像Neil发送一条消息，内容为你好", turn_id="ck-arch-direct-message-unverified"))
    calls = {"run_tool": 0}

    def fake_run_tool(tool_id: str, work_order_input: str, allowed_skills=None):
        calls["run_tool"] += 1
        if tool_id == "mcp:windows_open_app":
            return json.dumps({"ok": True, "app": "Lark", "detail": "window_opened"}, ensure_ascii=False)
        assert tool_id == "mcp:windows_lark_send_message"
        return json.dumps({"ok": True}, ensure_ascii=False)

    async def _run():
        reply = await try_execute_cognitive_direct_plan(
            plan=plan,
            tools=[{"id": "mcp:windows_open_app"}, {"id": "mcp:windows_lark_send_message"}],
            allowed_skills=None,
            run_tool_func=fake_run_tool,
        )
        assert calls["run_tool"] >= 1
        assert reply is not None
        assert "已发送消息" not in reply
        assert "没有通过验证" in reply
        assert "message_post_send_verification_missing" in reply

    asyncio.run(_run())


def test_run_tool_delegates_mcp_tools_to_registry(monkeypatch):
    from l3_node.primitives.tools import loader
    import l3_node.primitives.mcp.registry as registry

    calls = {}

    class FakeRegistry:
        async def invoke(self, tool_id, work_order_input, *, allowed_skills=None, **_kwargs):
            calls["tool_id"] = tool_id
            calls["work_order_input"] = work_order_input
            calls["allowed_skills"] = allowed_skills
            return json.dumps(
                {
                    "ok": True,
                    "send_ok": True,
                    "message_id": "registry-dispatch-1",
                    "via": "fake_mcp_registry",
                },
                ensure_ascii=False,
            )

    monkeypatch.setattr(registry, "get_mcp_registry", lambda: FakeRegistry())

    out = loader.run_tool(
        "mcp:windows_lark_send_message",
        json.dumps({"recipients_json": "[\"Neil\"]", "message": "hello"}, ensure_ascii=False),
        allowed_skills=None,
    )

    assert calls["tool_id"] == "mcp:windows_lark_send_message"
    assert json.loads(calls["work_order_input"])["message"] == "hello"
    assert json.loads(out)["via"] == "fake_mcp_registry"


def test_mainline_file_direct_entry_uses_file_executor(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    import core.native_tools as native_tools
    from l3_node.cognitive_kernel.direct_mainline import try_execute_cognitive_direct_plan
    from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn

    plan = plan_cognitive_turn(_ctx("read file README.md", turn_id="ck-arch-direct-file"))

    def fake_fs_read(path: str):
        assert path == "README.md"
        return "readme content from FileExecutor"

    def transport_should_not_run(*_args, **_kwargs):
        raise AssertionError("FileExecutorAgent should not call run_tool for direct core:fs_read")

    monkeypatch.setattr(native_tools, "core_fs_read", fake_fs_read)

    async def _run():
        reply = await try_execute_cognitive_direct_plan(
            plan=plan,
            tools=[{"id": "core:fs_read"}],
            allowed_skills=None,
            run_tool_func=transport_should_not_run,
        )
        assert _visible_reply(reply) == "已完成文件操作：README.md。"

    asyncio.run(_run())


def test_direct_mainline_confirmation_without_pending_falls_through(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    from l3_node.cognitive_kernel.direct_mainline import try_execute_cognitive_direct_plan

    async def _run():
        reply = await try_execute_cognitive_direct_plan(
            plan=None,
            tools=[],
            allowed_skills=None,
            run_tool_func=lambda *_args, **_kwargs: "",
            user_input="确认执行",
            session_id="no-new-kernel-pending",
            channel="websocket_terminal",
        )
        assert reply is None

    asyncio.run(_run())


def test_message_executor_dedupe_skips_repeat_send(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    from l3_node.cognitive_kernel.dispatcher import dispatch_existing_work_order
    from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn

    plan = plan_cognitive_turn(_ctx("send to Neil: hello once", turn_id="ck-arch-dedupe"))
    calls = {"send": 0}

    async def fake_send(_work_order):
        calls["send"] += 1
        return json.dumps({"ok": True, "send_ok": True, "message_id": f"m{calls['send']}"}, ensure_ascii=False)

    async def _run():
        first = await dispatch_existing_work_order(
            contract=plan.decision_contract,
            work_order=plan.work_orders[1],
            executor=fake_send,
        )
        second = await dispatch_existing_work_order(
            contract=plan.decision_contract,
            work_order=plan.work_orders[1],
            executor=fake_send,
        )
        assert first.verification.ok is True
        assert second.verification.ok is False
        assert second.verification.failure_reason == "message_duplicate_skipped_without_new_send"
        assert calls["send"] == 1
        assert "duplicate_skipped" in second.observation

    asyncio.run(_run())


def test_run_agent_message_direct_mainline_bypasses_role_execution_transport(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))
    monkeypatch.setenv("JACHIN_DISABLE_ROLE_NATIVE_ADAPTERS", "1")

    import l3_node.agent_core as agent_core

    async def fake_build_context(**kwargs):
        return _ctx(kwargs["user_input"], turn_id=kwargs["run_id"])

    async def fake_assemble_tool_pool(*_args, **_kwargs):
        return [
            {"id": "mcp:windows_open_app", "name": "windows_open_app"},
            {"id": "mcp:windows_lark_send_message", "name": "windows_lark_send_message"},
        ]

    def fake_run_tool(tool_id: str, work_order_input: str, allowed_skills=None):
        if tool_id == "mcp:windows_open_app":
            return json.dumps({"ok": True, "app": "Lark", "detail": "window_opened"}, ensure_ascii=False)
        assert tool_id == "mcp:windows_lark_send_message"
        payload = json.loads(work_order_input)
        assert json.loads(payload["recipients_json"]) == ["Neil"]
        assert payload["message"] == "hello from run_agent"
        return json.dumps({"ok": True, "send_ok": True, "message_id": "run-agent-1"}, ensure_ascii=False)

    async def text_transport_core_should_not_run(*_args, **_kwargs):
        raise AssertionError("run_agent should return from Cognitive Kernel direct mainline before text transport core")

    monkeypatch.setattr(agent_core, "build_cognitive_turn_context", fake_build_context)
    monkeypatch.setattr(agent_core, "assemble_tool_pool", fake_assemble_tool_pool)
    monkeypatch.setattr(agent_core, "run_tool", fake_run_tool)

    reply = asyncio.run(
        agent_core.run_agent(
            "send to Neil: hello from run_agent",
            object(),
            max_iterations=1,
        )
    )

    assert _visible_reply(reply) == "已发送消息给 Neil。"


def test_run_agent_appcontrol_direct_mainline_open_switch_close(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    import l3_node.agent_core as agent_core
    from l3_client.local_mcps.windows_uia_mcp import server as windows_uia_server

    async def fake_assemble_tool_pool(*_args, **_kwargs):
        return [
            {"id": "mcp:windows_open_app", "name": "windows_open_app"},
            {"id": "mcp:windows_window_switch", "name": "windows_window_switch"},
            {"id": "mcp:windows_window_close", "name": "windows_window_close"},
        ]

    async def text_transport_core_should_not_run(*_args, **_kwargs):
        raise AssertionError("AppControl direct mainline should bypass text transport core")

    def fake_open_app(app_name: str, args_json: str = "[]", out_dir: str = "") -> str:
        return json.dumps({"ok": True, "active_window": app_name, "screenshot": "C:/tmp/open.png"}, ensure_ascii=False)

    def fake_switch_window(keywords: str, exclude_keywords: str = "", timeout: float = 5.0, out_dir: str = "") -> str:
        return json.dumps({"ok": True, "active_window": keywords, "screenshot": "C:/tmp/switch.png"}, ensure_ascii=False)

    def fake_close_window(keywords: str, exclude_keywords: str = "", timeout: float = 5.0, out_dir: str = "") -> str:
        return json.dumps({"ok": True, "window_closed": keywords, "still_exists": False}, ensure_ascii=False)

    monkeypatch.setattr(agent_core, "assemble_tool_pool", fake_assemble_tool_pool)
    monkeypatch.setattr(windows_uia_server, "windows_open_app", fake_open_app)
    monkeypatch.setattr(windows_uia_server, "windows_window_switch", fake_switch_window)
    monkeypatch.setattr(windows_uia_server, "windows_window_close", fake_close_window)

    async def _open_context(**kwargs):
        return _ctx(kwargs["user_input"], turn_id=kwargs["run_id"])

    monkeypatch.setattr(agent_core, "build_cognitive_turn_context", _open_context)
    open_reply = asyncio.run(agent_core.run_agent("open calculator", object(), max_iterations=1))
    assert _visible_reply(open_reply) == "已打开 Calculator。"

    async def _switch_context(**kwargs):
        return _ctx(kwargs["user_input"], turn_id=kwargs["run_id"])

    monkeypatch.setattr(agent_core, "build_cognitive_turn_context", _switch_context)
    switch_reply = asyncio.run(agent_core.run_agent("switch to calculator", object(), max_iterations=1))
    assert _visible_reply(switch_reply) == "已切换到 Calculator。"

    async def _close_context(**kwargs):
        return _ctx(
            kwargs["user_input"],
            turn_id=kwargs["run_id"],
            active_window={"app_name": "Calculator", "title": "Calculator"},
        )

    monkeypatch.setattr(agent_core, "build_cognitive_turn_context", _close_context)
    close_reply = asyncio.run(agent_core.run_agent("close", object(), max_iterations=1))
    assert _visible_reply(close_reply) == "已关闭 Calculator。"


def test_run_agent_appcontrol_confirmation_pending_then_resume(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    import l3_node.agent_core as agent_core
    from l3_client.local_mcps.windows_uia_mcp import server as windows_uia_server
    from l3_node.cognitive_kernel.pending_confirmation import load_pending_confirmation

    calls = {"close": 0}

    async def fake_assemble_tool_pool(*_args, **_kwargs):
        return [{"id": "mcp:windows_window_close", "name": "windows_window_close"}]

    async def text_transport_core_should_not_run(*_args, **_kwargs):
        raise AssertionError("confirmation direct mainline should not enter text transport core")

    def fake_close_window(keywords: str, exclude_keywords: str = "", timeout: float = 5.0, out_dir: str = "") -> str:
        calls["close"] += 1
        return json.dumps({"ok": True, "window_closed": keywords, "still_exists": False}, ensure_ascii=False)

    session = "ck-confirm-session"

    async def fake_build_context(**kwargs):
        text = kwargs["user_input"]
        if text == "close":
            return _ctx(
                text,
                turn_id=kwargs["run_id"],
                active_window={"app_name": "Calculator", "title": "Calculator"},
                risk_state={"unsaved_documents": True},
            )
        return _ctx(text, turn_id=kwargs["run_id"])

    monkeypatch.setattr(agent_core, "build_cognitive_turn_context", fake_build_context)
    monkeypatch.setattr(agent_core, "assemble_tool_pool", fake_assemble_tool_pool)
    monkeypatch.setattr(windows_uia_server, "windows_window_close", fake_close_window)

    first_reply = asyncio.run(
        agent_core.run_agent(
            "close",
            object(),
            max_iterations=1,
            implicit_attribution={"lark_chat_id": session, "channel": "websocket_terminal"},
        )
    )
    assert "确认" in first_reply or "需要" in first_reply or "吗" in first_reply
    assert "jachin-ui:pending-confirmation" in first_reply
    assert "work_order_id" in first_reply
    assert calls["close"] == 0
    assert load_pending_confirmation(session_id=session, channel="websocket_terminal") is not None

    second_reply = asyncio.run(
        agent_core.run_agent(
            "是。",
            object(),
            max_iterations=1,
            implicit_attribution={"lark_chat_id": session, "channel": "websocket_terminal"},
        )
    )
    assert "Calculator" in second_reply
    assert calls["close"] == 1
    assert load_pending_confirmation(session_id=session, channel="websocket_terminal") is None


def test_run_agent_asr_entity_correction_yes_resume_opens_lark(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    import l3_node.agent_core as agent_core
    from l3_client.local_mcps.windows_uia_mcp import server as windows_uia_server
    from l3_node.cognitive_kernel.pending_confirmation import load_pending_confirmation

    calls = {"open": 0, "app": ""}
    session = "ck-lark-correction-session"

    async def fake_assemble_tool_pool(*_args, **_kwargs):
        return [{"id": "mcp:windows_open_app", "name": "windows_open_app"}]

    async def fake_build_context(**kwargs):
        return _ctx(kwargs["user_input"], turn_id=kwargs["run_id"])

    def fake_open_app(app_name: str, args_json: str = "[]", out_dir: str = "") -> str:
        calls["open"] += 1
        calls["app"] = app_name
        return json.dumps(
            {"ok": True, "app": app_name, "active_window": app_name, "detail": "window_opened"},
            ensure_ascii=False,
        )

    monkeypatch.setattr(agent_core, "build_cognitive_turn_context", fake_build_context)
    monkeypatch.setattr(agent_core, "assemble_tool_pool", fake_assemble_tool_pool)
    monkeypatch.setattr(windows_uia_server, "windows_open_app", fake_open_app)

    first_reply = asyncio.run(
        agent_core.run_agent(
            "打开 lock",
            object(),
            max_iterations=1,
            implicit_attribution={"lark_chat_id": session, "channel": "websocket_terminal"},
        )
    )
    assert "Lark" in first_reply
    assert "是" in first_reply and "否" in first_reply
    assert calls["open"] == 0
    assert load_pending_confirmation(session_id=session, channel="websocket_terminal") is not None

    second_reply = asyncio.run(
        agent_core.run_agent(
            "是。",
            object(),
            max_iterations=1,
            implicit_attribution={"lark_chat_id": session, "channel": "websocket_terminal"},
        )
    )
    assert "Lark" in second_reply
    assert calls == {"open": 1, "app": "Lark"}
    assert load_pending_confirmation(session_id=session, channel="websocket_terminal") is None

    third_reply = asyncio.run(
        agent_core.run_agent(
            "打开 lock",
            object(),
            max_iterations=1,
            implicit_attribution={"lark_chat_id": session, "channel": "websocket_terminal"},
        )
    )
    assert _visible_reply(third_reply) == "已打开 Lark。"
    assert calls == {"open": 2, "app": "Lark"}
    assert load_pending_confirmation(session_id=session, channel="websocket_terminal") is None


    from l3_node.cognitive_kernel.memory_lifecycle import recall_lifecycle_memories

    lifecycle_hits = recall_lifecycle_memories("lock Lark", memory_types=["correction"])
    assert lifecycle_hits
    store = json.loads((tmp_path / "state" / "entity_corrections.json").read_text(encoding="utf-8"))
    record = store["app_corrections"][0]
    assert record["surface_norm"] == "lock"
    assert record["app_name"] == "Lark"
    assert record["success_count"] >= 2
    assert record["failure_count"] == 0


def test_run_agent_file_direct_mainline_read_open_reveal(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    import core.native_tools as native_tools
    import l3_node.agent_core as agent_core
    from l3_client.local_mcps.windows_uia_mcp import server as windows_uia_server

    async def fake_build_context(**kwargs):
        return _ctx(kwargs["user_input"], turn_id=kwargs["run_id"])

    async def fake_assemble_tool_pool(*_args, **_kwargs):
        return [
            {"id": "core:fs_read", "name": "fs_read"},
            {"id": "mcp:windows_file_open", "name": "windows_file_open"},
            {"id": "mcp:windows_file_reveal_in_explorer", "name": "windows_file_reveal_in_explorer"},
        ]

    def transport_run_tool_should_not_run(*_args, **_kwargs):
        raise AssertionError("File direct mainline should use FileExecutorAgent, not raw run_tool")

    async def text_transport_core_should_not_run(*_args, **_kwargs):
        raise AssertionError("File direct mainline should bypass text transport core")

    def fake_fs_read(path: str) -> str:
        assert path == "README.md"
        return "readme content"

    def fake_file_open(path: str, out_dir: str = "") -> str:
        assert path == "README.md"
        return json.dumps({"ok": True, "operation": "open", "path": path}, ensure_ascii=False)

    def fake_file_reveal(path: str, out_dir: str = "") -> str:
        assert path == "README.md"
        return json.dumps({"ok": True, "operation": "reveal", "path": path}, ensure_ascii=False)

    monkeypatch.setattr(agent_core, "build_cognitive_turn_context", fake_build_context)
    monkeypatch.setattr(agent_core, "assemble_tool_pool", fake_assemble_tool_pool)
    monkeypatch.setattr(agent_core, "run_tool", transport_run_tool_should_not_run)
    monkeypatch.setattr(native_tools, "core_fs_read", fake_fs_read)
    monkeypatch.setattr(windows_uia_server, "windows_file_open", fake_file_open)
    monkeypatch.setattr(windows_uia_server, "windows_file_reveal_in_explorer", fake_file_reveal)

    read_reply = asyncio.run(agent_core.run_agent("read file README.md", object(), max_iterations=1))
    assert _visible_reply(read_reply) == "已完成文件操作：README.md。"

    open_reply = asyncio.run(agent_core.run_agent("open file README.md", object(), max_iterations=1))
    assert _visible_reply(open_reply) == "已完成文件操作：README.md。"

    reveal_reply = asyncio.run(agent_core.run_agent("reveal file README.md in explorer", object(), max_iterations=1))
    assert _visible_reply(reveal_reply) == "已完成文件操作：README.md。"
