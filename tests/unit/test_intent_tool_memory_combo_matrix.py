import json

from tests.unit.test_cognitive_kernel_architecture import _ctx


def _plan(text: str, *, turn_id: str):
    from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn

    return plan_cognitive_turn(_ctx(text, turn_id=turn_id))


def test_lark_message_send_is_not_stolen_by_project_briefing(monkeypatch, tmp_path):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))
    result = _plan(
        "\u6253\u5f00lark\u5411Neil\u53d1\u9001\u4e00\u6761\u6d88\u606f\uff0c\u5185\u5bb9\u4e3a\u4f60\u597d",
        turn_id="combo-lark-send",
    )

    assert result.review_summary.top_intent == "message_send"
    assert result.review_summary.task_type == "message_delivery"
    assert result.review_summary.target["recipients"] == ["Neil"]
    assert result.review_summary.target["message"] == "\u4f60\u597d"
    assert [wo.inputs.get("tool") for wo in result.work_orders] == [
        "mcp:windows_open_app",
        "mcp:windows_lark_send_message",
    ]


def test_project_briefing_can_upgrade_from_send_message_when_project_evidence_exists(monkeypatch, tmp_path):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))
    result = _plan(
        "\u603b\u7ed3 Jachin \u6700\u8fd1 3 \u5929\u8fdb\u5c55\uff0c\u4f7f\u7528 Codex \u540e\u53d1\u7ed9 Neil",
        turn_id="combo-project-briefing",
    )

    assert result.review_summary.top_intent == "project_briefing_delivery"
    assert result.review_summary.task_type == "project_briefing_delivery"
    assert result.review_summary.target["recipients"] == ["Neil"]
    assert result.review_summary.target["app"] == "Lark"
    assert result.review_summary.target["feature_query"]
    assert [wo.inputs.get("tool") for wo in result.work_orders] == ["mcp:windows_codex_lark_workflow_template"]


def test_web_research_delivery_is_not_misclassified_as_project_briefing(monkeypatch, tmp_path):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))
    result = _plan(
        "\u4e0a\u7f51\u641c\u7d22\u4eca\u5929AI\u6700\u65b0\u6d88\u606f\uff0c\u603b\u7ed3\u540e\u53d1\u7ed9Neil",
        turn_id="combo-web-research-delivery",
    )

    assert result.review_summary.top_intent == "web_research_delivery"
    assert result.review_summary.task_type == "web_research_delivery"
    assert result.review_summary.target["recipients"] == ["Neil"]
    assert "AI" in result.review_summary.target["query"]
    assert result.decision_contract.selected_workflow == "reviewed_web_research_delivery_workflow"
    assert [wo.inputs.get("tool") for wo in result.work_orders] == [
        "mcp:tavily_search",
        "mcp:fetch",
        "core:web_research_summarize",
        "mcp:windows_lark_send_message",
    ]
    assert result.work_orders[1].inputs["depends_on"] == [result.work_orders[0].inputs["decomposition_node_id"]]
    assert result.work_orders[2].inputs["depends_on"] == [result.work_orders[1].inputs["decomposition_node_id"]]
    assert result.work_orders[3].inputs["depends_on"] == [result.work_orders[2].inputs["decomposition_node_id"]]


def test_web_research_delivery_infers_summary_when_user_only_says_search_and_send(monkeypatch, tmp_path):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))
    result = _plan(
        "\u641c\u7d22\u6700\u65b0AI\u6a21\u578b\u76f8\u5173\u7684\u6d88\u606f\uff0c\u7136\u540e\u53d1\u9001\u7ed9Neil",
        turn_id="combo-web-search-send-no-summary-word",
    )

    assert result.review_summary.top_intent == "web_research_delivery"
    assert result.review_summary.task_type == "web_research_delivery"
    assert result.review_summary.target["recipients"] == ["Neil"]
    assert result.review_summary.target["query"] == "\u6700\u65b0AI\u6a21\u578b\u76f8\u5173\u7684\u6d88\u606f"
    assert [wo.inputs.get("tool") for wo in result.work_orders] == [
        "mcp:tavily_search",
        "mcp:fetch",
        "core:web_research_summarize",
        "mcp:windows_lark_send_message",
    ]


