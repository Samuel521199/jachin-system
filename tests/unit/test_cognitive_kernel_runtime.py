import asyncio
import json
from pathlib import Path


def test_cognitive_kernel_ledger_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    from l3_node.cognitive_kernel.ledger import current_ledger_path
    from l3_node.cognitive_kernel.pipeline import build_cognitive_turn_context
    from l3_node.cognitive_kernel.runtime import (
        build_decision_contract,
        build_recovery_plan,
        build_work_order,
        close_turn,
        mark_work_order_done,
        mark_work_order_running,
        verify_work_order,
    )

    async def _run():
        ctx = await build_cognitive_turn_context(
            run_id="ck-test-1",
            user_input="read project status",
            channel="unit_test",
            prior_messages=[{"role": "user", "content": "please continue the project task"}],
        )
        contract = build_decision_contract(
            turn_id=ctx.envelope.turn_id,
            goal="read project status",
            tool="core:fs_read",
            action_input='{"path":"README.md"}',
        )
        work = build_work_order(
            contract=contract,
            tool="core:fs_read",
            action_input='{"path":"README.md"}',
        )
        mark_work_order_running(work, contract.turn_id)
        report = verify_work_order(
            turn_id=contract.turn_id,
            work_order=work,
            observation="README content ok",
            elapsed_ms=8.0,
        )
        mark_work_order_done(work, contract.turn_id, ok=report.ok)
        assert build_recovery_plan(turn_id=contract.turn_id, work_order=work, verification=report) is None
        close_turn(
            turn_id=contract.turn_id,
            final_text="done",
            executed_work_orders=[work.work_order_id],
            verification_reports=[report],
        )

    asyncio.run(_run())

    path = current_ledger_path()
    assert path.exists()
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    core_events = [
        e["event_type"]
        for e in events
        if e["event_type"] in {
            "turn_started",
            "decision_contract",
            "work_order",
            "verification_report",
            "turn_closure",
        }
    ]
    assert core_events == [
        "turn_started",
        "decision_contract",
        "work_order",
        "work_order",
        "verification_report",
        "work_order",
        "turn_closure",
    ]
    assert events[-1]["payload"]["closure_type"] == "completed"
    assert events[-1]["payload"]["memory_write_requests"][0]["memory_type"] == "short_term_action"


def test_voice_envelope_and_fast_lane_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))
    monkeypatch.delenv("JACHIN_LEGACY_VOICE_FAST_LANE", raising=False)
    monkeypatch.delenv("JACHIN_ENABLE_VOICE_FAST_LANE", raising=False)

    from l3_node.cognitive_kernel.pipeline import build_cognitive_turn_context
    from l3_node.cognitive_kernel.contracts import InputSource
    from l3_node.ws_server import _is_ws_voice_fast_lane, _normalize_ws_implicit_signals_for_kernel

    sig = {
        "source": "desktop_voice_companion",
        "voice_fast_lane": True,
        "skip_context_retrieval": True,
        "voice_raw_stt_text": "关闭",
        "voice_stt_finalized": True,
    }
    cleaned = _normalize_ws_implicit_signals_for_kernel(sig, local_voice_session=True)
    assert cleaned["cognitive_kernel_required"] is True
    assert "voice_fast_lane" not in cleaned
    assert "skip_context_retrieval" not in cleaned
    assert _is_ws_voice_fast_lane("你好", sig, {"origin": "desktop_voice_companion"}) is False

    async def _run():
        ctx = await build_cognitive_turn_context(
            run_id="ck-voice-1",
            user_input="关闭",
            channel="desktop_voice_companion",
            implicit_attribution={"channel": "desktop_voice_companion"},
            desktop_companion_context=cleaned,
        )
        assert ctx.envelope.source == InputSource.VOICE
        assert ctx.envelope.raw_text == "关闭"
        assert ctx.envelope.modality_evidence["voice"]["cognitive_kernel_required"] is True

    asyncio.run(_run())

    monkeypatch.setenv("JACHIN_LEGACY_VOICE_FAST_LANE", "1")
    assert _is_ws_voice_fast_lane("你好", {"voice_fast_lane": True}, {"origin": "desktop_voice_companion"}) is False


