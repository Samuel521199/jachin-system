import asyncio
import json

import pytest


class _PressureBrowserExecutor:
    role_id = "BrowserExecutorAgent"
    adapter_kind = "browser_research_pressure"

    async def execute(self, work_order, tool_transport_executor, context):
        from l3_node.cognitive_kernel.role_executors import BrowserExecutor

        if context.tool in {"mcp:tavily_search", "mcp:fetch"}:
            return await _TransportBackedBrowserExecutor().execute(work_order, tool_transport_executor, context)
        return await BrowserExecutor().execute(work_order, tool_transport_executor, context)


class _TransportBackedBrowserExecutor:
    role_id = "BrowserExecutorAgent"
    adapter_kind = "browser_research_pressure_transport"

    async def execute(self, work_order, tool_transport_executor, context):
        from l3_node.cognitive_kernel.role_executors import RoleExecutionAdapter

        class Adapter(RoleExecutionAdapter):
            role_id = "BrowserExecutorAgent"
            adapter_kind = "browser_research_pressure_transport"

            async def _execute(self, work_order, tool_transport_executor, context):
                return await tool_transport_executor(work_order)

        return await Adapter().execute(work_order, tool_transport_executor, context)


def _pressure_executor_registry():
    from l3_node.cognitive_kernel.role_executors import RoleExecutorRegistry, default_role_executors

    registry = RoleExecutorRegistry(default_role_executors())
    registry.register(_PressureBrowserExecutor())
    return registry


