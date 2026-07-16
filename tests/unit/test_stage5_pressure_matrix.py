import json

import pytest

from tests.unit.test_cognitive_kernel_architecture import _ctx


def _plan(text: str, *, turn_id: str, recent_actions=None):
    from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn

    return plan_cognitive_turn(_ctx(text, turn_id=turn_id, recent_actions=recent_actions))


def _work(tool: str, *, task: str = "web_research_delivery", role: str = "BrowserExecutorAgent"):
    from l3_node.cognitive_kernel.contracts import ToolPolicy, WorkOrder

    return WorkOrder(
        work_order_id=f"work-{tool.replace(':', '-').replace('_', '-')}",
        decision_id="decision-stage5-pressure",
        role_agent=role,
        task=task,
        inputs={"tool": tool},
        tool_policy=ToolPolicy(),
    )


@pytest.mark.parametrize(
    ("text", "expected_task", "expected_intent"),
    [
        ("打开lark向Neil发送一条消息，内容为你好", "message_delivery", "message_send"),
        ("给 Neil 发消息：你好", "message_delivery", "message_send"),
        ("通知Neil今天测试完成", "message_delivery", "message_send"),
        ("同步给Neil：今晚复盘一下", "message_delivery", "message_send"),
        ("发Neil你好", "message_delivery", "message_send"),
        ("给测试备注冒烟草稿发一下测试完成", "message_delivery", "message_send"),
        ("send to Neil: hello", "message_delivery", "message_send"),
        ("message Neil hello", "message_delivery", "message_send"),
        ("把这段话发给Neil：收到", "message_delivery", "message_send"),
        ("告诉Neil我稍后处理", "message_delivery", "message_send"),
        ("打开飞书给Neil发你好", "message_delivery", "message_send"),
        ("用lark发给Neil：OK", "message_delivery", "message_send"),
        ("上网搜索今天AI最新消息，总结后发给Neil", "web_research_delivery", "web_research_delivery"),
        ("搜索最新AI模型相关的消息，然后发送给Neil", "web_research_delivery", "web_research_delivery"),
        ("看看网上最新AI模型新闻，整理发Neil", "web_research_delivery", "web_research_delivery"),
        ("查一下AI Agent最新进展并同步给Neil", "web_research_delivery", "web_research_delivery"),
        ("搜一下GPT最新新闻发Neil", "web_research_delivery", "web_research_delivery"),
        ("web search latest AI model news and send to Neil", "web_research_delivery", "web_research_delivery"),
        ("找找网上关于Qwen的新消息，发给Neil", "web_research_delivery", "web_research_delivery"),
        ("检索今天大模型新闻，整理后通知Neil", "web_research_delivery", "web_research_delivery"),
        ("搜索最近AI编程工具新闻发Neil", "web_research_delivery", "web_research_delivery"),
        ("查一下网上AI模型开源动态，发Neil", "web_research_delivery", "web_research_delivery"),
        ("看看最新人工智能资讯并发给Neil", "web_research_delivery", "web_research_delivery"),
        ("上网查AI模型融资新闻，然后发Neil", "web_research_delivery", "web_research_delivery"),
        ("打开浏览器", "app_control", "open_app"),
        ("启动微信", "app_control", "open_app"),
        ("运行计算器", "app_control", "open_app"),
        ("打开 L A R K", "app_control", "open_app"),
        ("open browser", "app_control", "open_app"),
        ("launch chrome", "app_control", "open_app"),
        ("打开资源管理器", "file_operation", "file_operation"),
        ("切换到浏览器", "app_control", "switch_app"),
        ("回到微信窗口", "app_control", "switch_app"),
        ("关闭微信", "app_control", "close_app"),
        ("关掉浏览器", "app_control", "close_app"),
        ("退出lark", "app_control", "close_app"),
        ("打开计算器，计算99+100等于多少", "calculator_calculate", "calculator_calculate"),
        ("计算器算一下66*8+9-4", "calculator_calculate", "calculator_calculate"),
        ("用计算器计算(12+8)*5", "calculator_calculate", "calculator_calculate"),
        ("calc 40*50+100", "calculator_calculate", "calculator_calculate"),
        ("算一下 18/3+7", "calculator_calculate", "calculator_calculate"),
        ("读取 D:\\tmp\\report.txt", "file_operation", "file_operation"),
        ("打开 D:\\tmp\\report.txt 所在位置", "file_operation", "file_operation"),
        ("把 D:\\tmp\\a.txt 复制到 D:\\tmp\\b.txt", "file_operation", "file_operation"),
        ("移动文件 D:\\tmp\\a.txt 到 D:\\tmp\\archive", "file_operation", "file_operation"),
        ("重命名 D:\\tmp\\a.txt 为 b.txt", "file_operation", "file_operation"),
        ("删除 D:\\tmp\\old.log", "file_operation", "file_operation"),
        ("show in explorer D:\\tmp\\report.txt", "file_operation", "file_operation"),
        ("open file D:\\tmp\\report.txt", "file_operation", "file_operation"),
        ("关闭", "app_control", "close_app"),
    ],
)
def test_stage5_intent_generalization_pressure_matrix(monkeypatch, tmp_path, text, expected_task, expected_intent):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))
    recent_actions = [json.dumps({"target_name": "WeChat", "execution_status": "success"}, ensure_ascii=False)]
    result = _plan(text, turn_id=f"stage5-intent-{abs(hash(text))}", recent_actions=recent_actions)

    assert result.review_summary.task_type == expected_task
    assert result.review_summary.top_intent == expected_intent