def test_web_research_delivery_accepts_short_send_to_recipient_phrase(monkeypatch, tmp_path):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))
    result = _plan(
        "\u770b\u770b\u7f51\u4e0a\u6700\u65b0AI\u6a21\u578b\u65b0\u95fb\uff0c\u6574\u7406\u53d1Neil",
        turn_id="combo-web-search-send-neil-short",
    )

    assert result.review_summary.top_intent == "web_research_delivery"
    assert result.review_summary.task_type == "web_research_delivery"
    assert result.review_summary.target["recipients"] == ["Neil"]
    assert [wo.inputs.get("tool") for wo in result.work_orders] == [
        "mcp:tavily_search",
        "mcp:fetch",
        "core:web_research_summarize",
        "mcp:windows_lark_send_message",
    ]


def test_web_research_manifest_nodes_switch_tools_from_memory_reliability(monkeypatch, tmp_path):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))
    from l3_node.cognitive_kernel.contracts import MemoryEvidence
    from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn

    ctx = _ctx(
        "\u641c\u7d22\u6700\u65b0AI\u6a21\u578b\u76f8\u5173\u7684\u6d88\u606f\uff0c\u7136\u540e\u53d1\u9001\u7ed9Neil",
        turn_id="combo-web-research-reliability-switch",
    )
    ctx.memory_bundle.tool_habits.extend(
        [
            MemoryEvidence(
                memory_id="memory_growth:playbook:tavily-search-degraded",
                memory_type="success_playbook",
                content=(
                    "playbook path=playbooks/learned_success/tavily-search-degraded.md; "
                    "tool=mcp:tavily_search; artifact_success_rate=0.10; "
                    "artifact_use_count=12; artifact_failure_count=10; "
                    "artifact_last_failure_reason=search_empty_results"
                ),
                source="Memory Growth Playbooks",
                confidence=0.92,
                relevance_reason="tool=mcp:tavily_search; artifact_success_rate=0.10",
            ),
            MemoryEvidence(
                memory_id="memory_growth:playbook:browser-search-reliable",
                memory_type="success_playbook",
                content=(
                    "playbook path=playbooks/learned_success/browser-search-reliable.md; "
                    "tool=mcp:browser_search; artifact_success_rate=0.93; "
                    "artifact_use_count=8; artifact_failure_count=0"
                ),
                source="Memory Growth Playbooks",
                confidence=0.88,
                relevance_reason="tool=mcp:browser_search; artifact_success_rate=0.93",
            ),
            MemoryEvidence(
                memory_id="memory_growth:playbook:fetch-degraded",
                memory_type="success_playbook",
                content=(
                    "playbook path=playbooks/learned_success/fetch-degraded.md; "
                    "tool=mcp:fetch; artifact_success_rate=0.20; "
                    "artifact_use_count=10; artifact_failure_count=8; "
                    "artifact_last_failure_reason=fetch_access_or_bot_wall"
                ),
                source="Memory Growth Playbooks",
                confidence=0.9,
                relevance_reason="tool=mcp:fetch; artifact_success_rate=0.20",
            ),
            MemoryEvidence(
                memory_id="memory_growth:playbook:browser-extract-reliable",
                memory_type="success_playbook",
                content=(
                    "playbook path=playbooks/learned_success/browser-extract-reliable.md; "
                    "tool=mcp:browser_extract; artifact_success_rate=0.86; "
                    "artifact_use_count=7; artifact_failure_count=1"
                ),
                source="Memory Growth Playbooks",
                confidence=0.85,
                relevance_reason="tool=mcp:browser_extract; artifact_success_rate=0.86",
            ),
        ]
    )

    result = plan_cognitive_turn(ctx)

    tools = [wo.inputs.get("tool") for wo in result.work_orders]
    assert tools == [
        "mcp:browser_search",
        "mcp:browser_extract",
        "core:web_research_summarize",
        "mcp:windows_lark_send_message",
    ]
    assert result.work_orders[0].inputs["planned_tool"] == "mcp:tavily_search"
    assert result.work_orders[0].inputs["tool_selection_reason"] == "memory_growth_reliability_preferred_alternate_tool"
    assert result.work_orders[1].inputs["planned_tool"] == "mcp:fetch"
    assert result.work_orders[1].inputs["tool_selection_reason"] == "memory_growth_reliability_preferred_alternate_tool"
    assert result.work_orders[3].inputs.get("planned_tool") == "mcp:windows_lark_send_message"
    search_reliability = result.work_orders[0].inputs["candidate_tool_reliability"]
    assert search_reliability[0]["tool"] == "mcp:browser_search"
    assert search_reliability[0]["health"] == "reliable"
    tavily = next(item for item in search_reliability if item["tool"] == "mcp:tavily_search")
    assert tavily["health"] == "degraded"