@pytest.mark.parametrize(
    "scenario",
    [
        "clean_success",
        "search_empty_then_recover",
        "fetch_wall_then_recover",
        "lark_timeout_then_retry",
        "lark_fake_ok_blocked",
    ],
)
def test_web_research_to_lark_multi_mcp_workflow_pressure_matrix(monkeypatch, tmp_path, scenario):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / scenario / "kernel"))
    monkeypatch.setenv("JACHIN_RECOVERY_MAX_ATTEMPTS", "4")

    from l3_node.cognitive_kernel.dispatcher import dispatch_tool_work_order
    from l3_node.cognitive_kernel.ledger import current_ledger_path

    state = {"lark_calls": 0, "browser_calls": []}
    qwen_page_text = (
        "AIBase%20--%3e%3cdefs%3e%3cstyle%3e .st0 { fill: #061b40; } "
        "2026年7月，Qwen 发布新的模型能力更新。重点包括更稳定的工具调用、更长上下文和更强多模态理解。"
        "这些变化主要面向开发者和企业应用场景，有助于把模型能力接入真实业务流程。"
    ) * 4
    coding_page_text = (
        "AI 编程工具新版强化了代码审查、测试生成和多文件改动解释能力，适合团队在开发流程中使用。"
        "新版能力强调从需求理解到代码修改再到验证报告的闭环，方便研发团队沉淀工程经验。"
    ) * 4

    async def browser_transport(work_order):
        tool = str(work_order.inputs.get("tool") or "")
        recovery = work_order.inputs.get("recovery") if isinstance(work_order.inputs.get("recovery"), dict) else {}
        strategy = str(recovery.get("strategy") or "initial")
        state["browser_calls"].append({"tool": tool, "strategy": strategy})
        if tool == "mcp:tavily_search":
            if scenario == "search_empty_then_recover" and strategy == "initial":
                return json.dumps({"ok": True, "results": []}, ensure_ascii=False)
            return json.dumps(
                {
                    "ok": True,
                    "results": [
                        {
                            "title": "Qwen 模型能力更新",
                            "url": "https://example.com/qwen-update",
                            "content": "Qwen 发布新的模型能力更新，重点包括更稳定的工具调用、更长上下文和更强多模态理解。",
                        },
                        {
                            "title": "AI 编程工具新版",
                            "url": "https://example.com/ai-coding",
                            "content": "AI 编程工具新版强化了代码审查、测试生成和多文件改动解释能力。",
                        },
                    ],
                },
                ensure_ascii=False,
            )
        if tool == "mcp:fetch":
            if scenario == "fetch_wall_then_recover" and strategy == "initial":
                return json.dumps(
                    {
                        "ok": True,
                        "pages": [
                            {
                                "url": "https://example.com/qwen-update",
                                "text": "Access denied. Please enable JavaScript. " * 8,
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
                            "ok": True,
                            "title": "Qwen 模型能力更新",
                            "url": "https://example.com/qwen-update",
                            "text": qwen_page_text,
                        },
                        {
                            "ok": True,
                            "title": "AI 编程工具新版",
                            "url": "https://example.com/ai-coding",
                            "text": coding_page_text,
                        },
                    ],
                },
                ensure_ascii=False,
            )
        raise AssertionError(f"unexpected browser tool: {tool}")

    async def lark_transport(work_order):
        state["lark_calls"] += 1
        payload = json.loads(str(work_order.inputs.get("work_order_input") or "{}"))
        assert payload["recipients_json"] == json.dumps(["Neil"], ensure_ascii=False)
        assert "【最新AI模型相关的消息｜最新信息简报】" in payload["message"]
        assert "链接：https://example.com/qwen-update" in payload["message"]
        assert "..." not in payload["message"]
        assert "](" not in payload["message"]
        if scenario == "lark_timeout_then_retry" and state["lark_calls"] == 1:
            return json.dumps({"ok": False, "error": "timeout while sending lark message"}, ensure_ascii=False)
        if scenario == "lark_fake_ok_blocked":
            return json.dumps({"ok": True}, ensure_ascii=False)
        return json.dumps(
            {
                "ok": True,
                "message_id": f"msg-{scenario}",
                "recipient_visible": True,
                "message_visible": True,
                "detail": "sent_and_verified_with_visual",
            },
            ensure_ascii=False,
        )

    async def _run():
        registry = _pressure_executor_registry()
        search = await dispatch_tool_work_order(
            turn_id=f"pressure-web-lark-{scenario}",
            goal="搜索最新AI模型相关的消息，然后发送给Neil",
            tool="mcp:tavily_search",
            work_order_input='{"query":"最新AI模型相关的消息","max_results":3}',
            executor=browser_transport,
            executor_registry=registry,
        )
        fetch = await dispatch_tool_work_order(
            turn_id=f"pressure-web-lark-{scenario}",
            goal="搜索最新AI模型相关的消息，然后发送给Neil",
            tool="mcp:fetch",
            work_order_input=json.dumps(
                {
                    "urls": ["https://example.com/qwen-update", "https://example.com/ai-coding"],
                    "upstream_observations": [{"tool": "mcp:tavily_search", "observation": search.observation}],
                },
                ensure_ascii=False,
            ),
            executor=browser_transport,
            executor_registry=registry,
        )
        summary = await dispatch_tool_work_order(
            turn_id=f"pressure-web-lark-{scenario}",
            goal="搜索最新AI模型相关的消息，然后发送给Neil",
            tool="core:web_research_summarize",
            work_order_input=json.dumps(
                {
                    "query": "最新AI模型相关的消息",
                    "recipients_json": json.dumps(["Neil"], ensure_ascii=False),
                    "upstream_observations": [
                        {"tool": "mcp:tavily_search", "observation": search.observation},
                        {"tool": "mcp:fetch", "observation": fetch.observation},
                    ],
                },
                ensure_ascii=False,
            ),
            executor=browser_transport,
            executor_registry=registry,
        )
        summary_obj = json.loads(summary.observation)
        message = summary_obj["message"]
        send = await dispatch_tool_work_order(
            turn_id=f"pressure-web-lark-{scenario}",
            goal="把搜索总结发给Neil",
            tool="mcp:windows_lark_send_message",
            work_order_input=json.dumps(
                {"recipients_json": json.dumps(["Neil"], ensure_ascii=False), "message": message},
                ensure_ascii=False,
            ),
            executor=lark_transport,
            executor_registry=registry,
        )
        return search, fetch, summary, send, message

    search, fetch, summary, send, message = asyncio.run(_run())

    assert search.verification.ok is True
    assert fetch.verification.ok is True
    assert summary.verification.ok is True
    assert "fill:" not in message
    assert "defs" not in message
    assert "来源：" in message
    assert "链接：https://example.com/qwen-update" in message

    if scenario == "search_empty_then_recover":
        assert [item["strategy"] for item in search.attempts] == ["initial", "retry_search_with_clean_query"]
    if scenario == "fetch_wall_then_recover":
        assert [item["strategy"] for item in fetch.attempts] == ["initial", "mark_source_blocked_and_search_alternative"]
    if scenario == "lark_timeout_then_retry":
        assert state["lark_calls"] == 2
        assert send.verification.ok is True
    elif scenario == "lark_fake_ok_blocked":
        assert send.verification.ok is False
        assert send.verification.failure_reason == "message_post_send_verification_missing"
    else:
        assert send.verification.ok is True

    events = [json.loads(line) for line in current_ledger_path().read_text(encoding="utf-8").splitlines()]
    tools = [
        event["payload"].get("tool")
        for event in events
        if event.get("event_type") == "role_execution_started"
    ]
    assert "mcp:tavily_search" in tools
    assert "mcp:fetch" in tools
    assert "core:web_research_summarize" in tools
    assert "mcp:windows_lark_send_message" in tools
    quality_events = [
        item
        for event in events
        for item in (event.get("payload") or {}).get("evidence", [])
        if isinstance(item, dict) and item.get("type") == "tool_quality"
    ]
    assert any(item.get("tool") == "core:web_research_summarize" and item.get("blocks_execution") is False for item in quality_events)
    if scenario in {"search_empty_then_recover", "fetch_wall_then_recover", "lark_fake_ok_blocked"}:
        assert any(event.get("event_type") == "failure_learning_recorded" for event in events)