@pytest.mark.parametrize(
    ("tool", "observation", "expected_issue", "should_block"),
    [
        ("mcp:tavily_search", {"ok": True, "results": []}, "search_results_missing", True),
        ("mcp:tavily_search", {"ok": True, "results": [{"title": "AI", "url": ""}]}, "search_result_urls_missing", True),
        ("mcp:tavily_search", {"ok": True, "results": [{"url": "https://example.com/a"}]}, "search_result_titles_missing", False),
        ("mcp:tavily_search", {"ok": False, "error": "quota exhausted"}, "tool_reported_failure", True),
        ("mcp:fetch", {"ok": True, "pages": []}, "fetch_pages_missing", True),
        ("mcp:fetch", {"ok": True, "pages": [{"url": "https://a", "text": "too short"}]}, "fetch_readable_content_missing", True),
        ("mcp:fetch", {"ok": True, "pages": [{"url": "https://a", "text": "Access denied. Please enable JavaScript. " * 8}]}, "fetch_access_or_bot_wall", True),
        ("mcp:fetch", {"ok": True, "pages": [{"url": "https://a", "text": "验证码 verify you are human captcha " * 8}]}, "fetch_access_or_bot_wall", True),
        ("mcp:fetch", {"ok": True, "pages": [{"url": "https://a", "content": "login required sign in to continue " * 8}]}, "fetch_access_or_bot_wall", True),
        ("mcp:fetch", {"success": False, "reason": "timeout"}, "tool_reported_failure", True),
        ("core:web_research_summarize", {"ok": True, "message": "只有一句短话 https://a.co"}, "summary_too_short", False),
        ("core:web_research_summarize", {"ok": True, "message": "这是一段没有来源链接的长摘要。" * 8}, "summary_missing_source_urls", True),
        ("core:web_research_summarize", {"ok": True, "message": "1. 正在生成，请稍后会自动刷新。\n链接：https://a.co"}, "summary_placeholder_text", True),
        ("core:web_research_summarize", {"ok": True, "message": "1. AIBase%20--%3e%3cdefs%3e%3cstyle%3e .st0 { fill: #061b40; } 发布新模型。\n链接：https://a.co"}, "summary_contains_web_noise", True),
        ("core:web_research_summarize", {"ok": True, "message": "1. 最新AI模型发布了很多...\n链接：https://a.co"}, "summary_has_ellipsis_truncation", True),
        ("core:web_research_summarize", {"ok": True, "message": "1. 苹果和谷歌合作推出新模型\n链接：https://a.co"}, "summary_incomplete_sentence", True),
        ("core:web_research_summarize", {"ok": True, "message": "1. [### GPT-5.6一发布]([https://a.co]) 带来新变化。\n链接：https://a.co"}, "summary_contains_markdown_artifact", True),
        ("core:web_research_summarize", {"ok": True, "message": "placeholder result not ready\nsource: https://a.co"}, "summary_placeholder_text", True),
        ("core:web_research_summarize", {"ok": True, "message": "1. undefined function() 返回了页面脚本。\n链接：https://a.co"}, "summary_contains_web_noise", True),
        ("core:web_research_summarize", {"ok": True, "message": "1. |---|---| 表格残片混入摘要。\n链接：https://a.co"}, "summary_contains_markdown_artifact", True),
        ("mcp:windows_lark_send_message", {"ok": True, "detail": "sent"}, "message_post_send_unverified", True),
        ("mcp:windows_lark_send_message", {"ok": False, "error": "recipient not found"}, "tool_reported_failure", True),
        ("mcp:windows_lark_send_message", {"ok": True, "message_id": "m1"}, "message_adapter_failed", True),
        ("mcp:windows_lark_send_message", {"ok": True, "duplicate_skipped": True}, "message_duplicate_skipped", True),
        ("core:web_research_summarize", "", "empty_observation", True),
        ("mcp:fetch", {"ok": True, "pages": [{"url": "https://a", "text": "403 forbidden " * 16}]}, "fetch_access_or_bot_wall", True),
        ("mcp:tavily_search", {"ok": True, "items": [{"title": "AI"}]}, "search_results_missing", True),
        ("core:web_research_summarize", {"ok": True, "summary": "1. 暂未生成高质量内容。\n链接：https://a.co"}, "summary_placeholder_text", True),
        ("core:web_research_summarize", {"ok": True, "text": "1. ```html <style>body</style> ```\n链接：https://a.co"}, "summary_contains_markdown_artifact", True),
        ("core:web_research_summarize", {"ok": False, "error": "model timeout"}, "tool_reported_failure", True),
    ],
)
def test_stage5_tool_quality_bad_output_pressure_matrix(tool, observation, expected_issue, should_block):
    from l3_node.cognitive_kernel.tool_quality import evaluate_tool_observation

    extra_evidence = []
    if tool == "mcp:windows_lark_send_message":
        if isinstance(observation, dict) and observation.get("duplicate_skipped") is True:
            extra_evidence = [
                {
                    "type": "role_execution",
                    "adapter_ok": True,
                    "adapter_evidence": {"post_send_verified": True, "duplicate_skipped": True},
                }
            ]
        else:
            extra_evidence = [{"type": "role_execution", "adapter_ok": False, "adapter_evidence": {}}]
    text = json.dumps(observation, ensure_ascii=False) if not isinstance(observation, str) else observation
    report = evaluate_tool_observation(work_order=_work(tool), observation=text, extra_evidence=extra_evidence)

    assert expected_issue in report.issues
    assert report.blocks_execution is should_block


