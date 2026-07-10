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
