import asyncio
import json


def _ctx(text, *, turn_id="cap-adapter-run-agent"):
    from l3_node.cognitive_kernel.contracts import (
        AgentInputEnvelope,
        InputSource,
        RelevantMemoryBundle,
        StateSnapshot,
        TaskLedgerEntry,
    )
    from l3_node.cognitive_kernel.pipeline import CognitiveTurnContext

    envelope = AgentInputEnvelope(
        turn_id=turn_id,
        source=InputSource.TEXT,
        raw_text=text,
        normalized_text=text,
        confidence=0.88,
    )
    state = StateSnapshot(
        snapshot_id=f"state-{turn_id}",
        generated_at_ms=1,
        freshness_ms=1,
    )
    memory = RelevantMemoryBundle(turn_id=turn_id, confidence=0.8)
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


def test_capability_adapter_executes_orchestrator_chosen_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    from l3_node.cognitive_kernel.capability_work_order_adapter import try_execute_capability_work_order

    calls = {}

    async def fake_run_tool(tool_id, work_order_input, allowed):
        calls["tool_id"] = tool_id
        calls["work_order_input"] = json.loads(work_order_input)
        calls["allowed"] = allowed
        return json.dumps({"ok": True, "definition": "agenda: 议程"}, ensure_ascii=False)

    class _Frame:
        inputs = {}
        target = ""

    class _Decision:
        chosen = {
            "tool_id": "mcp:english_tutor_lookup",
            "route_policy": "execute",
            "consistency": "PASS",
        }
        intent_frame = _Frame()

    reply = asyncio.run(
        try_execute_capability_work_order(
            user_input="explain agenda",
            tools=[
                {
                    "id": "mcp:english_tutor_lookup",
                    "description": "Explain English words and vocabulary meanings.",
                    "inputSchema": {"type": "object", "properties": {"word": {"type": "string"}}},
                }
            ],
            allowed_skills=None,
            run_tool_func=fake_run_tool,
            run_id="cap-adapter-chosen",
            intent_decision=_Decision(),
        )
    )

    assert reply is not None
    assert "已通过 mcp:english_tutor_lookup 完成该能力调用" in reply
    assert calls["tool_id"] == "mcp:english_tutor_lookup"
    assert calls["work_order_input"]["word"] == "agenda"