@pytest.mark.parametrize(
    ("reason", "evidence", "expected_class", "expected_strategy"),
    [
        ("tool_quality:summary_missing_source_urls", [], "tool_quality_failed", "switch_to_higher_quality_path_or_regenerate_output"),
        ("summary_contains_web_noise", [], "tool_quality_failed", "switch_to_higher_quality_path_or_regenerate_output"),
        ("summary_has_ellipsis_truncation", [], "tool_quality_failed", "switch_to_higher_quality_path_or_regenerate_output"),
        ("fetch_readable_content_missing", [], "tool_quality_failed", "switch_to_higher_quality_path_or_regenerate_output"),
        ("search_results_missing", [], "tool_quality_failed", "switch_to_higher_quality_path_or_regenerate_output"),
        ("window_not_found", [], "target_not_found", "resolve_target_from_memory_or_ask_user"),
        ("recipient_not_found", [], "target_not_found", "resolve_target_from_memory_or_ask_user"),
        ("missing target app", [], "target_not_found", "resolve_target_from_memory_or_ask_user"),
        ("permission denied", [], "permission_required", "ask_user_or_refresh_auth"),
        ("401 unauthorized", [], "permission_required", "ask_user_or_refresh_auth"),
        ("403 forbidden", [], "permission_required", "ask_user_or_refresh_auth"),
        ("failed to fetch", [], "timeout_or_connection", "retry_with_longer_timeout_or_offline_path"),
        ("network connection reset", [], "timeout_or_connection", "retry_with_longer_timeout_or_offline_path"),
        ("operation timed out", [], "timeout_or_connection", "retry_with_longer_timeout_or_offline_path"),
        ("app_focus_failed", [], "focus_or_window", "switch_window_then_retry_with_visual_check"),
        ("wrong foreground window", [], "focus_or_window", "switch_window_then_retry_with_visual_check"),
        ("post_send_verification_missing", [], "verification_missing", "collect_evidence_then_retry_or_mark_uncertain"),
        ("OCR evidence_missing", [], "verification_missing", "collect_evidence_then_retry_or_mark_uncertain"),
        ("ValueError: message send requires recipient", [], "invalid_input", "repair_slots_or_request_single_missing_field"),
        ("missing slot: project_path", [], "invalid_input", "repair_slots_or_request_single_missing_field"),
        ("unexpected renderer failure", [], "unknown", "inspect_evidence_then_retry_once"),
    ],
)
def test_stage5_failure_learning_classification_pressure_matrix(reason, evidence, expected_class, expected_strategy):
    from l3_node.cognitive_kernel.contracts import ToolPolicy, VerificationReport, WorkOrder
    from l3_node.cognitive_kernel.failure_learning_loop import learn_from_failure

    work_order = WorkOrder(
        work_order_id="work-stage5-failure",
        decision_id="decision-stage5-failure",
        role_agent="BrowserExecutorAgent",
        task="web_research_delivery",
        inputs={"tool": "core:web_research_summarize"},
        tool_policy=ToolPolicy(),
    )
    verification = VerificationReport(
        verification_id="verify-stage5-failure",
        work_order_id=work_order.work_order_id,
        ok=False,
        failure_reason=reason,
        evidence=evidence,
    )
    record = learn_from_failure(
        turn_id=f"stage5-failure-{abs(hash(reason))}",
        work_order=work_order,
        verification=verification,
    )

    assert record.failure_class == expected_class
    assert record.next_strategy == expected_strategy