def test_web_research_summary_uses_complete_clean_sentences():
    from l3_node.cognitive_kernel.role_executors import _web_research_summary_message

    message = _web_research_summary_message(
        query="\u6700\u65b0AI\u6a21\u578b\u76f8\u5173\u7684\u6d88\u606f",
        recipients=["Neil"],
        upstream_observations=[
            {
                "observation": json.dumps(
                    {
                        "ok": True,
                        "results": [
                            {
                                "title": "AI\u6700\u65b0\u8d44\u8baf_Headlines",
                                "url": "https://news.example.com/ai",
                                "content": "AIBase%20--%3e%3cdefs%3e%3cstyle%3e .st0 { fill: #061b40; } %3c/style%3e 2026\u5e747\u6708\uff0c\u591a\u5bb6\u516c\u53f8\u53d1\u5e03\u65b0\u4e00\u4ee3AI\u6a21\u578b\u3002\u8fd9\u4e9b\u6a21\u578b\u4e3b\u8981\u805a\u7126\u4ee3\u7801\u751f\u6210\u3001\u591a\u6a21\u6001\u7406\u89e3\u548c\u4f01\u4e1a\u7ea7\u5de5\u4f5c\u6d41\u3002",
                            }
                        ],
                    },
                    ensure_ascii=False,
                )
            }
        ],
    )

    assert "fill" not in message
    assert "defs" not in message
    assert "..." not in message
    assert "\u591a\u5bb6\u516c\u53f8\u53d1\u5e03\u65b0\u4e00\u4ee3AI\u6a21\u578b" in message
    assert "\u94fe\u63a5\uff1ahttps://news.example.com/ai" in message
    for line in message.splitlines():
        if line.startswith("1. "):
            assert line[-1] in "\u3002.!！?？"


def test_tool_quality_blocks_truncated_noisy_web_summary():
    from l3_node.cognitive_kernel.contracts import ToolPolicy, WorkOrder
    from l3_node.cognitive_kernel.runtime import verify_work_order

    work_order = WorkOrder(
        work_order_id="work-quality-summary",
        decision_id="decision-quality-summary",
        role_agent="BrowserExecutorAgent",
        task="web_research_delivery",
        inputs={"tool": "core:web_research_summarize"},
        tool_policy=ToolPolicy(),
    )
    observation = json.dumps(
        {
            "ok": True,
            "message": "1. AIBase%20--%3e%3cdefs%3e%3cstyle%3e .st0 { fill: #061b40; } 最新AI模型发布了很多...\n链接：https://example.com/ai",
        },
        ensure_ascii=False,
    )

    report = verify_work_order(
        turn_id="combo-quality-summary",
        work_order=work_order,
        observation=observation,
    )

    assert report.ok is False
    assert report.failure_reason.startswith("tool_quality:")
    quality = next(item for item in report.evidence if item.get("type") == "tool_quality")
    assert quality["blocks_execution"] is True
    assert "summary_contains_web_noise" in quality["issues"]
    assert "summary_has_ellipsis_truncation" in quality["issues"]