def test_web_research_to_lark_planned_dag_uses_learned_tool_reliability(monkeypatch, tmp_path):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))
    monkeypatch.setenv("JACHIN_RECOVERY_MAX_ATTEMPTS", "3")

    from l3_node.cognitive_kernel.contracts import MemoryEvidence
    from l3_node.cognitive_kernel.dispatcher import dispatch_existing_work_order
    from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn
    from l3_node.cognitive_kernel.ledger import current_ledger_path

    from tests.unit.test_cognitive_kernel_architecture import _ctx

    ctx = _ctx(
        "\u641c\u7d22\u6700\u65b0AI\u6a21\u578b\u76f8\u5173\u7684\u6d88\u606f\uff0c\u7136\u540e\u53d1\u9001\u7ed9Neil",
        turn_id="pressure-web-research-planned-dag",
    )
    ctx.memory_bundle.tool_habits.extend(
        [
            MemoryEvidence(
                memory_id="memory_growth:playbook:tavily-search-degraded",
                memory_type="success_playbook",
                content=(
                    "playbook path=playbooks/learned_success/tavily-search-degraded.md; "
                    "tool=mcp:tavily_search; artifact_success_rate=0.10; "
                    "artifact_use_count=12; artifact_failure_count=10; artifact_last_failure_reason=search_empty_results"
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
                    "artifact_use_count=10; artifact_failure_count=8; artifact_last_failure_reason=fetch_access_or_bot_wall"
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
    plan = plan_cognitive_turn(ctx)
    assert [work.inputs.get("tool") for work in plan.work_orders] == [
        "mcp:browser_search",
        "mcp:browser_extract",
        "core:web_research_summarize",
        "mcp:windows_lark_send_message",
    ]

    async def transport(work_order):
        tool = str(work_order.inputs.get("tool") or "")
        if tool == "mcp:browser_search":
            return json.dumps(
                {
                    "ok": True,
                    "results": [
                        {
                            "title": "Qwen model update",
                            "url": "https://example.com/qwen-update",
                            "content": "Qwen released model updates for tool use, longer context, and multimodal reasoning.",
                        },
                        {
                            "title": "AI coding tools update",
                            "url": "https://example.com/ai-coding",
                            "content": "AI coding tools improved code review, test generation, and multi-file change explanation.",
                        },
                    ],
                },
                ensure_ascii=False,
            )
        if tool == "mcp:browser_extract":
            return json.dumps(
                {
                    "ok": True,
                    "pages": [
                        {
                            "ok": True,
                            "title": "Qwen model update",
                            "url": "https://example.com/qwen-update",
                            "text": (
                                "Qwen released model updates that improve stable tool calling, longer context, "
                                "and multimodal understanding for enterprise agent workflows."
                            ),
                        },
                        {
                            "ok": True,
                            "title": "AI coding tools update",
                            "url": "https://example.com/ai-coding",
                            "text": (
                                "AI coding tools are improving code review, test generation, and multi-file "
                                "change explanations so engineering teams can close the loop from requirement "
                                "to implementation and verification."
                            ),
                        },
                    ],
                },
                ensure_ascii=False,
            )
        if tool == "mcp:windows_lark_send_message":
            payload = json.loads(str(work_order.inputs.get("work_order_input") or "{}"))
            message = str(payload.get("message") or "")
            quality_report = payload.get("quality_report") if isinstance(payload.get("quality_report"), dict) else {}
            assert "https://example.com/qwen-update" in message
            assert "..." not in message
            assert quality_report.get("send_ready") is True
            assert quality_report.get("source_count", 0) >= 1
            return json.dumps(
                {
                    "ok": True,
                    "message_id": "msg-planned-web-research",
                    "recipient_visible": True,
                    "message_visible": True,
                    "detail": "sent_and_verified_with_visual",
                },
                ensure_ascii=False,
            )
        raise AssertionError(f"unexpected tool: {tool}")

    def inject_upstream(work_order, upstream):
        raw = str(work_order.inputs.get("work_order_input") or "{}")
        try:
            payload = json.loads(raw) if raw.strip() else {}
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        payload["upstream_observations"] = [{"tool": item.work_order.inputs.get("tool"), "observation": item.observation} for item in upstream]
        tool = str(work_order.inputs.get("tool") or "")
        if tool == "mcp:browser_extract" and not payload.get("urls"):
            urls = []
            for item in upstream:
                try:
                    obj = json.loads(item.observation)
                except Exception:
                    obj = {}
                for result in obj.get("results") or []:
                    if isinstance(result, dict) and result.get("url"):
                        urls.append(result["url"])
            payload["urls"] = urls[:3]
        if tool == "mcp:windows_lark_send_message":
            for item in reversed(upstream):
                try:
                    obj = json.loads(item.observation)
                except Exception:
                    obj = {}
                message = str(obj.get("message") or obj.get("summary") or "").strip()
                if message:
                    payload["message"] = message
                    if isinstance(obj.get("quality_report"), dict):
                        payload["quality_report"] = obj["quality_report"]
                    if isinstance(obj.get("sources"), list):
                        payload["sources"] = obj["sources"]
                    break
        work_order.inputs["work_order_input"] = json.dumps(payload, ensure_ascii=False)

    async def _run():
        results = []
        for work_order in plan.work_orders:
            inject_upstream(work_order, results)
            result = await dispatch_existing_work_order(
                contract=plan.decision_contract,
                work_order=work_order,
                executor=transport,
                executor_registry=_pressure_executor_registry(),
            )
            results.append(result)
        return results

    results = asyncio.run(_run())
    assert all(result.verification.ok for result in results)
    assert results[0].work_order.inputs["planned_tool"] == "mcp:tavily_search"
    assert results[1].work_order.inputs["planned_tool"] == "mcp:fetch"
    assert results[0].work_order.inputs["selected_tool_reliability"]["health"] == "reliable"
    assert results[1].work_order.inputs["selected_tool_reliability"]["health"] == "reliable"

    events = [json.loads(line) for line in current_ledger_path().read_text(encoding="utf-8").splitlines()]
    started = [event for event in events if event.get("event_type") == "role_execution_started"]
    search_event = next(event for event in started if (event.get("payload") or {}).get("tool") == "mcp:browser_search")
    evidence = (search_event.get("payload") or {}).get("evidence") or {}
    assert evidence["selected_tool_reliability"]["tool"] == "mcp:browser_search"
    assert evidence["selected_tool_reliability"]["health"] == "reliable"
    assert any(
        event.get("event_type") == "role_execution_started"
        and (event.get("payload") or {}).get("tool") == "mcp:windows_lark_send_message"
        for event in events
    )


def test_web_research_summary_payload_quality_gate_and_direct_injection():
    from l3_node.cognitive_kernel.contracts import RiskLevel, ToolPolicy, WorkOrder
    from l3_node.cognitive_kernel.direct_mainline import _inject_upstream_into_work_order
    from l3_node.cognitive_kernel.role_executors import _web_research_summary_payload

    payload = _web_research_summary_payload(
        query="latest AI model news",
        recipients=["Neil"],
        upstream_observations=[
            {
                "tool": "mcp:browser_extract",
                "observation": json.dumps(
                    {
                        "ok": True,
                        "pages": [
                            {
                                "ok": True,
                                "title": "Qwen model update",
                                "url": "https://example.com/qwen-update",
                                "text": (
                                    "Qwen released a model update for enterprise agent workflows. "
                                    "The update improves tool calling, longer context handling, and multimodal reasoning. "
                                    "Teams can use it to build more reliable multi-step workplace automations."
                                ),
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
            }
        ],
    )

    assert payload["ok"] is True
    quality = payload["quality_report"]
    assert quality["send_ready"] is True
    assert quality["source_count"] == 1
    assert payload["sources"][0]["url"] == "https://example.com/qwen-update"

    work = WorkOrder(
        work_order_id="work-send-quality-injection",
        decision_id="decision-send-quality-injection",
        role_agent="MessageExecutorAgent",
        task="send web research brief",
        inputs={
            "tool": "mcp:windows_lark_send_message",
            "work_order_input": json.dumps({"recipients_json": json.dumps(["Neil"], ensure_ascii=False)}, ensure_ascii=False),
        },
        tool_policy=ToolPolicy(allowed_tools=["mcp:windows_lark_send_message"], risk_level=RiskLevel.LOW),
    )
    _inject_upstream_into_work_order(
        work,
        [
            {
                "tool": "core:web_research_summarize",
                "observation": json.dumps(payload, ensure_ascii=False),
            }
        ],
    )

    injected = json.loads(work.inputs["work_order_input"])
    assert "Qwen" in injected["message"]
    assert injected["quality_report"]["send_ready"] is True
    assert injected["sources"][0]["url"] == "https://example.com/qwen-update"


def test_web_research_source_quality_memory_downranks_bad_domains(monkeypatch, tmp_path):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.source_quality_memory import (
        rank_urls_by_source_quality,
        record_web_research_source_quality,
        source_reputation_for_url,
    )

    for _ in range(3):
        record_web_research_source_quality(
            query="latest AI model news",
            quality_report={
                "send_ready": False,
                "score": 0.12,
                "quality_level": "blocked",
                "issues": ["brief_contains_web_residue", "source_url_missing"],
                "primary_issue": "brief_contains_web_residue",
                "source_count": 1,
            },
            sources=[{"title": "Bad AI page", "url": "https://bad.example.com/ai", "source": "finding_rejected"}],
            turn_id="source-quality-bad",
        )
    for _ in range(3):
        record_web_research_source_quality(
            query="latest AI model news",
            quality_report={
                "send_ready": True,
                "score": 0.94,
                "quality_level": "production",
                "issues": [],
                "primary_issue": "",
                "source_count": 1,
            },
            sources=[{"title": "Good AI page", "url": "https://good.example.com/ai", "source": "model"}],
            turn_id="source-quality-good",
        )

    bad = source_reputation_for_url("https://bad.example.com/ai")
    good = source_reputation_for_url("https://good.example.com/ai")
    assert bad["health"] == "degraded"
    assert good["health"] == "reliable"
    ranked = rank_urls_by_source_quality(
        [
            "https://bad.example.com/ai",
            "https://unknown.example.com/ai",
            "https://good.example.com/ai",
        ]
    )
    assert ranked[0] == "https://good.example.com/ai"
    assert ranked[-1] == "https://bad.example.com/ai"

    from l3_node.memory_growth_http import memory_growth_status

    status = memory_growth_status()
    source_quality = status["monitoring"]["source_quality"]
    assert source_quality["summary"]["domain_count"] == 2
    assert source_quality["summary"]["reliable_count"] == 1
    assert source_quality["summary"]["degraded_count"] == 1
    assert source_quality["reliable_sources"][0]["domain"] == "good.example.com"
    assert source_quality["degraded_sources"][0]["domain"] == "bad.example.com"


def test_web_research_summary_skips_degraded_sources_when_clean_alternatives_exist(monkeypatch, tmp_path):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "source-selection" / "kernel"))

    from l3_node.cognitive_kernel.role_executors import _web_research_summary_payload
    from l3_node.cognitive_kernel.source_quality_memory import record_web_research_source_quality

    for _ in range(3):
        record_web_research_source_quality(
            query="latest AI model news",
            quality_report={
                "send_ready": False,
                "score": 0.08,
                "quality_level": "blocked",
                "issues": ["brief_contains_web_residue", "source_url_missing"],
                "primary_issue": "brief_contains_web_residue",
                "source_count": 1,
            },
            sources=[{"title": "Bad source", "url": "https://bad.example.com/ai", "source": "finding_rejected"}],
            turn_id="source-selection-bad",
        )

    payload = _web_research_summary_payload(
        query="latest AI model news",
        recipients=["Neil"],
        upstream_observations=[
            {
                "tool": "mcp:fetch",
                "observation": json.dumps(
                    {
                        "ok": True,
                        "pages": [
                            {
                                "ok": True,
                                "title": "Bad AI page",
                                "url": "https://bad.example.com/ai",
                                "text": (
                                    "This page has a history of producing noisy AI summaries. "
                                    "It repeats weak fragments and should not be trusted when cleaner alternatives exist. "
                                )
                                * 4,
                            },
                            {
                                "ok": True,
                                "title": "Reliable AI model update",
                                "url": "https://good.example.com/ai",
                                "text": (
                                    "The latest AI model update improves tool calling, long-context handling, "
                                    "and multimodal reasoning for enterprise workflows. Teams can use the update "
                                    "to build more reliable multi-step workplace automations. "
                                )
                                * 4,
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
            }
        ],
    )

    assert payload["ok"] is True
    assert [source["url"] for source in payload["sources"]] == ["https://good.example.com/ai"]
    assert "https://good.example.com/ai" in payload["message"]
    assert "https://bad.example.com/ai" not in payload["message"]
    assert payload["quality_report"]["source_health"]["degraded"] == 0


def test_web_research_dry_run_message_executor_generates_preview_without_send(monkeypatch, tmp_path):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "dry-run-message" / "kernel"))

    from l3_node.cognitive_kernel.dispatcher import dispatch_tool_work_order

    calls = {"send": 0}

    async def forbidden_send_executor(work_order):
        calls["send"] += 1
        raise AssertionError("dry-run must not call the external Lark transport")

    async def _run():
        return await dispatch_tool_work_order(
            turn_id="web-research-dry-run-message",
            goal="preview web research brief for Neil",
            tool="mcp:windows_lark_send_message",
            work_order_input=json.dumps(
                {
                    "recipients_json": json.dumps(["Neil"], ensure_ascii=False),
                    "message": "【AI模型最新消息】\n一句话结论：这是 dry-run 预览。\n来源：https://example.com/ai",
                    "delivery_mode": "dry_run",
                    "dry_run": True,
                    "send_allowed": False,
                    "quality_report": {
                        "send_ready": True,
                        "requires_preview": False,
                        "quality_level": "production",
                        "score": 0.96,
                        "source_count": 1,
                        "message_length": 80,
                    },
                },
                ensure_ascii=False,
            ),
            executor=forbidden_send_executor,
        )

    result = asyncio.run(_run())

    assert calls["send"] == 0
    assert result.verification.ok is True
    obj = json.loads(result.observation)
    assert obj["dry_run"] is True
    assert obj["dry_run_preview_verified"] is True
    assert obj["channel"] == "MessageExecutorAgent.preview"


def test_web_research_natural_language_dry_run_marks_delivery_node(monkeypatch, tmp_path):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "dry-run-plan" / "kernel"))

    from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn
    from tests.unit.test_cognitive_kernel_architecture import _ctx

    plan = plan_cognitive_turn(
        _ctx(
            "只演练：搜索最新AI模型相关的消息，总结后发给Neil，不要发送",
            turn_id="web-research-dry-run-plan",
        )
    )

    assert plan.decision_contract.task_type == "web_research_delivery"
    send_orders = [
        work
        for work in plan.work_orders
        if str(work.inputs.get("tool") or "") == "mcp:windows_lark_send_message"
    ]
    assert send_orders
    payload = json.loads(str(send_orders[-1].inputs.get("work_order_input") or "{}"))
    assert payload["delivery_mode"] == "dry_run"
    assert payload["dry_run"] is True
    assert payload["send_allowed"] is False


def test_web_research_live_guard_blocks_unsafe_recipients(monkeypatch):
    monkeypatch.setenv("JACHIN_WEB_RESEARCH_LIVE_RECIPIENT_ALLOWLIST", "Neil,测试备注冒烟草稿")

    from scripts.os_evidence_task_runner import _live_recipients_guard

    allowed = _live_recipients_guard(["Neil", "测试备注冒烟草稿"])
    blocked = _live_recipients_guard(["Vivian"])
    missing = _live_recipients_guard([])

    assert allowed["ok"] is True
    assert blocked["ok"] is False
    assert blocked["reason"] == "recipient_not_in_live_allowlist"
    assert blocked["blocked"] == ["Vivian"]
    assert missing["ok"] is False
    assert missing["reason"] == "missing_recipient"


def test_web_research_delivery_evidence_extracts_post_send_verification(tmp_path):
    from scripts.os_evidence_task_runner import _extract_lark_delivery_evidence, _message_sha256

    ledger = tmp_path / "ledger.jsonl"
    turn_id = "web-live-proof"
    message = "AI 模型简报\n链接：https://example.com/ai"
    ledger.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "event_type": "role_execution_finished",
                        "turn_id": turn_id,
                        "payload": {
                            "role_id": "MessageExecutorAgent",
                            "adapter_role": "MessageExecutorAgent",
                            "work_order_id": "work-send-ai-brief",
                            "tool": "mcp:windows_lark_send_message",
                            "elapsed_ms": 1234,
                            "observation_preview": json.dumps(
                                {
                                    "ok": True,
                                    "message_id": "msg-1",
                                    "recipient_visible": True,
                                    "message_visible": True,
                                },
                                ensure_ascii=False,
                            ),
                            "evidence": {
                                "delivery_mode": "live_run",
                                "post_send_verified": True,
                                "dry_run_preview_verified": False,
                                "send_result": {"ok": True, "reason": "sent_and_verified_with_visual"},
                                "web_research_quality_report": {"send_ready": True, "score": 0.96},
                            },
                        },
                    },
                    ensure_ascii=False,
                )
            ]
        ),
        encoding="utf-8",
    )

    evidence = _extract_lark_delivery_evidence(
        ledger,
        turn_id,
        message=message,
        live_guard={"ok": True, "recipients": ["Neil"], "reason": ""},
    )

    assert evidence["role_execution_found"] is True
    assert evidence["delivery_mode"] == "live_run"
    assert evidence["post_send_verified"] is True
    assert evidence["message_sha256"] == _message_sha256(message)
    assert evidence["quality_report"]["send_ready"] is True
    assert evidence["reason"] == "verified"