def test_stage5_quality_failures_flow_into_evidence_and_memory_write():
    from l3_node.cognitive_kernel.failure_learning_loop import learn_from_failure
    from l3_node.cognitive_kernel.runtime import verify_work_order

    cases = [
        (
            "mcp:tavily_search",
            {"ok": True, "results": []},
            "search_results_missing",
        ),
        (
            "mcp:fetch",
            {"ok": True, "pages": [{"url": "https://a", "text": "Access denied. Please enable JavaScript. " * 8}]},
            "fetch_access_or_bot_wall",
        ),
        (
            "core:web_research_summarize",
            {"ok": True, "message": "1. 最新AI模型发布了很多...\n链接：https://a.co"},
            "summary_has_ellipsis_truncation",
        ),
    ]
    for tool, observation, expected_issue in cases:
        work_order = _work(tool)
        report = verify_work_order(
            turn_id=f"stage5-quality-flow-{expected_issue}",
            work_order=work_order,
            observation=json.dumps(observation, ensure_ascii=False),
        )
        quality = next(item for item in report.evidence if item.get("type") == "tool_quality")
        assert report.ok is False
        assert expected_issue in quality["issues"]
        assert quality["blocks_execution"] is True

        record = learn_from_failure(
            turn_id=f"stage5-quality-flow-{expected_issue}",
            work_order=work_order,
            verification=report,
        )
        assert record.failure_class == "tool_quality_failed"
        assert record.memory_write["memory_type"] == "failure_hint"
        assert expected_issue in json.dumps(record.memory_write, ensure_ascii=False)