def test_capability_adapter_uses_local_windows_open_app_bridge(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    import l3_node.cognitive_kernel.capability_work_order_adapter as adapter
    from l3_node.cognitive_kernel.capability_work_order_adapter import try_execute_capability_work_order

    calls = {"local": 0, "runner": 0}

    async def fake_local_app_control(tool_id: str, work_order_input: str) -> str:
        calls["local"] += 1
        assert tool_id == "mcp:windows_open_app"
        assert json.loads(work_order_input)["app_name"] == "lark"
        return json.dumps(
            {
                "task": "open_app",
                "ok": True,
                "detail": "app_opened_and_window_verified",
                "evidence": {"active_window": {"title": "Lark"}, "screenshot": "C:/tmp/lark.png"},
            },
            ensure_ascii=False,
        )

    def runner_should_not_handle_app_control(tool_id, work_order_input, allowed):
        calls["runner"] += 1
        return f"[未知工具: {tool_id}]"

    monkeypatch.setattr(adapter, "_call_local_windows_app_control", fake_local_app_control)

    class _Frame:
        inputs = {"app_name": "lark"}
        target = "lark"

    class _Decision:
        chosen = {
            "tool_id": "mcp:windows_open_app",
            "route_policy": "execute",
            "consistency": "PASS",
        }
        intent_frame = _Frame()

    reply = asyncio.run(
        try_execute_capability_work_order(
            user_input="打开lark",
            tools=[
                {
                    "id": "mcp:windows_open_app",
                    "description": "Open a Windows app and verify the foreground window.",
                    "inputSchema": {"type": "object", "properties": {"app_name": {"type": "string"}}},
                }
            ],
            allowed_skills=None,
            run_tool_func=runner_should_not_handle_app_control,
            run_id="cap-adapter-open-lark-local",
            intent_decision=_Decision(),
        )
    )

    assert reply is not None
    assert "已通过 mcp:windows_open_app 完成该能力调用" in reply
    assert calls == {"local": 1, "runner": 0}
def test_run_agent_dynamic_mcp_uses_capability_work_order_adapter(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    import l3_node.agent_core as agent_core

    calls = {}

    async def fake_build_context(**kwargs):
        return _ctx(kwargs["user_input"], turn_id=kwargs["run_id"])

    async def fake_assemble_tool_pool(*_args, **_kwargs):
        return [
            {
                "id": "mcp:english_tutor_lookup",
                "name": "english_tutor_lookup",
                "description": "Explain English words, vocabulary meanings, example sentences, and quizzes.",
                "inputSchema": {"type": "object", "properties": {"word": {"type": "string"}}},
            }
        ]

    def fake_run_tool(tool_id, work_order_input, allowed_skills=None):
        calls["tool_id"] = tool_id
        calls["work_order_input"] = json.loads(work_order_input)
        return json.dumps({"ok": True, "definition": "agenda: 议程"}, ensure_ascii=False)

    async def text_transport_should_not_run(*_args, **_kwargs):
        raise AssertionError("dynamic MCP should execute through capability WorkOrder adapter")

    monkeypatch.setattr(agent_core, "build_cognitive_turn_context", fake_build_context)
    monkeypatch.setattr(agent_core, "assemble_tool_pool", fake_assemble_tool_pool)
    monkeypatch.setattr(agent_core, "run_tool", fake_run_tool)

    reply = asyncio.run(agent_core.run_agent("english tutor lookup agenda", object(), max_iterations=1))

    assert "mcp:english_tutor_lookup" in reply
    assert calls["tool_id"] == "mcp:english_tutor_lookup"
    assert calls["work_order_input"]["word"] == "agenda"


def test_capability_adapter_executes_semantic_metadata_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    from l3_node.cognitive_kernel.capability_work_order_adapter import try_execute_capability_work_order

    calls = {}

    def fake_run_tool(tool_id, work_order_input, allowed):
        calls["tool_id"] = tool_id
        calls["work_order_input"] = json.loads(work_order_input)
        return json.dumps({"ok": True, "quiz": "agenda means a list of meeting items"}, ensure_ascii=False)

    reply = asyncio.run(
        try_execute_capability_work_order(
            user_input="english tutor lookup agenda",
            tools=[
                {
                    "id": "mcp:english_tutor_lookup",
                    "description": "Explain English words, vocabulary meanings, example sentences, and quizzes.",
                    "inputSchema": {"type": "object", "properties": {"word": {"type": "string"}}},
                }
            ],
            allowed_skills=None,
            run_tool_func=fake_run_tool,
            run_id="cap-adapter-semantic",
            intent_decision=None,
        )
    )

    assert reply is not None
    assert calls["tool_id"] == "mcp:english_tutor_lookup"
    assert calls["work_order_input"]["word"] == "agenda"


def test_capability_adapter_executes_hook_work_order_suggestion(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    from l3_node.cognitive_kernel.capability_hook_bridge import build_work_order_suggestion
    from l3_node.cognitive_kernel.capability_work_order_adapter import try_execute_capability_work_order

    calls = {}

    async def fake_run_tool(tool_id, work_order_input, allowed):
        calls["tool_id"] = tool_id
        calls["work_order_input"] = json.loads(work_order_input)
        calls["allowed"] = allowed
        return json.dumps({"ok": True, "written": True}, ensure_ascii=False)

    prompt = build_work_order_suggestion(
        tool="mcp:demo_tool",
        work_order_input={"value": "42"},
        reason="unit_test_hook",
        role_agent="ToolExecutionAgent",
    )

    reply = asyncio.run(
        try_execute_capability_work_order(
            user_input=prompt,
            tools=[
                {
                    "id": "mcp:demo_tool",
                    "description": "Demo no-side-effect tool.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "value": {"type": "string"},
                        },
                    },
                }
            ],
            allowed_skills=None,
            run_tool_func=fake_run_tool,
            run_id="cap-adapter-hook",
            intent_decision=None,
        )
    )

    assert reply is not None
    assert calls["tool_id"] == "mcp:demo_tool"
    assert calls["work_order_input"] == {"value": "42"}


def test_capability_policy_prompts_are_structured_work_order_suggestions():
    from l3_node.capability_policies.hr_recruitment import build_scheduler_running_without_tool_prompt
    from l3_node.capability_policies.sqlite_grounding import build_sqlite_requires_observation_prompt
    from l3_node.capability_policies.workspace_writeback import build_writeback_missing_prompt

    prompts = [
        build_writeback_missing_prompt(),
        build_sqlite_requires_observation_prompt(),
        build_scheduler_running_without_tool_prompt(),
    ]
    for prompt in prompts:
        assert "jachin-kernel:work-order-suggestion" in prompt
        assert "WorkOrder:" not in prompt
        assert "tool input" not in prompt
        assert "RoleExecutionAgent" not in prompt


def test_capability_adapter_maps_lark_recipients_json_from_intent_slots(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    from l3_node.cognitive_kernel.capability_work_order_adapter import try_execute_capability_work_order

    calls = {}

    async def fake_run_tool(tool_id, work_order_input, allowed):
        calls["tool_id"] = tool_id
        calls["work_order_input"] = json.loads(work_order_input)
        return json.dumps({"ok": True, "send_ok": True, "message_id": "unit-msg-1"}, ensure_ascii=False)

    class _Frame:
        target = "lark"
        inputs = {"recipients": ["VIVIAN"], "message": "今天不下雨"}

    class _Decision:
        chosen = {
            "tool_id": "mcp:windows_lark_send_message",
            "route_policy": "execute",
            "consistency": "PASS",
        }
        intent_frame = _Frame()

    reply = asyncio.run(
        try_execute_capability_work_order(
            user_input="打开LARK给VIVIAN发一条消息，内容是今天不下雨。",
            tools=[
                {
                    "id": "mcp:windows_lark_send_message",
                    "description": "Open Lark and send a message with verified UI evidence.",
                    "params": ["recipients_json", "message", "out_dir", "max_attempts"],
                }
            ],
            allowed_skills=None,
            run_tool_func=fake_run_tool,
            run_id="cap-adapter-lark-recipients-json",
            intent_decision=_Decision(),
        )
    )

    assert reply is not None
    assert calls["tool_id"] == "mcp:windows_lark_send_message"
    assert json.loads(calls["work_order_input"]["recipients_json"]) == ["VIVIAN"]
    assert calls["work_order_input"]["message"] == "今天不下雨"