def test_failure_learning_classifies_tool_quality_failures():
    from l3_node.cognitive_kernel.contracts import ToolPolicy, VerificationReport, WorkOrder
    from l3_node.cognitive_kernel.failure_learning_loop import learn_from_failure

    work_order = WorkOrder(
        work_order_id="work-quality-failure-learning",
        decision_id="decision-quality-failure-learning",
        role_agent="BrowserExecutorAgent",
        task="web_research_delivery",
        inputs={"tool": "core:web_research_summarize"},
        tool_policy=ToolPolicy(),
    )
    verification = VerificationReport(
        verification_id="verify-quality-failure-learning",
        work_order_id=work_order.work_order_id,
        ok=False,
        failure_reason="tool_quality:summary_placeholder_text",
        evidence=[{"type": "tool_quality", "issues": ["summary_placeholder_text"]}],
    )

    record = learn_from_failure(
        turn_id="combo-quality-failure-learning",
        work_order=work_order,
        verification=verification,
    )

    assert record.failure_class == "tool_quality_failed"
    assert record.next_strategy == "switch_to_higher_quality_path_or_regenerate_output"


def test_calculator_request_decomposes_into_open_then_calculate(monkeypatch, tmp_path):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))
    result = _plan(
        "\u6253\u5f00\u8ba1\u7b97\u5668\uff0c\u8ba1\u7b9799+100\u7b49\u4e8e\u591a\u5c11",
        turn_id="combo-calculator",
    )

    assert result.review_summary.top_intent == "calculator_calculate"
    assert result.review_summary.target["expression"] == "99+100"
    assert [wo.inputs.get("tool") for wo in result.work_orders] == [
        "mcp:windows_open_app",
        "mcp:windows_calculator_calculate",
    ]


def test_file_reveal_request_selects_file_reveal_tool(monkeypatch, tmp_path):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))
    result = _plan(
        "\u8bfb\u53d6 D:\\tmp\\report.txt \u5e76\u6253\u5f00\u6240\u5728\u4f4d\u7f6e",
        turn_id="combo-file-reveal",
    )

    assert result.review_summary.top_intent == "file_operation"
    assert result.review_summary.target["operation"] == "reveal"
    assert [wo.inputs.get("tool") for wo in result.work_orders] == ["mcp:windows_file_reveal_in_explorer"]


def test_close_without_target_uses_recent_action_memory(monkeypatch, tmp_path):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))
    from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn

    result = plan_cognitive_turn(
        _ctx(
            "\u5173\u95ed",
            turn_id="combo-close-memory",
            active_window={"app_name": "Jachin Omni", "title": "Jachin"},
            recent_actions=[json.dumps({"target_name": "WeChat", "execution_status": "success"}, ensure_ascii=False)],
        )
    )

    assert result.review_summary.top_intent == "close_app"
    assert result.review_summary.target["name"] == "WeChat"
    assert result.review_summary.target["source"] == "recent_action_memory"
    assert [wo.inputs.get("tool") for wo in result.work_orders] == ["mcp:windows_window_close"]


def test_close_memory_resolution_survives_large_noise_bundle(monkeypatch, tmp_path):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))
    from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn

    noisy_actions = [
        json.dumps({"target_name": f"NoiseApp{i}", "execution_status": "success"}, ensure_ascii=False)
        for i in range(800)
    ]
    noisy_actions.append(json.dumps({"target_name": "WeChat", "execution_status": "success"}, ensure_ascii=False))

    result = plan_cognitive_turn(
        _ctx(
            "\u5173\u95ed",
            turn_id="combo-close-noisy-memory",
            active_window={"app_name": "Jachin Omni", "title": "Jachin"},
            recent_actions=noisy_actions,
        )
    )

    assert result.review_summary.target["name"] == "WeChat"
    assert result.review_summary.target["source"] == "recent_action_memory"