def test_stage5_dry_run_dispatcher_dag_records_recovery_evidence_and_failure_memory(monkeypatch, tmp_path):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))
    monkeypatch.setenv("JACHIN_RECOVERY_MAX_ATTEMPTS", "3")

    from l3_node.cognitive_kernel.dispatcher import dispatch_tool_work_order
    from l3_node.cognitive_kernel.ledger import current_ledger_path
    from l3_node.cognitive_kernel.role_executors import RoleExecutionAdapter, RoleExecutorRegistry, default_role_executors

    class DryRunBrowserExecutor(RoleExecutionAdapter):
        role_id = "BrowserExecutorAgent"
        adapter_kind = "browser_research_dry_run"

        async def _execute(self, work_order, tool_transport_executor, context):
            return await tool_transport_executor(work_order)

    registry = RoleExecutorRegistry(default_role_executors())
    registry.register(DryRunBrowserExecutor())

    async def _run():
        calls: list[dict[str, str]] = []

        async def fake_executor(work_order):
            tool = str(work_order.inputs.get("tool") or "")
            recovery = work_order.inputs.get("recovery") if isinstance(work_order.inputs.get("recovery"), dict) else {}
            strategy = str(recovery.get("strategy") or "initial")
            calls.append({"tool": tool, "strategy": strategy})
            if tool == "mcp:tavily_search":
                if strategy == "retry_search_with_clean_query":
                    return json.dumps(
                        {
                            "ok": True,
                            "results": [
                                {
                                    "title": "Qwen 发布新模型能力更新",
                                    "url": "https://example.com/qwen-news",
                                    "content": "Qwen 发布新的模型能力更新，覆盖推理、工具调用和多模态。",
                                }
                            ],
                        },
                        ensure_ascii=False,
                    )
                return json.dumps({"ok": True, "results": []}, ensure_ascii=False)
            if tool == "mcp:fetch":
                if strategy == "refetch_sources_for_summary":
                    return json.dumps(
                        {
                            "ok": True,
                            "pages": [
                                {
                                    "url": "https://example.com/qwen-news",
                                    "text": "Qwen 发布新的模型能力更新，重点包括更稳的工具调用、更长上下文和更强多模态理解。" * 6,
                                }
                            ],
                        },
                        ensure_ascii=False,
                    )
                return json.dumps(
                    {
                        "ok": True,
                        "pages": [
                            {
                                "url": "https://example.com/qwen-news",
                                "text": "Access denied. Please enable JavaScript. " * 8,
                            }
                        ],
                    },
                    ensure_ascii=False,
                )
            if tool == "core:web_research_summarize":
                if strategy == "regenerate_clean_summary":
                    return json.dumps(
                        {
                            "ok": True,
                            "message": "1. Qwen 发布新的模型能力更新，重点包括工具调用、多模态理解和长上下文能力。\n链接：https://example.com/qwen-news",
                        },
                        ensure_ascii=False,
                    )
                return json.dumps(
                    {
                        "ok": True,
                        "message": "1. [### Qwen 新消息]([https://example.com/qwen-news]) 还有很多...",
                    },
                    ensure_ascii=False,
                )
            return json.dumps({"ok": False, "error": f"unexpected tool {tool}"}, ensure_ascii=False)

        search = await dispatch_tool_work_order(
            turn_id="stage5-dry-run-dag",
            goal="找找网上关于 Qwen 的新消息，发给 Neil",
            tool="mcp:tavily_search",
            work_order_input='{"query":"Qwen 新消息","max_results":3}',
            executor=fake_executor,
            executor_registry=registry,
        )
        fetch = await dispatch_tool_work_order(
            turn_id="stage5-dry-run-dag",
            goal="找找网上关于 Qwen 的新消息，发给 Neil",
            tool="mcp:fetch",
            work_order_input='{"urls":["https://example.com/qwen-news"]}',
            executor=fake_executor,
            executor_registry=registry,
        )
        summary = await dispatch_tool_work_order(
            turn_id="stage5-dry-run-dag",
            goal="找找网上关于 Qwen 的新消息，发给 Neil",
            tool="core:web_research_summarize",
            work_order_input='{"query":"Qwen 新消息","recipients_json":"[\"Neil\"]"}',
            executor=fake_executor,
            executor_registry=registry,
        )

        assert search.verification.ok is True
        assert fetch.verification.ok is True
        assert summary.verification.ok is True
        assert [item["strategy"] for item in search.attempts] == ["initial", "retry_search_with_clean_query"]
        assert [item["strategy"] for item in fetch.attempts] == ["initial", "refetch_sources_for_summary"]
        assert [item["strategy"] for item in summary.attempts] == ["initial", "regenerate_clean_summary"]
        assert any(call["strategy"] == "retry_search_with_clean_query" for call in calls)
        assert any(call["strategy"] == "refetch_sources_for_summary" for call in calls)
        assert any(call["strategy"] == "regenerate_clean_summary" for call in calls)

    import asyncio

    asyncio.run(_run())

    events = [json.loads(line) for line in current_ledger_path().read_text(encoding="utf-8").splitlines()]
    assert any(e["event_type"] == "role_execution_started" for e in events)
    assert any(e["event_type"] == "role_execution_finished" for e in events)
    assert any(e["event_type"] == "recovery_attempt_planned" for e in events)
    assert any(e["event_type"] == "recovery_execution_started" for e in events)
    assert any(e["event_type"] == "recovery_execution_finished" and e["payload"]["ok"] is True for e in events)
    failure_records = [e for e in events if e["event_type"] == "failure_learning_recorded"]
    assert len(failure_records) >= 3
    assert all(e["payload"]["memory_write"]["memory_type"] == "failure_hint" for e in failure_records)
    quality_events = [
        item
        for e in events
        for item in (e.get("payload") or {}).get("evidence", [])
        if isinstance(item, dict) and item.get("type") == "tool_quality"
    ]
    assert any("search_results_missing" in item.get("issues", []) for item in quality_events)
    assert any("fetch_access_or_bot_wall" in item.get("issues", []) for item in quality_events)
    assert any("summary_contains_markdown_artifact" in item.get("issues", []) for item in quality_events)
