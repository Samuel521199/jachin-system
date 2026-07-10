import asyncio
import json
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_external_memory_providers(monkeypatch):
    import l3_node.cognitive_kernel.memory_recall_agent as memory_recall_agent

    async def _no_passive_nexus(_limit: int):
        return [], []

    def _no_experience(**_kwargs):
        return [], []

    monkeypatch.setattr(memory_recall_agent, "_passive_nexus_memory_evidence", _no_passive_nexus)
    monkeypatch.setattr(memory_recall_agent, "_experience_memory_evidence", _no_experience)


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
            work_order_input='{"path":"README.md"}',
        )
        work = build_work_order(
            contract=contract,
            tool="core:fs_read",
            work_order_input='{"path":"README.md"}',
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
    monkeypatch.delenv("JACHIN_ARCHIVED_VOICE_FAST_LANE", raising=False)
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

    monkeypatch.setenv("JACHIN_ARCHIVED_VOICE_FAST_LANE", "1")
    assert _is_ws_voice_fast_lane("你好", {"voice_fast_lane": True}, {"origin": "desktop_voice_companion"}) is False


def test_memory_recall_section_6_query_plan_and_long_term_channels(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    import l3_node.local_memory_search as local_search
    from l3_node.cognitive_kernel.contracts import AgentInputEnvelope, InputSource, MemoryWriteRequest, StateSnapshot
    from l3_node.cognitive_kernel.memory_lifecycle import write_lifecycle_memory
    from l3_node.cognitive_kernel.memory_recall_agent import recall_relevant_memory

    def fake_search_local_memories(query, *, top_k=8, **_kwargs):
        return {
            "ok": True,
            "hits": [
                {
                    "id": f"tool-{abs(hash(query))}",
                    "memory_type": "tool_habit",
                    "content": "tool habit: for communication use mcp:windows_lark_send_message before browser automation",
                    "score": 0.91,
                    "ttl": "long_term",
                }
            ],
        }

    monkeypatch.setattr(local_search, "search_local_memories", fake_search_local_memories)

    for memory_type, content in [
        ("contact", "contact memory: Vivian is the usual report recipient for communication tasks"),
        ("safety_preference", "safety preference: confirm before sending messages to Vivian"),
        ("project_fact", "project fact: the report belongs to the sales dashboard project"),
        ("historical_task_summary", "historical task summary: report task paused after data cleaning"),
    ]:
        write_lifecycle_memory(
            MemoryWriteRequest(
                turn_id="ck-memory-section-6-seed",
                source_event="unit_test",
                memory_type=memory_type,
                content=content,
                confidence=0.9,
                ttl="permanent",
            )
        )

    async def _run():
        envelope = AgentInputEnvelope(
            turn_id="ck-memory-section-6",
            source=InputSource.TEXT,
            raw_text="send to Vivian: report is ready",
            normalized_text="send to Vivian: report is ready",
        )
        state = StateSnapshot(
            snapshot_id="state-section-6",
            generated_at_ms=1,
            freshness_ms=1,
            active_window={"app_name": "Lark", "title": "Vivian"},
            recent_app_events=[{"event": "foreground_changed", "app_name": "Lark"}],
            task_state={"active_task": "report"},
        )
        bundle = await recall_relevant_memory(
            envelope=envelope,
            state_snapshot=state,
            prior_messages=[{"role": "user", "content": "continue the report task"}],
            max_results_per_channel=4,
        )
        assert "send_message" in bundle.candidate_intents
        assert "communication" in bundle.candidate_task_domains
        assert "query_2_candidate_intent" in bundle.multi_queries
        assert "query_6_long_term_user_memory" in bundle.multi_queries
        assert "load_long_term_user_memory" in bundle.recall_request["retrieval_purpose"]
        assert bundle.contact_matches
        assert bundle.safety_preferences
        assert bundle.project_facts
        assert bundle.tool_habits
        assert bundle.historical_task_summaries
        assert bundle.ranking_evidence
        assert {"score", "task_relevance_score", "state_alignment_score"} <= set(bundle.ranking_evidence[0])

    asyncio.run(_run())


def test_memory_recall_unifies_passive_prompt_memory_sources(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    import l3_node.cognitive_kernel.memory_recall_agent as memory_recall_agent
    from l3_node.cognitive_kernel.contracts import AgentInputEnvelope, InputSource, MemoryEvidence, StateSnapshot

    async def fake_passive_nexus(_limit: int):
        return [
            MemoryEvidence(
                memory_id="passive-l0",
                memory_type="user_preference",
                content="preference: preferred browser Chrome",
                source="unit-passive-l0",
                confidence=0.9,
                confirmed_by_user=True,
                ttl="long_term",
            ),
            MemoryEvidence(
                memory_id="passive-l1",
                memory_type="historical_task_summary",
                content="historical task summary: browser automation usually uses Chrome",
                source="unit-passive-l1",
                confidence=0.8,
                ttl="long_term",
            ),
        ], []

    def fake_experience(**_kwargs):
        return [
            MemoryEvidence(
                memory_id="experience-tool",
                memory_type="tool_habit",
                content="tool habit: open browser with mcp:windows_open_app before UI automation",
                source="unit-experience",
                confidence=0.88,
                ttl="long_term",
            )
        ], []

    monkeypatch.setattr(memory_recall_agent, "_passive_nexus_memory_evidence", fake_passive_nexus)
    monkeypatch.setattr(memory_recall_agent, "_experience_memory_evidence", fake_experience)

    async def _run():
        bundle = await memory_recall_agent.recall_relevant_memory(
            envelope=AgentInputEnvelope(
                turn_id="ck-unified-memory-sources",
                source=InputSource.TEXT,
                raw_text="open browser",
                normalized_text="open browser",
            ),
            state_snapshot=StateSnapshot(
                snapshot_id="state-unified-memory",
                generated_at_ms=1,
                freshness_ms=1,
                active_window={"app_name": "Codex"},
            ),
            prior_messages=[],
            max_results_per_channel=4,
        )
        assert "passive_nexus_profile_memory" in bundle.recall_request["retrieval_channels"]
        assert "experience_rag_memory" in bundle.recall_request["retrieval_channels"]
        assert any(item.memory_id == "passive-l0" for item in bundle.user_preferences)
        assert any(item.memory_id == "passive-l1" for item in bundle.historical_task_summaries)
        assert any(item.memory_id == "experience-tool" for item in bundle.tool_habits)

    asyncio.run(_run())


def test_passive_memory_prompt_snapshots_are_disabled():
    from l3_node.local_memory import get_local_memory_for_prompt
    from l3_node.memory_facade import snapshot_for_prompt

    assert get_local_memory_for_prompt() == ""
    assert snapshot_for_prompt() == ""


def test_cognitive_prompt_uses_section_6_memory_package():
    from l3_node.cognitive_kernel.contracts import (
        AgentInputEnvelope,
        InputSource,
        MemoryEvidence,
        RelevantMemoryBundle,
        StateSnapshot,
        TaskLedgerEntry,
    )
    from l3_node.cognitive_kernel.pipeline import CognitiveTurnContext

    envelope = AgentInputEnvelope(
        turn_id="ck-prompt-section-6",
        source=InputSource.TEXT,
        raw_text="close it",
        normalized_text="close it",
    )
    state = StateSnapshot(
        snapshot_id="state-prompt-section-6",
        generated_at_ms=1,
        freshness_ms=1,
        active_window={"app_name": "Calculator"},
    )
    memory = RelevantMemoryBundle(
        turn_id=envelope.turn_id,
        retrieval_summary="resolved close_app from recent action and active window",
        candidate_intents=["close_app"],
        candidate_task_domains=["desktop_app_control"],
        multi_queries={"query_2_candidate_intent": "close_app"},
        recent_actions=[
            MemoryEvidence(
                memory_id="recent-calc",
                memory_type="short_term_action",
                content="last_opened_app=Calculator",
                source="unit",
                confidence=0.9,
                ttl="recent",
            )
        ],
        user_preferences=[
            MemoryEvidence(
                memory_id="pref-confirm",
                memory_type="safety_preference",
                content="confirm before closing apps with unsaved work",
                source="unit",
                confidence=0.9,
                confirmed_by_user=True,
                ttl="permanent",
            )
        ],
        ranking_evidence=[{"memory_id": "recent-calc", "score": 0.88}],
        confidence=0.8,
    )
    ctx = CognitiveTurnContext(
        envelope=envelope,
        state_snapshot=state,
        memory_bundle=memory,
        ledger_entry=TaskLedgerEntry(
            turn_id=envelope.turn_id,
            input_envelope=envelope,
            state_snapshot=state,
            memory_bundle=memory,
        ),
    )
    block = ctx.prompt_block()
    assert "short_term_context" in block
    assert "long_term_context" in block
    assert "query_2_candidate_intent" in block
    assert "last_opened_app=Calculator" in block


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
            work_order_input='{"path":"README.md"}',
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
            work_order_input='{"recipients":["Vivian","Neil"],"text":"hello"}',
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
            work_order_input='{"value":1}',
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
            work_order_input='{"path":"notes/stage-d.txt","content":"stage d ok"}',
            executor=transport_should_not_run,
        )
        assert write.verification.ok is True
        assert "FileExecutorAgent.native" in write.observation

        read = await dispatch_tool_work_order(
            turn_id="ck-file-direct-read",
            goal="read note",
            tool="core:fs_read",
            work_order_input='{"path":"notes/stage-d.txt"}',
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
            work_order_input='{"recipients":["Neil"],"text":"hello"}',
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
            work_order_input='{"content":"User prefers role-agent evidence.","tags":["preference","stage-d"]}',
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
            work_order_input='{"path":"README.md"}',
        )
        work = build_work_order(
            contract=contract,
            tool="core:fs_read",
            work_order_input='{"path":"README.md"}',
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
        work_order_input='{"path":"README.md"}',
    )
    first = build_work_order(contract=contract, tool="core:fs_read", work_order_input='{"path":"README.md"}')
    second = build_work_order(contract=contract, tool="mcp:lark_send_text", work_order_input='{"recipients":["Neil"],"text":"ok"}')
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
            work_order_input='{"window_title":"Calculator"}',
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


def test_dispatcher_recovery_switches_paths_before_success(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    from l3_node.cognitive_kernel.dispatcher import dispatch_tool_work_order
    from l3_node.cognitive_kernel.ledger import current_ledger_path

    async def _run():
        seen: list[str] = []

        async def desktop_tool(work_order):
            recovery = work_order.inputs.get("recovery") if isinstance(work_order.inputs.get("recovery"), dict) else {}
            strategy = str(recovery.get("strategy") or "initial")
            seen.append(strategy)
            if strategy == "switch_existing_window":
                return '{"ok":true,"active_window":"Browser","screenshot_path":"C:/tmp/browser.png"}'
            return '{"ok":false,"error":"window_not_found"}'

        result = await dispatch_tool_work_order(
            turn_id="ck-app-recovery-switch-path",
            goal="switch browser",
            tool="mcp:windows_window_switch",
            work_order_input='{"keywords":"Browser","timeout":1}',
            executor=desktop_tool,
        )
        assert result.verification.ok is True
        assert seen == ["initial", "retry_same_path", "switch_existing_window"]
        assert result.attempts is not None
        assert [x["strategy"] for x in result.attempts] == ["initial", "retry_same_path", "switch_existing_window"]

    asyncio.run(_run())
    events = [json.loads(line) for line in current_ledger_path().read_text(encoding="utf-8").splitlines()]
    planned = [e for e in events if e["event_type"] == "recovery_attempt_planned"]
    assert [e["payload"]["strategy"] for e in planned] == ["retry_same_path", "switch_existing_window"]


def test_recovery_planner_uses_manifest_and_history_to_choose_next_path(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))
    manifest_root = tmp_path / "capabilities" / "com.example.visual-app"
    manifest_root.mkdir(parents=True)
    (manifest_root / "plugin.json").write_text(
        json.dumps(
            {
                "id": "com.example.visual-app",
                "name": "Visual App Control",
                "recovery_playbook": {
                    "targets": [
                        {
                            "id": "visual_app_control",
                            "role_agent": "AppControlExecutorAgent",
                            "tools": ["mcp:windows_window_switch"],
                            "max_attempts": 5,
                            "steps": [
                                {
                                    "strategy": "switch_by_visual_anchor",
                                    "tool": "mcp:windows_window_switch",
                                    "when": {
                                        "failure_any": ["window_not_found", "window"],
                                        "after_attempt": 2,
                                    },
                                    "action_template": {
                                        "keywords": "$window_hint",
                                        "timeout": 15.0,
                                        "visual_anchor": True,
                                    },
                                    "rationale": "after plain retry fails, use visual anchors declared by the capability",
                                    "priority": 1,
                                }
                            ],
                        }
                    ]
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("JACHIN_RECOVERY_MANIFEST_ROOTS", str(tmp_path / "capabilities"))

    from l3_node.cognitive_kernel.dispatcher import dispatch_tool_work_order

    async def _run():
        seen: list[str] = []

        async def desktop_tool(work_order):
            recovery = work_order.inputs.get("recovery") if isinstance(work_order.inputs.get("recovery"), dict) else {}
            strategy = str(recovery.get("strategy") or "initial")
            seen.append(strategy)
            if strategy == "switch_by_visual_anchor":
                payload = json.loads(str(work_order.inputs.get("work_order_input") or "{}"))
                assert payload["visual_anchor"] is True
                assert payload["timeout"] == 15.0
                return '{"ok":true,"active_window":"Browser","screenshot_path":"C:/tmp/browser.png"}'
            return '{"ok":false,"error":"window_not_found"}'

        result = await dispatch_tool_work_order(
            turn_id="ck-app-recovery-manifest-history",
            goal="switch browser",
            tool="mcp:windows_window_switch",
            work_order_input='{"keywords":"Browser","timeout":1}',
            executor=desktop_tool,
        )
        assert result.verification.ok is True
        assert seen == ["initial", "retry_same_path", "switch_by_visual_anchor"]
        assert result.attempts is not None
        assert [x["strategy"] for x in result.attempts] == ["initial", "retry_same_path", "switch_by_visual_anchor"]

    asyncio.run(_run())


def test_dispatcher_recovery_stops_with_final_failure_report(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))
    monkeypatch.setenv("JACHIN_RECOVERY_MAX_ATTEMPTS", "5")

    from l3_node.cognitive_kernel.dispatcher import dispatch_tool_work_order
    from l3_node.cognitive_kernel.ledger import current_ledger_path

    async def _run():
        calls = {"n": 0}

        async def always_fails(work_order):
            calls["n"] += 1
            return '{"ok":false,"error":"window_not_found"}'

        result = await dispatch_tool_work_order(
            turn_id="ck-app-recovery-final-failure",
            goal="switch missing app",
            tool="mcp:windows_window_switch",
            work_order_input='{"keywords":"MissingApp","timeout":1}',
            executor=always_fails,
        )
        assert result.verification.ok is False
        assert calls["n"] == 5
        assert result.final_failure_report is not None
        assert result.final_failure_report["max_attempts"] == 5
        assert result.final_failure_report["attempt_count"] == 5
        assert result.final_failure_report["final_failure_reason"] == "window_not_found"
        assert result.final_failure_report["recommended_next_steps"]

    asyncio.run(_run())
    events = [json.loads(line) for line in current_ledger_path().read_text(encoding="utf-8").splitlines()]
    assert any(e["event_type"] == "final_failure_report" for e in events)


def test_stage_f_policy_boundaries_live_in_kernel():
    from l3_node.cognitive_kernel import (
        RECALL_MEMORY_TOOL_ID,
        SQL_DATA_SOP_PROMPT,
        WORK_ORDER_ALIAS_IDS,
        build_fake_mcp_error_recovery_prompt,
        build_fake_weather_error_recovery_prompt,
        is_hallucinated_final_mcp_error_json,
        is_hallucinated_weather_service_error_json,
    )

    assert RECALL_MEMORY_TOOL_ID == "recall_memory"
    assert WORK_ORDER_ALIAS_IDS == ("recall_memory", "coordinate", "delegate")
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