def test_entity_correction_requires_confirmation_before_execution(monkeypatch, tmp_path):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))
    result = _plan("open lock", turn_id="combo-lock-correction")

    assert result.review_summary.top_intent == "open_app"
    assert result.review_summary.target["name"] == "Lark"
    assert result.review_summary.target["requires_entity_confirmation"] is True
    assert result.decision_contract.execution_allowed is False
    assert "Lark" in result.decision_contract.clarification_question


def test_capability_manifest_can_drive_multi_mcp_decomposition(monkeypatch, tmp_path):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))
    from l3_node.cognitive_kernel.contracts import DecisionContract, ReviewSummary, RiskLevel, ToolPolicy
    from l3_node.cognitive_kernel.task_decomposer import decompose_task

    descriptor = {
        "id": "mcp:web_research_to_lark",
        "task_type": "web_research_delivery",
        "metadata": {
            "decomposition": {
                "nodes": [
                    {
                        "id": "search",
                        "goal": "Search the web for $goal",
                        "role_agent": "BrowserExecutorAgent",
                        "tool": "mcp:tavily_search",
                        "inputs": {"query": "$goal"},
                        "verification_criteria": ["search results returned"],
                    },
                    {
                        "id": "summarize",
                        "goal": "Summarize search evidence",
                        "role_agent": "ConversationAgent",
                        "tool": "core:web_research_summarize",
                        "depends_on": ["search"],
                        "verification_criteria": ["summary cites search evidence"],
                    },
                    {
                        "id": "send",
                        "goal": "Send summary to Lark",
                        "role_agent": "MessageExecutorAgent",
                        "tool": "mcp:windows_lark_send_message",
                        "depends_on": ["summarize"],
                        "inputs": {"recipients_json": "$target.recipients_json"},
                        "verification_criteria": ["post-send evidence exists"],
                    },
                ]
            }
        },
    }
    contract = DecisionContract(
        decision_id="decision-manifest-web",
        turn_id="combo-manifest-web",
        task_type="web_research_delivery",
        goal="AI latest news",
        selected_roles=["BrowserExecutorAgent", "MessageExecutorAgent", "VerificationAgent"],
        risk_level=RiskLevel.HIGH,
        tool_policy=ToolPolicy(allowed_tools=["mcp:web_research_to_lark"], risk_level=RiskLevel.HIGH),
        execution_allowed=True,
    )
    summary = ReviewSummary(
        review_session_id="review-manifest-web",
        turn_id="combo-manifest-web",
        top_intent="web_research_delivery",
        task_type="web_research_delivery",
        target={"recipients": ["Neil"], "app": "Lark"},
        candidate_tools=["mcp:web_research_to_lark"],
        capability_candidates=[{"descriptor": descriptor}],
    )

    plan = decompose_task(contract=contract, summary=summary)

    assert [node.tool for node in plan.nodes] == [
        "mcp:tavily_search",
        "core:web_research_summarize",
        "mcp:windows_lark_send_message",
    ]
    assert len(plan.nodes[1].depends_on) == 1
    assert len(plan.nodes[2].depends_on) == 1
    assert "capability metadata decomposition" in " ".join(plan.rationale)


def test_message_verification_blocks_fake_sent_claim_without_role_evidence(monkeypatch, tmp_path):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))
    from l3_node.cognitive_kernel.contracts import RiskLevel, ToolPolicy, WorkOrder
    from l3_node.cognitive_kernel.runtime import verify_work_order

    work_order = WorkOrder(
        work_order_id="work-fake-send",
        decision_id="decision-fake-send",
        role_agent="MessageExecutorAgent",
        task="message_delivery",
        inputs={"tool": "mcp:windows_lark_send_message"},
        tool_policy=ToolPolicy(allowed_tools=["mcp:windows_lark_send_message"], risk_level=RiskLevel.HIGH),
    )

    report = verify_work_order(
        turn_id="combo-fake-send",
        work_order=work_order,
        observation="已发送消息给 Neil。",
        extra_evidence=[],
    )

    assert report.ok is False
    assert report.failure_reason == "missing_role_execution_evidence"