def test_role_registry_and_work_order_dispatcher(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    from l3_node.cognitive_kernel.dispatcher import dispatch_tool_work_order
    from l3_node.cognitive_kernel.ledger import current_ledger_path
    from l3_node.cognitive_kernel.roles import get_default_role_registry

    registry = get_default_role_registry()
    assert registry.select_for_tool("core:fs_read").role_id == "FileExecutorAgent"
    assert registry.select_for_tool("mcp:atom_lark_notifier").role_id == "MessageExecutorAgent"
    assert registry.select_for_tool("mcp:windows_lark_send_message").role_id == "MessageExecutorAgent"
    assert registry.select_for_tool("mcp:windows_window_switch").role_id == "AppControlExecutorAgent"
    assert registry.select_for_tool("mcp:windows_window_close").role_id == "AppControlExecutorAgent"
    assert registry.select_for_tool("core:local_memory_search").role_id == "MemoryRecallAgent"
    assert registry.select_for_tool("core:local_memory_append").role_id == "MemoryWriteAgent"

    async def _run():
        async def executor(work_order):
            assert work_order.role_agent == "FileExecutorAgent"
            assert work_order.tool_policy.allowed_tools == ["core:fs_read"]
            return '{"ok":true,"content":"hello"}'

        result = await dispatch_tool_work_order(
            turn_id="ck-dispatch-1",
            goal="read file",
            tool="core:fs_read",
            action_input='{"path":"README.md"}',
            executor=executor,
        )
        assert result.work_order.role_agent == "FileExecutorAgent"
        assert result.verification.ok is True
        assert result.recovery_plan is None

    asyncio.run(_run())
    events = [json.loads(line) for line in current_ledger_path().read_text(encoding="utf-8").splitlines()]
    assert "decision_contract" in [e["event_type"] for e in events]
    assert any(
        e["event_type"] == "work_order" and e["payload"]["role_agent"] == "FileExecutorAgent"
        for e in events
    )
    assert any(
        e["event_type"] == "role_execution_started"
        and e["payload"]["role_id"] == "FileExecutorAgent"
        and e["payload"]["adapter_kind"] == "file"
        for e in events
    )
    assert any(
        e["event_type"] == "role_execution_finished"
        and e["payload"]["role_id"] == "FileExecutorAgent"
        and e["payload"]["ok"] is True
        for e in events
    )


def test_specialized_role_executor_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    from l3_node.cognitive_kernel.dispatcher import dispatch_tool_work_order
    from l3_node.cognitive_kernel.ledger import current_ledger_path

    async def _run():
        async def executor(work_order):
            assert work_order.role_agent == "MessageExecutorAgent"
            return '{"ok":true,"sent":true}'

        result = await dispatch_tool_work_order(
            turn_id="ck-message-1",
            goal="send a lark message",
            tool="mcp:lark_send_text",
            action_input='{"recipients":["Vivian","Neil"],"text":"hello"}',
            executor=executor,
        )
        assert result.work_order.role_agent == "MessageExecutorAgent"
        assert result.verification.ok is True
        assert any(e["type"] == "role_execution" for e in result.verification.evidence)

    asyncio.run(_run())
    events = [json.loads(line) for line in current_ledger_path().read_text(encoding="utf-8").splitlines()]
    finished = [
        e for e in events
        if e["event_type"] == "role_execution_finished" and e["payload"]["role_id"] == "MessageExecutorAgent"
    ]
    assert finished
    assert finished[-1]["payload"]["adapter_kind"] == "message"
    assert finished[-1]["payload"]["evidence"]["recipient_hints"] == ["Vivian", "Neil"]


def test_unknown_tool_still_goes_through_work_order_dispatcher(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    from l3_node.cognitive_kernel.dispatcher import dispatch_tool_work_order
    from l3_node.cognitive_kernel.ledger import current_ledger_path

    async def _run():
        async def executor(work_order):
            assert work_order.role_agent == "ToolExecutionAgent"
            assert work_order.tool_policy.allowed_tools == ["mcp:future_new_tool"]
            return '{"ok":true,"future":true}'

        result = await dispatch_tool_work_order(
            turn_id="ck-future-tool",
            goal="execute future tool",
            tool="mcp:future_new_tool",
            action_input='{"value":1}',
            executor=executor,
        )
        assert result.work_order.role_agent == "ToolExecutionAgent"
        assert result.verification.ok is True

    asyncio.run(_run())
    events = [json.loads(line) for line in current_ledger_path().read_text(encoding="utf-8").splitlines()]
    assert any(e["event_type"] == "decision_contract" for e in events)
    assert any(
        e["event_type"] == "role_execution_started"
        and e["payload"]["role_id"] == "ToolExecutionAgent"
        for e in events
    )


def test_file_executor_direct_native_channel(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))
    monkeypatch.setenv("JACHIN_HOME", str(tmp_path / "home"))

    from l3_node.cognitive_kernel.dispatcher import dispatch_tool_work_order

    async def _run():
        async def transport_should_not_run(_work_order):
            raise AssertionError("FileExecutor should use direct native channel for core:fs_write/read")

        write = await dispatch_tool_work_order(
            turn_id="ck-file-direct-write",
            goal="write note",
            tool="core:fs_write",
            action_input='{"path":"notes/stage-d.txt","content":"stage d ok"}',
            executor=transport_should_not_run,
        )
        assert write.verification.ok is True
        assert "FileExecutorAgent.native" in write.observation

        read = await dispatch_tool_work_order(
            turn_id="ck-file-direct-read",
            goal="read note",
            tool="core:fs_read",
            action_input='{"path":"notes/stage-d.txt"}',
            executor=transport_should_not_run,
        )
        assert read.verification.ok is True
        assert "stage d ok" in read.observation

    asyncio.run(_run())
    assert (Path(tmp_path / "home" / "workspace" / "notes" / "stage-d.txt")).read_text(encoding="utf-8") == "stage d ok"


def test_message_executor_retry_and_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    from l3_node.cognitive_kernel.dispatcher import dispatch_tool_work_order

    async def _run():
        calls = {"n": 0}

        async def flaky_sender(_work_order):
            calls["n"] += 1
            if calls["n"] == 1:
                return '{"ok":false,"error":"timeout"}'
            return '{"ok":true,"sent":true,"message_id":"m1"}'

        result = await dispatch_tool_work_order(
            turn_id="ck-message-retry-1",
            goal="send message",
            tool="mcp:lark_send_text",
            action_input='{"recipients":["Neil"],"text":"hello"}',
            executor=flaky_sender,
        )
        assert calls["n"] == 2
        assert result.verification.ok is True
        role_evidence = [e for e in result.verification.evidence if e.get("type") == "role_execution"]
        assert role_evidence
        assert role_evidence[-1]["adapter_evidence"]["post_send_verified"] is True

    asyncio.run(_run())


def test_memory_write_executor_direct_channel(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    import l3_node.tools.core_local_memory_append as mem_append
    from l3_node.cognitive_kernel.dispatcher import dispatch_tool_work_order

    async def fake_append(*, content, tags=None):
        return {"ok": True, "content": content, "tags": tags or []}

    monkeypatch.setattr(mem_append, "async_run_local_memory_append", fake_append)

    async def _run():
        async def transport_should_not_run(_work_order):
            raise AssertionError("MemoryWriteAgent should use direct memory channel")

        result = await dispatch_tool_work_order(
            turn_id="ck-memory-direct-1",
            goal="remember preference",
            tool="core:local_memory_append",
            action_input='{"content":"User prefers role-agent evidence.","tags":["preference","stage-d"]}',
            executor=transport_should_not_run,
        )
        assert result.verification.ok is True
        assert "MemoryWriteAgent.native" in result.observation
        role_evidence = [e for e in result.verification.evidence if e.get("type") == "role_execution"]
        assert role_evidence[-1]["adapter_evidence"]["direct_memory_channel"] is True

    asyncio.run(_run())


def test_turn_closure_memory_requests_execute_via_memory_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    import l3_node.tools.core_local_memory_append as mem_append
    from l3_node.cognitive_kernel.closure_memory import execute_turn_closure_memory_writes
    from l3_node.cognitive_kernel.ledger import current_ledger_path
    from l3_node.cognitive_kernel.runtime import (
        build_decision_contract,
        build_work_order,
        close_turn,
        verify_work_order,
    )

    writes = []

    async def fake_append(*, content, tags=None):
        writes.append({"content": content, "tags": tags or []})
        return {"ok": True, "content": content, "tags": tags or []}

    monkeypatch.setattr(mem_append, "async_run_local_memory_append", fake_append)

    async def _run():
        contract = build_decision_contract(
            turn_id="ck-closure-memory-1",
            goal="read file",
            tool="core:fs_read",
            action_input='{"path":"README.md"}',
        )
        work = build_work_order(
            contract=contract,
            tool="core:fs_read",
            action_input='{"path":"README.md"}',
        )
        report = verify_work_order(
            turn_id=contract.turn_id,
            work_order=work,
            observation='{"ok":true,"content":"ok"}',
            elapsed_ms=1.0,
        )
        closure = close_turn(
            turn_id=contract.turn_id,
            final_text="done",
            executed_work_orders=[work.work_order_id],
            verification_reports=[report],
        )
        results = await execute_turn_closure_memory_writes(closure)
        assert len(results) == 1
        assert results[0].work_order.role_agent == "MemoryWriteAgent"
        assert results[0].verification.ok is True

    asyncio.run(_run())
    assert writes
    assert "turn_closure" in writes[0]["tags"]
    events = [json.loads(line) for line in current_ledger_path().read_text(encoding="utf-8").splitlines()]
    assert any(
        e["event_type"] == "role_execution_finished"
        and e["payload"]["role_id"] == "MemoryWriteAgent"
        for e in events
    )
    assert any(e["event_type"] == "turn_closure_memory_write_finished" for e in events)


def test_memory_lifecycle_dedupe_recall_and_expiry(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.contracts import MemoryWriteRequest
    from l3_node.cognitive_kernel.memory_lifecycle import (
        recall_lifecycle_memories,
        write_lifecycle_memory,
    )

    req = MemoryWriteRequest(
        turn_id="ck-memory-life-1",
        source_event="unit_test",
        memory_type="user_preference",
        content="User prefers concise role-agent evidence summaries.",
        confidence=0.8,
        ttl="permanent",
    )
    first = write_lifecycle_memory(req)
    second = write_lifecycle_memory(req)
    assert first.memory_id == second.memory_id
    assert second.hit_count == 2

    hits = recall_lifecycle_memories("role-agent evidence", memory_types=["user_preference"], limit=3)
    assert hits
    assert hits[0].memory_id == first.memory_id
    assert hits[0].memory_type == "user_preference"

    short = write_lifecycle_memory(
        MemoryWriteRequest(
            turn_id="ck-memory-life-2",
            source_event="unit_test",
            memory_type="short_term_action",
            content="Temporary task state should expire.",
            confidence=0.7,
            ttl="1ms",
        )
    )
    store = tmp_path / "kernel" / "memory" / "memory_lifecycle.jsonl"
    rows = [json.loads(line) for line in store.read_text(encoding="utf-8").splitlines() if line.strip()]
    for row in rows:
        if row["memory_id"] == short.memory_id:
            row["expires_at_ms"] = 1
    store.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    hits = recall_lifecycle_memories("Temporary task state", memory_types=["short_term_action"], limit=3)
    assert not hits


def test_state_fabric_service_samples_persists_and_reports_status(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    import l3_node.cognitive_kernel.state_service as state_service

    def fake_sample_state():
        return {
            "snapshot_id": "state-unit",
            "generated_at_ms": 123,
            "active_window": {"app_name": "UnitApp", "title": "Unit"},
            "open_windows": [{"app_name": "UnitApp"}],
            "running_apps": [{"name": "UnitApp"}],
            "risk_state": {"unsaved_documents": "unknown"},
            "system_status": {"cpu": 1},
            "sources": ["unit"],
        }

    monkeypatch.setattr(state_service, "sample_state", fake_sample_state)
    service = state_service.StateFabricService(interval_sec=60.0, history_limit=4)
    snapshot = service.sample_once()
    assert snapshot["active_window"]["app_name"] == "UnitApp"
    assert service.latest()["snapshot_id"] == "state-unit"
    assert service.status().sample_count == 1
    assert (tmp_path / "kernel" / "state" / "state_fabric_latest.json").exists()


def test_task_dag_and_guardian_track_ready_nodes(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.runtime import build_decision_contract, build_work_order
    from l3_node.cognitive_kernel.task_dag import create_task_dag_from_work_orders, load_task_dag, update_node_status
    from l3_node.cognitive_kernel.task_guardian import TaskGuardian

    contract = build_decision_contract(
        turn_id="ck-dag-1",
        goal="read then send",
        tool="core:fs_read",
        action_input='{"path":"README.md"}',
    )
    first = build_work_order(contract=contract, tool="core:fs_read", action_input='{"path":"README.md"}')
    second = build_work_order(contract=contract, tool="mcp:lark_send_text", action_input='{"recipients":["Neil"],"text":"ok"}')
    dag = create_task_dag_from_work_orders(
        turn_id=contract.turn_id,
        goal=contract.goal,
        contract=contract,
        work_orders=[first, second],
    )
    assert len(dag.nodes) == 2
    assert dag.nodes[0].status == "ready"
    assert dag.nodes[1].status == "pending"
    assert load_task_dag(dag.dag_id).dag_id == dag.dag_id

    updated = update_node_status(dag.dag_id, dag.nodes[0].node_id, "done")
    ready = [node.node_id for node in updated.nodes if node.status == "ready"]
    assert updated.nodes[1].node_id in ready
    updated = load_task_dag(dag.dag_id)
    assert updated.nodes[1].status == "ready"
    guardian = TaskGuardian(interval_sec=60.0)
    status = guardian.scan_once()
    assert status["watched_dag_count"] >= 1
    assert status["ready_node_count"] >= 1


def test_pending_confirmation_cancel_and_expiry(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn
    from l3_node.cognitive_kernel.ledger import current_ledger_path
    from l3_node.cognitive_kernel.pending_confirmation import (
        cancel_pending_confirmation,
        load_pending_confirmation,
        save_pending_confirmation,
    )
    from tests.unit.test_cognitive_kernel_architecture import _ctx

    plan = plan_cognitive_turn(
        _ctx(
            "close",
            turn_id="ck-pending-cancel",
            active_window={"app_name": "Calculator", "title": "Calculator"},
            risk_state={"unsaved_documents": True},
        )
    )
    save_pending_confirmation(
        contract=plan.decision_contract,
        work_order=plan.work_orders[0],
        session_id="pending-cancel",
        channel="unit",
    )
    assert load_pending_confirmation(session_id="pending-cancel", channel="unit") is not None
    assert cancel_pending_confirmation(session_id="pending-cancel", channel="unit") is not None
    assert load_pending_confirmation(session_id="pending-cancel", channel="unit") is None

    path = save_pending_confirmation(
        contract=plan.decision_contract,
        work_order=plan.work_orders[0],
        session_id="pending-expire",
        channel="unit",
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["expires_at_ms"] = 1
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    assert load_pending_confirmation(session_id="pending-expire", channel="unit") is None

    events = [json.loads(line) for line in current_ledger_path().read_text(encoding="utf-8").splitlines()]
    assert any(e["event_type"] == "confirmation_cancelled" for e in events)
    assert any(e["event_type"] == "confirmation_expired" for e in events)


def test_dispatcher_auto_recovery_retry_for_app_control(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    from l3_node.cognitive_kernel.dispatcher import dispatch_tool_work_order
    from l3_node.cognitive_kernel.ledger import current_ledger_path

    async def _run():
        calls = {"n": 0}

        async def flaky_app_tool(_work_order):
            calls["n"] += 1
            if calls["n"] == 1:
                return '{"ok":false,"error":"timeout"}'
            return '{"ok":true,"active_window":"Calculator","screenshot_path":"C:/tmp/calc.png"}'

        result = await dispatch_tool_work_order(
            turn_id="ck-app-recovery-1",
            goal="switch app",
            tool="mcp:windows_window_switch",
            action_input='{"window_title":"Calculator"}',
            executor=flaky_app_tool,
        )
        assert calls["n"] == 2
        assert result.verification.ok is True
        assert result.recovery_plan is not None
        assert result.recovery_plan.strategy == "retry"

    asyncio.run(_run())
    events = [json.loads(line) for line in current_ledger_path().read_text(encoding="utf-8").splitlines()]
    assert any(e["event_type"] == "recovery_execution_started" for e in events)
    assert any(
        e["event_type"] == "recovery_execution_finished" and e["payload"]["ok"] is True
        for e in events
    )


def test_stage_f_policy_boundaries_live_in_kernel():
    from l3_node.cognitive_kernel import (
        REACT_PSEUDO_ACTION_IDS,
        RECALL_MEMORY_TOOL_ID,
        SQL_DATA_SOP_PROMPT,
        build_fake_mcp_error_recovery_prompt,
        build_fake_weather_error_recovery_prompt,
        is_hallucinated_final_mcp_error_json,
        is_hallucinated_weather_service_error_json,
    )

    assert RECALL_MEMORY_TOOL_ID == "recall_memory"
    assert REACT_PSEUDO_ACTION_IDS == ("recall_memory", "coordinate", "delegate")
    assert "WorkOrder" in SQL_DATA_SOP_PROMPT
    assert "probe" in SQL_DATA_SOP_PROMPT
    assert is_hallucinated_final_mcp_error_json(
        '{"status":"failed","error":"MCP error -32602 invalid arguments for write_file"}'
    )
    assert is_hallucinated_weather_service_error_json(
        '{"status":"error","message":"天气服务暂时不可用","suggestion":"curl wttr.in/Shanghai"}'
    )
    assert not is_hallucinated_weather_service_error_json(
        '{"ok":false,"error":"real wrapped tool failure"}'
    )
    assert "core:fs_write" in build_fake_mcp_error_recovery_prompt()
    assert "util:get_weather_lite" in build_fake_weather_error_recovery_prompt()
