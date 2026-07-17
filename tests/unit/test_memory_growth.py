import json
from pathlib import Path


def test_memory_growth_scaffold_and_append_raw_event(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.memory_growth import (
        append_raw_event,
        ensure_memory_growth_scaffold,
        memory_growth_dir,
    )

    root = ensure_memory_growth_scaffold()
    assert root == memory_growth_dir()
    assert (root / "raw" / "evidence").is_dir()
    assert (root / "concepts" / "README.md").is_file()
    assert (root / "playbooks" / "README.md").is_file()

    path = append_raw_event(
        category="evidence",
        source="unit_test",
        stream="probe",
        payload={"hello": "memory growth"},
        source_refs=[{"type": "unit_test", "id": "raw-1"}],
    )
    assert path.exists()
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["schema_version"] == 1
    assert rows[0]["category"] == "evidence"
    assert rows[0]["source"] == "unit_test"
    assert rows[0]["payload"]["hello"] == "memory growth"
    assert rows[0]["review"]["review_candidate"] is True


def test_close_turn_records_memory_growth_raw_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.runtime import (
        build_decision_contract,
        build_work_order,
        close_turn,
        verify_work_order,
    )

    contract = build_decision_contract(
        turn_id="ck-memory-growth-close-1",
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
    close_turn(
        turn_id=contract.turn_id,
        final_text="done",
        executed_work_orders=[work.work_order_id],
        verification_reports=[report],
    )

    raw_root = tmp_path / "kernel" / "memory_growth" / "raw" / "evidence"
    paths = list(raw_root.glob("*.turn_closure.jsonl"))
    assert len(paths) == 1
    rows = [json.loads(line) for line in paths[0].read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "turn_closure_agent"
    assert row["payload"]["turn_id"] == contract.turn_id
    assert row["payload"]["closure"]["verification_status"] == "passed"
    assert row["payload"]["promotion_hints"]["has_executed_work_orders"] is True
    assert row["review"]["promotion_targets"] == ["concepts", "playbooks", "outputs"]
    assert any(ref.get("work_order_id") == work.work_order_id for ref in row["source_refs"])


def test_close_turn_waiting_user_records_pending_raw_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.runtime import close_turn_waiting_user

    close_turn_waiting_user(
        turn_id="ck-memory-growth-wait-1",
        final_text="需要确认",
        pending_decision={"decision_id": "d1", "requires_confirmation": True},
    )

    paths = list((tmp_path / "kernel" / "memory_growth" / "raw" / "evidence").glob("*.turn_closure.jsonl"))
    assert len(paths) == 1
    row = json.loads(paths[0].read_text(encoding="utf-8").splitlines()[0])
    assert row["payload"]["closure"]["pending_decision"]["decision_id"] == "d1"
    assert row["payload"]["promotion_hints"]["has_pending_decision"] is True
    assert row["review"]["priority"] == "high"


def test_daily_review_generates_review_and_patch_from_raw_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.daily_review import run_daily_review
    from l3_node.cognitive_kernel.runtime import (
        build_decision_contract,
        build_work_order,
        close_turn,
        verify_work_order,
    )

    contract = build_decision_contract(
        turn_id="ck-memory-growth-daily-1",
        goal="open calculator",
        tool="windows_app_open",
        work_order_input='{"app":"calculator"}',
    )
    work = build_work_order(
        contract=contract,
        tool="windows_app_open",
        work_order_input='{"app":"calculator"}',
    )
    report = verify_work_order(
        turn_id=contract.turn_id,
        work_order=work,
        observation='{"ok":true,"window":"Calculator"}',
        elapsed_ms=1.0,
    )
    close_turn(
        turn_id=contract.turn_id,
        final_text="calculator opened",
        executed_work_orders=[work.work_order_id],
        verification_reports=[report],
    )

    result = run_daily_review()
    assert result.raw_event_count == 1
    assert result.task_count == 1
    assert result.passed_count == 1
    assert result.failed_count == 0
    assert result.concept_candidate_count >= 1
    assert result.playbook_candidate_count >= 1
    assert result.output_candidate_count >= 1
    assert result.review_path.exists()
    assert result.patch_path.exists()

    patch = json.loads(result.patch_path.read_text(encoding="utf-8"))
    assert patch["schema_version"] == 1
    assert patch["source"] == "daily_review_agent"
    assert patch["task_summaries"][0]["turn_id"] == contract.turn_id
    assert patch["concept_candidates"][0]["source_refs"]
    assert "Daily Review" in result.review_path.read_text(encoding="utf-8")


def test_failure_learning_grows_reusable_experience_playbook(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.contracts import DecisionContract, VerificationReport, WorkOrder
    from l3_node.cognitive_kernel.daily_review import run_daily_review
    from l3_node.cognitive_kernel.failure_learning_loop import learn_from_failure
    from l3_node.cognitive_kernel.memory_growth import memory_growth_dir

    decision = DecisionContract(
        decision_id="decision-failure-growth-1",
        turn_id="turn-failure-growth-1",
        task_type="app_control",
        goal="open lark",
    )
    work_order = WorkOrder(
        work_order_id="wo-failure-growth-1",
        decision_id=decision.decision_id,
        role_agent="AppControlExecutorAgent",
        task="open_app",
        inputs={"tool": "mcp:windows_app_open", "app": "Lark"},
    )
    verification = VerificationReport(
        verification_id="vr-failure-growth-1",
        work_order_id=work_order.work_order_id,
        ok=False,
        evidence=[{"window": "Lock", "expected": "Lark"}],
        confidence=0.2,
        failure_reason="window_not_found: Lark",
    )

    record = learn_from_failure(
        turn_id=decision.turn_id,
        decision=decision,
        work_order=work_order,
        verification=verification,
        attempt_count=1,
    )
    assert record.failure_class == "target_not_found"

    raw_paths = list((memory_growth_dir() / "raw" / "evidence").glob("*.failure_learning.jsonl"))
    assert len(raw_paths) == 1
    raw_row = json.loads(raw_paths[0].read_text(encoding="utf-8").splitlines()[0])
    assert raw_row["source"] == "failure_learning_loop"
    assert raw_row["payload"]["failure_learning"]["next_strategy"] == "resolve_target_from_memory_or_ask_user"

    result = run_daily_review()
    assert result.learned_playbook_created_count == 1
    assert result.learned_playbook_updated_count == 0

    root = memory_growth_dir()
    learned_index = json.loads((root / "indexes" / "learned_playbooks.json").read_text(encoding="utf-8"))
    assert len(learned_index["playbooks"]) == 1
    learned = learned_index["playbooks"][0]
    assert learned["task_type"] == "app_control"
    assert learned["tool"] == "mcp:windows_app_open"
    assert learned["failure_class"] == "target_not_found"
    assert learned["next_strategy"] == "resolve_target_from_memory_or_ask_user"

    playbook_path = root / learned["path"]
    text = playbook_path.read_text(encoding="utf-8")
    assert "Learned Recovery Playbook" in text
    assert "resolve_target_from_memory_or_ask_user" in text
    assert "window_not_found" in text

    playbook_index = json.loads((root / "indexes" / "playbooks.json").read_text(encoding="utf-8"))
    assert any(row.get("path") == learned["path"] for row in playbook_index["playbooks"])


def test_experience_playbook_builder_dedupes_repeated_daily_review(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.memory_growth import append_raw_event, memory_growth_dir
    from l3_node.cognitive_kernel.daily_review import run_daily_review

    for idx in range(2):
        append_raw_event(
            category="evidence",
            source="unit_test_failure_learning",
            stream="failure_learning",
            payload={
                "turn_id": f"turn-repeat-{idx}",
                "failure_learning": {
                    "failure_id": f"failure-repeat-{idx}",
                    "task_type": "web_research_delivery",
                    "tool": "mcp:official-fetch",
                    "role_agent": "WebResearchExecutorAgent",
                    "failure_reason": "fetch_readable_content_missing",
                    "failure_class": "tool_quality_failed",
                    "attempt_count": 1,
                    "next_strategy": "switch_to_higher_quality_path_or_regenerate_output",
                    "rationale": ["fetch produced unreadable content"],
                },
            },
            source_refs=[{"type": "unit", "idx": idx}],
            review={"review_candidate": True, "promotion_targets": ["playbooks"], "priority": "high"},
        )

    first = run_daily_review()
    second = run_daily_review()
    assert first.learned_playbook_created_count == 1
    assert second.learned_playbook_created_count == 0
    assert second.learned_playbook_updated_count == 0

    root = memory_growth_dir()
    learned_index = json.loads((root / "indexes" / "learned_playbooks.json").read_text(encoding="utf-8"))
    assert len(learned_index["playbooks"]) == 1
    assert learned_index["playbooks"][0]["source_event_count"] == 2


def test_learned_experience_playbook_is_recalled_for_similar_task(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.contracts import AgentInputEnvelope, InputSource, MemoryRecallRequest
    from l3_node.cognitive_kernel.daily_review import run_daily_review
    from l3_node.cognitive_kernel.memory_growth import append_raw_event
    from l3_node.cognitive_kernel.memory_growth_recall import recall_memory_growth

    append_raw_event(
        category="evidence",
        source="unit_test_failure_learning",
        stream="failure_learning",
        payload={
            "turn_id": "turn-recall-learned-playbook",
            "failure_learning": {
                "failure_id": "failure-recall-learned-playbook",
                "task_type": "message_delivery",
                "tool": "mcp:windows_lark_send_message",
                "role_agent": "MessageExecutorAgent",
                "failure_reason": "post_send_verification_missing",
                "failure_class": "verification_missing",
                "attempt_count": 1,
                "next_strategy": "collect_evidence_then_retry_or_mark_uncertain",
                "rationale": ["message delivery must verify post-send evidence"],
            },
        },
        review={"review_candidate": True, "promotion_targets": ["playbooks"], "priority": "high"},
    )
    run_daily_review()

    envelope = AgentInputEnvelope(
        turn_id="turn-recall-query",
        source=InputSource.TEXT,
        raw_text="send lark message and verify it was sent",
        normalized_text="send lark message and verify it was sent",
    )
    request = MemoryRecallRequest(
        turn_id=envelope.turn_id,
        input_envelope=envelope,
        candidate_intents=["message_delivery"],
        candidate_task_domains=["lark", "message"],
        candidate_entities=["Neil", "Lark"],
        multi_queries={"goal": "Lark message delivery post send verification missing"},
    )
    memories, gaps = recall_memory_growth(request, limit=5)
    assert not gaps
    assert any(memory.memory_id.startswith("memory_growth:playbook:failure-message") for memory in memories)
    assert any("collect_evidence_then_retry_or_mark_uncertain" in memory.content for memory in memories)


def test_success_experience_playbook_is_built_and_indexed(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.daily_review import run_daily_review
    from l3_node.cognitive_kernel.memory_growth import append_raw_event, memory_growth_dir

    append_raw_event(
        category="evidence",
        source="unit_test_turn_closure",
        stream="turn_closure",
        payload={
            "turn_id": "turn-success-message-1",
            "closure": {
                "turn_id": "turn-success-message-1",
                "closure_type": "completed",
                "final_user_message_intent": "send Lark message to Neil",
                "executed_work_orders": ["decomp_open_lark_1", "decomp_send_lark_1"],
                "verification_status": "passed",
                "memory_write_requests": [],
                "next_turn_hints": [],
            },
        },
        review={"review_candidate": True, "promotion_targets": ["playbooks"], "priority": "normal"},
    )
    result = run_daily_review()
    assert result.learned_success_playbook_created_count == 1
    assert result.learned_success_playbook_updated_count == 0

    root = memory_growth_dir()
    success_index = json.loads((root / "indexes" / "learned_success_playbooks.json").read_text(encoding="utf-8"))
    assert len(success_index["playbooks"]) == 1
    learned = success_index["playbooks"][0]
    assert learned["type"] == "success_playbook"
    assert learned["task_type"] == "message_delivery"
    assert learned["success_strategy"] == "reuse_verified_message_delivery_chain"

    text = (root / learned["path"]).read_text(encoding="utf-8")
    assert "Learned Success Playbook" in text
    assert "reuse_verified_message_delivery_chain" in text

    playbook_index = json.loads((root / "indexes" / "playbooks.json").read_text(encoding="utf-8"))
    assert any(row.get("path") == learned["path"] and row.get("type") == "success_playbook" for row in playbook_index["playbooks"])


def test_success_experience_playbook_is_recalled_for_similar_task(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.contracts import AgentInputEnvelope, InputSource, MemoryRecallRequest
    from l3_node.cognitive_kernel.daily_review import run_daily_review
    from l3_node.cognitive_kernel.memory_growth import append_raw_event
    from l3_node.cognitive_kernel.memory_growth_recall import recall_memory_growth

    append_raw_event(
        category="evidence",
        source="unit_test_turn_closure",
        stream="turn_closure",
        payload={
            "turn_id": "turn-success-recall-1",
            "closure": {
                "turn_id": "turn-success-recall-1",
                "closure_type": "completed",
                "final_user_message_intent": "send Lark message to Neil",
                "executed_work_orders": ["decomp_open_lark_1", "decomp_send_lark_1"],
                "verification_status": "passed",
            },
        },
        review={"review_candidate": True, "promotion_targets": ["playbooks"], "priority": "normal"},
    )
    run_daily_review()

    envelope = AgentInputEnvelope(
        turn_id="turn-success-recall-query",
        source=InputSource.TEXT,
        raw_text="send a lark message to Neil and verify delivery",
        normalized_text="send a lark message to Neil and verify delivery",
    )
    request = MemoryRecallRequest(
        turn_id=envelope.turn_id,
        input_envelope=envelope,
        candidate_intents=["message_delivery"],
        candidate_task_domains=["lark", "message"],
        candidate_entities=["Neil", "Lark"],
        multi_queries={"goal": "send Lark message to Neil"},
    )
    memories, gaps = recall_memory_growth(request, limit=5)
    assert not gaps
    assert any(memory.memory_id.startswith("memory_growth:playbook:success-message") for memory in memories)
    assert any("success_strategy=reuse_verified_message_delivery_chain" in memory.content for memory in memories)


def test_task_decomposer_attaches_success_playbook_refs(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.contracts import DecisionContract, ReviewSummary, RiskLevel, ToolPolicy
    from l3_node.cognitive_kernel.task_decomposer import decompose_task

    success_ref = {
        "memory_id": "memory_growth:playbook:success-message-delivery-lark",
        "memory_type": "tool_habit",
        "source": "Memory Growth Playbooks",
        "confidence": 0.91,
        "artifact_path": "playbooks/learned_success/success-message-delivery-lark.md",
        "preview": "playbook path=...; success_strategy=reuse_verified_message_delivery_chain; flow=open Lark then send message",
        "relevance_reason": "success playbook matched current query",
    }
    contract = DecisionContract(
        decision_id="decision-success-decomp",
        turn_id="turn-success-decomp",
        task_type="message_delivery",
        goal="send Lark message to Neil",
        selected_roles=["AppControlExecutorAgent", "MessageExecutorAgent"],
        risk_level=RiskLevel.LOW,
        tool_policy=ToolPolicy(allowed_tools=["mcp:windows_lark_send_message"], risk_level=RiskLevel.LOW),
        verification_criteria=["message send evidence is visible"],
        memory_context_refs=[success_ref],
    )
    summary = ReviewSummary(
        review_session_id="review-success-decomp",
        turn_id=contract.turn_id,
        top_intent="message_send",
        task_type="message_delivery",
        target={"app": "Lark", "recipients": ["Neil"], "message": "hello"},
        selected_roles=["AppControlExecutorAgent", "MessageExecutorAgent"],
        candidate_tools=["mcp:windows_lark_send_message"],
        risk_level=RiskLevel.LOW,
        confidence=0.9,
    )
    plan = decompose_task(contract=contract, summary=summary)
    assert plan.nodes
    assert "learned success" in " ".join(plan.rationale).lower()
    assert all(node.inputs.get("preferred_success_playbooks") for node in plan.nodes)
    assert plan.nodes[-1].recovery_policy.get("preferred_success_playbooks")[0]["success_strategy"] == "reuse_verified_message_delivery_chain"
    assert plan.nodes[-1].inputs.get("preferred_execution_strategy") == "reuse_verified_message_delivery_chain"
    assert plan.nodes[-1].inputs.get("success_playbook_preference", {}).get("selected_memory_id") == success_ref["memory_id"]


def test_task_decomposer_prioritizes_best_success_playbook_ref(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.contracts import DecisionContract, ReviewSummary, RiskLevel, ToolPolicy
    from l3_node.cognitive_kernel.task_decomposer import decompose_task

    weaker_ref = {
        "memory_id": "memory_growth:playbook:success-message-delivery-generic",
        "memory_type": "tool_habit",
        "source": "Memory Growth Playbooks",
        "confidence": 0.62,
        "artifact_path": "playbooks/learned_success/success-message-delivery-generic.md",
        "preview": "playbook path=...; success_strategy=reuse_generic_message_path; work_order_chain=['open_app','send_message']; artifact_success_rate=0.60",
        "relevance_reason": "generic success playbook matched",
    }
    stronger_ref = {
        "memory_id": "memory_growth:playbook:success-message-delivery-lark-neil",
        "memory_type": "tool_habit",
        "source": "Memory Growth Playbooks",
        "confidence": 0.88,
        "artifact_path": "playbooks/learned_success/success-message-delivery-lark-neil.md",
        "preview": "playbook path=...; success_strategy=reuse_verified_lark_neil_delivery_chain; work_order_chain=['open_lark','resolve_recipient','send_lark_message','verify_delivery']; artifact_success_rate=0.94",
        "relevance_reason": "specific Neil delivery success playbook matched",
    }
    contract = DecisionContract(
        decision_id="decision-success-ranking",
        turn_id="turn-success-ranking",
        task_type="message_delivery",
        goal="send Lark message to Neil",
        selected_roles=["AppControlExecutorAgent", "MessageExecutorAgent"],
        risk_level=RiskLevel.LOW,
        tool_policy=ToolPolicy(allowed_tools=["mcp:windows_lark_send_message"], risk_level=RiskLevel.LOW),
        verification_criteria=["message send evidence is visible"],
        memory_context_refs=[weaker_ref, stronger_ref],
    )
    summary = ReviewSummary(
        review_session_id="review-success-ranking",
        turn_id=contract.turn_id,
        top_intent="message_send",
        task_type="message_delivery",
        target={"app": "Lark", "recipients": ["Neil"], "message": "hello"},
        selected_roles=["AppControlExecutorAgent", "MessageExecutorAgent"],
        candidate_tools=["mcp:windows_lark_send_message"],
        risk_level=RiskLevel.LOW,
        confidence=0.9,
    )

    plan = decompose_task(contract=contract, summary=summary)
    assert plan.nodes
    preference = plan.nodes[-1].inputs.get("success_playbook_preference")
    assert preference["selected_memory_id"] == stronger_ref["memory_id"]
    assert preference["preferred_execution_strategy"] == "reuse_verified_lark_neil_delivery_chain"
    assert preference["preferred_work_order_chain"] == [
        "open_lark",
        "resolve_recipient",
        "send_lark_message",
        "verify_delivery",
    ]
    ranked = plan.nodes[-1].inputs["preferred_success_playbooks"]
    assert ranked[0]["memory_id"] == stronger_ref["memory_id"]
    assert ranked[0]["rank"] == 1
    assert ranked[1]["rank"] == 2
    assert "preferred learned success strategy" in " ".join(plan.rationale).lower()


def test_task_decomposer_downranks_degraded_success_playbook_ref(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.contracts import DecisionContract, ReviewSummary, RiskLevel, ToolPolicy
    from l3_node.cognitive_kernel.task_decomposer import decompose_task

    degraded_ref = {
        "memory_id": "memory_growth:playbook:success-message-delivery-degraded",
        "memory_type": "tool_habit",
        "source": "Memory Growth Playbooks",
        "confidence": 0.96,
        "artifact_path": "playbooks/learned_success/success-message-delivery-degraded.md",
        "preview": (
            "playbook path=...; success_strategy=reuse_old_lark_delivery_chain; "
            "work_order_chain=['open_lark','send_lark_message']; artifact_success_rate=0.10; "
            "artifact_use_count=12; artifact_failure_count=10; artifact_last_failure_reason=post_send_verification_missing"
        ),
        "relevance_reason": "specific but degraded success playbook matched",
    }
    reliable_ref = {
        "memory_id": "memory_growth:playbook:success-message-delivery-reliable",
        "memory_type": "tool_habit",
        "source": "Memory Growth Playbooks",
        "confidence": 0.76,
        "artifact_path": "playbooks/learned_success/success-message-delivery-reliable.md",
        "preview": (
            "playbook path=...; success_strategy=reuse_verified_lark_delivery_chain; "
            "work_order_chain=['open_lark','resolve_recipient','send_lark_message','verify_delivery']; "
            "artifact_success_rate=0.82; artifact_use_count=8; artifact_failure_count=1"
        ),
        "relevance_reason": "reliable success playbook matched",
    }
    contract = DecisionContract(
        decision_id="decision-success-degraded-ranking",
        turn_id="turn-success-degraded-ranking",
        task_type="message_delivery",
        goal="send Lark message to Neil",
        selected_roles=["AppControlExecutorAgent", "MessageExecutorAgent"],
        risk_level=RiskLevel.LOW,
        tool_policy=ToolPolicy(allowed_tools=["mcp:windows_lark_send_message"], risk_level=RiskLevel.LOW),
        verification_criteria=["message send evidence is visible"],
        memory_context_refs=[degraded_ref, reliable_ref],
    )
    summary = ReviewSummary(
        review_session_id="review-success-degraded-ranking",
        turn_id=contract.turn_id,
        top_intent="message_send",
        task_type="message_delivery",
        target={"app": "Lark", "recipients": ["Neil"], "message": "hello"},
        selected_roles=["AppControlExecutorAgent", "MessageExecutorAgent"],
        candidate_tools=["mcp:windows_lark_send_message"],
        risk_level=RiskLevel.LOW,
        confidence=0.9,
    )

    plan = decompose_task(contract=contract, summary=summary)
    preference = plan.nodes[-1].inputs["success_playbook_preference"]
    ranked = plan.nodes[-1].inputs["preferred_success_playbooks"]
    assert preference["selected_memory_id"] == reliable_ref["memory_id"]
    assert preference["selected_health"] == "reliable"
    assert preference["selected_failure_count"] == 1
    assert ranked[0]["health"] == "reliable"
    assert ranked[1]["health"] == "degraded"
    assert ranked[1]["last_failure_reason"] == "post_send_verification_missing"


def test_success_playbook_usage_score_promotes_high_success_path(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.contracts import AgentInputEnvelope, InputSource, MemoryRecallRequest
    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold, memory_growth_dir
    from l3_node.cognitive_kernel.memory_growth_recall import recall_memory_growth

    ensure_memory_growth_scaffold()
    root = memory_growth_dir()
    learned_dir = root / "playbooks" / "learned_success"
    learned_dir.mkdir(parents=True, exist_ok=True)
    high_path = learned_dir / "success-message-delivery-lark-neil-high.md"
    low_path = learned_dir / "success-message-delivery-lark-neil-low.md"
    common_body = """

# Success path

## Trigger Conditions
Send Lark message to Neil and verify delivery.

## Recommended Flow
open_lark -> send_lark_message -> verify_delivery
"""
    high_path.write_text(
        """---
id: "playbook:success-message-delivery-lark-neil-high"
type: "success_playbook"
summary: "Verified Lark message delivery to Neil"
confidence: 0.80
success_strategy: "reuse_verified_lark_neil_delivery_chain"
work_order_chain: ["open_lark", "send_lark_message", "verify_delivery"]
memory_use_count: 10
memory_success_count: 9
memory_failure_count: 1
memory_success_rate: 0.9
---""" + common_body,
        encoding="utf-8",
    )
    low_path.write_text(
        """---
id: "playbook:success-message-delivery-lark-neil-low"
type: "success_playbook"
summary: "Verified Lark message delivery to Neil"
confidence: 0.80
success_strategy: "reuse_unstable_lark_neil_delivery_chain"
work_order_chain: ["open_lark", "send_lark_message", "verify_delivery"]
memory_use_count: 10
memory_success_count: 1
memory_failure_count: 9
memory_success_rate: 0.1
memory_last_failure_reason: "post_send_verification_missing"
---""" + common_body,
        encoding="utf-8",
    )
    (root / "indexes" / "playbooks.json").write_text(
        json.dumps(
            {
                "playbooks": [
                    {"path": str(low_path.relative_to(root)), "type": "success_playbook", "slug": low_path.stem},
                    {"path": str(high_path.relative_to(root)), "type": "success_playbook", "slug": high_path.stem},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    request = MemoryRecallRequest(
        turn_id="turn-success-usage-ranking",
        input_envelope=AgentInputEnvelope(
            turn_id="turn-success-usage-ranking",
            source=InputSource.TEXT,
            raw_text="send Lark message to Neil and verify delivery",
            normalized_text="send Lark message to Neil and verify delivery",
        ),
        candidate_intents=["message_delivery"],
        candidate_task_domains=["lark", "message"],
        candidate_entities=["Neil", "Lark"],
        multi_queries={"goal": "send Lark message to Neil verify delivery"},
    )

    memories, gaps = recall_memory_growth(request, limit=2)
    assert not gaps
    assert memories[0].memory_id.endswith("success-message-delivery-lark-neil-high")
    assert "artifact_success_rate=0.900" in memories[0].relevance_reason
    assert "artifact_last_failure_reason=post_send_verification_missing" in memories[1].relevance_reason


def test_arbiter_work_order_carries_success_execution_preference(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.arbiter import arbitrate_review_summary, build_work_orders_from_decision
    from l3_node.cognitive_kernel.contracts import ReviewSummary, RiskLevel

    success_ref = {
        "memory_id": "memory_growth:playbook:success-message-delivery-lark-neil",
        "memory_type": "tool_habit",
        "source": "Memory Growth Playbooks",
        "confidence": 0.93,
        "artifact_path": "playbooks/learned_success/success-message-delivery-lark-neil.md",
        "preview": "playbook path=...; success_strategy=reuse_verified_lark_neil_delivery_chain; work_order_chain=['open_lark','send_lark_message','verify_delivery']; artifact_success_rate=0.91",
        "relevance_reason": "specific Neil delivery success playbook matched",
    }
    summary = ReviewSummary(
        review_session_id="review-success-work-order",
        turn_id="turn-success-work-order",
        top_intent="message_send",
        task_type="message_delivery",
        target={"app": "Lark", "recipients": ["Neil"], "message": "hello"},
        selected_roles=["AppControlExecutorAgent", "MessageExecutorAgent"],
        candidate_tools=["mcp:windows_lark_send_message"],
        risk_level=RiskLevel.LOW,
        confidence=0.92,
        reviews=[
            type(
                "FakeReview",
                (),
                {
                    "evidence": [{"memory_growth_refs": [success_ref]}],
                },
            )()
        ],
    )

    contract = arbitrate_review_summary(summary, goal="send Lark message to Neil")
    work_orders = build_work_orders_from_decision(contract, summary)

    assert work_orders
    preference = work_orders[-1].inputs["execution_preference"]
    assert preference["source"] == "memory_growth_success_playbook"
    assert preference["selected_memory_id"] == success_ref["memory_id"]
    assert preference["preferred_execution_strategy"] == "reuse_verified_lark_neil_delivery_chain"
    assert preference["preferred_work_order_chain"] == ["open_lark", "send_lark_message", "verify_delivery"]
    assert work_orders[0].inputs["execution_order_advice"]["matched_step"] == "open_lark"
    assert work_orders[-1].inputs["execution_order_advice"]["matched_step"] == "send_lark_message"
    assert work_orders[-1].inputs["execution_order_advice"]["mode"] == "non_destructive"


def test_arbiter_selects_reliable_candidate_tool_over_degraded_first_candidate(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.arbiter import arbitrate_review_summary, build_work_orders_from_decision
    from l3_node.cognitive_kernel.contracts import ReviewSummary, RiskLevel

    degraded_ref = {
        "memory_id": "memory_growth:playbook:success-lark-legacy",
        "memory_type": "tool_habit",
        "source": "Memory Growth Playbooks",
        "confidence": 0.96,
        "preview": (
            "tool=mcp:windows_lark_send_message_legacy; "
            "success_strategy=reuse_legacy_lark_path; artifact_success_rate=0.10; "
            "artifact_use_count=12; artifact_failure_count=10; artifact_last_failure_reason=post_send_verification_missing"
        ),
        "relevance_reason": "legacy lark send path is degraded",
    }
    reliable_ref = {
        "memory_id": "memory_growth:playbook:success-lark-stable",
        "memory_type": "tool_habit",
        "source": "Memory Growth Playbooks",
        "confidence": 0.78,
        "preview": (
            "tool=mcp:windows_lark_send_message_stable; "
            "success_strategy=reuse_verified_lark_path; artifact_success_rate=0.90; "
            "artifact_use_count=9; artifact_failure_count=1"
        ),
        "relevance_reason": "stable lark send path is reliable",
    }
    summary = ReviewSummary(
        review_session_id="review-tool-reliability",
        turn_id="turn-tool-reliability",
        top_intent="message_send",
        task_type="message_delivery",
        target={"app": "Lark", "recipients": ["Neil"], "message": "hello"},
        selected_roles=["AppControlExecutorAgent", "MessageExecutorAgent"],
        candidate_tools=["mcp:windows_lark_send_message_legacy", "mcp:windows_lark_send_message_stable"],
        risk_level=RiskLevel.LOW,
        confidence=0.9,
        reviews=[
            type(
                "FakeReview",
                (),
                {
                    "evidence": [{"memory_growth_refs": [degraded_ref, reliable_ref]}],
                },
            )()
        ],
    )

    contract = arbitrate_review_summary(summary, goal="send Lark message to Neil")
    work_orders = build_work_orders_from_decision(contract, summary)

    assert contract.tool_policy.allowed_tools == ["mcp:windows_lark_send_message_stable"]
    reliability = work_orders[-1].inputs["candidate_tool_reliability"]
    assert reliability[0]["tool"] == "mcp:windows_lark_send_message_stable"
    assert reliability[0]["health"] == "reliable"
    assert reliability[1]["tool"] == "mcp:windows_lark_send_message_legacy"
    assert reliability[1]["health"] == "degraded"
    assert any("Memory Growth reliability" in line for line in contract.rationale)


def test_dispatcher_evidence_includes_candidate_tool_reliability(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    import asyncio

    from l3_node.cognitive_kernel.contracts import DecisionContract, RiskLevel, ToolPolicy, WorkOrder
    from l3_node.cognitive_kernel.dispatcher import dispatch_existing_work_order

    reliability = [
        {
            "tool": "mcp:stable_lark_send",
            "rank": 1,
            "selected": True,
            "health": "reliable",
            "success_rate": 0.9,
            "use_count": 9,
            "failure_count": 1,
        },
        {
            "tool": "mcp:legacy_lark_send",
            "rank": 2,
            "selected": False,
            "health": "degraded",
            "success_rate": 0.1,
            "use_count": 12,
            "failure_count": 10,
        },
    ]
    contract = DecisionContract(
        decision_id="decision-dispatch-tool-reliability",
        turn_id="turn-dispatch-tool-reliability",
        task_type="message_delivery",
        goal="send message",
        selected_roles=["ToolExecutionAgent"],
        risk_level=RiskLevel.LOW,
        tool_policy=ToolPolicy(allowed_tools=["mcp:stable_lark_send"], risk_level=RiskLevel.LOW),
        execution_allowed=True,
    )
    work = WorkOrder(
        work_order_id="work-dispatch-tool-reliability",
        decision_id=contract.decision_id,
        role_agent="ToolExecutionAgent",
        task="send message",
        inputs={
            "tool": "mcp:stable_lark_send",
            "work_order_input": '{"message":"hello"}',
            "candidate_tool_reliability": reliability,
        },
        tool_policy=contract.tool_policy,
    )

    async def fake_executor(_work_order):
        return json.dumps({"ok": True, "detail": "sent"}, ensure_ascii=False)

    async def _run():
        return await dispatch_existing_work_order(contract=contract, work_order=work, executor=fake_executor)

    result = asyncio.run(_run())
    role_evidence = next(item for item in result.verification.evidence if item.get("type") == "role_execution")
    assert role_evidence["selected_tool_reliability"]["tool"] == "mcp:stable_lark_send"
    assert role_evidence["selected_tool_reliability"]["health"] == "reliable"
    assert role_evidence["candidate_tool_reliability"][1]["health"] == "degraded"


def test_candidate_tool_reliability_end_to_end_feedback_and_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))
    monkeypatch.setenv("JACHIN_OS_EVIDENCE_GOVERNANCE_INDEX", str(tmp_path / "missing_governance.json"))

    import asyncio

    from l3_node.cognitive_kernel.arbiter import arbitrate_review_summary, build_work_orders_from_decision
    from l3_node.cognitive_kernel.contracts import ReviewSummary, RiskLevel
    from l3_node.cognitive_kernel.dispatcher import dispatch_existing_work_order
    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold, memory_growth_dir
    from l3_node.cognitive_kernel.runtime import close_turn
    from l3_node.memory_growth_http import apply_memory_growth_governance, memory_growth_status

    ensure_memory_growth_scaffold()
    root = memory_growth_dir()
    stable_path = root / "playbooks" / "learned_success" / "success-stable-tool-path.md"
    alternate_path = root / "playbooks" / "learned_success" / "success-alternate-tool-path.md"
    stable_path.parent.mkdir(parents=True, exist_ok=True)
    stable_path.write_text(
        """---
id: "playbook:success-stable-tool-path"
type: "success_playbook"
summary: "Stable tool path"
confidence: 0.82
memory_use_count: 3
memory_success_count: 3
memory_failure_count: 0
memory_success_rate: 1.0
---

# Stable tool path
""",
        encoding="utf-8",
    )
    alternate_path.write_text(
        """---
id: "playbook:success-alternate-tool-path"
type: "success_playbook"
summary: "Alternate tool path"
confidence: 0.76
memory_use_count: 4
memory_success_count: 3
memory_failure_count: 1
memory_success_rate: 0.75
---

# Alternate tool path
""",
        encoding="utf-8",
    )

    stable_ref = {
        "bucket": "success_playbooks",
        "memory_id": "memory_growth:playbook:success-stable-tool-path",
        "source": "Memory Growth Playbooks",
        "confidence": 0.82,
        "artifact_path": str(stable_path),
        "preview": (
            "tool=mcp:stable_tool_path; success_strategy=reuse_stable_tool_path; "
            "artifact_success_rate=1.0; artifact_use_count=3; artifact_failure_count=0"
        ),
    }
    alternate_ref = {
        "bucket": "success_playbooks",
        "memory_id": "memory_growth:playbook:success-alternate-tool-path",
        "source": "Memory Growth Playbooks",
        "confidence": 0.76,
        "artifact_path": str(alternate_path),
        "preview": (
            "tool=mcp:alternate_tool_path; success_strategy=reuse_alternate_tool_path; "
            "artifact_success_rate=0.75; artifact_use_count=4; artifact_failure_count=1"
        ),
    }

    def make_summary(turn_id: str, stable: dict, alternate: dict) -> ReviewSummary:
        return ReviewSummary(
            review_session_id=f"review-{turn_id}",
            turn_id=turn_id,
            top_intent="run_reliable_tool",
            task_type="tool_execution",
            target={"name": "reliable tool path", "payload": "hello"},
            selected_roles=["ToolExecutionAgent"],
            candidate_tools=["mcp:degraded_tool_path", "mcp:stable_tool_path", "mcp:alternate_tool_path"],
            risk_level=RiskLevel.LOW,
            confidence=0.9,
            reviews=[
                type(
                    "FakeReview",
                    (),
                    {
                        "evidence": [
                            {
                                "memory_growth_refs": [
                                    {
                                        "bucket": "success_playbooks",
                                        "memory_id": "memory_growth:playbook:success-degraded-tool-path",
                                        "source": "Memory Growth Playbooks",
                                        "confidence": 0.94,
                                        "preview": (
                                            "tool=mcp:degraded_tool_path; success_strategy=reuse_degraded_tool_path; "
                                            "artifact_success_rate=0.10; artifact_use_count=12; artifact_failure_count=10; "
                                            "artifact_last_failure_reason=verification_missing"
                                        ),
                                    },
                                    stable,
                                    alternate,
                                ]
                            }
                        ],
                    },
                )()
            ],
        )

    summary = make_summary("turn-tool-switch-1", stable_ref, alternate_ref)
    contract = arbitrate_review_summary(summary, goal="run reliable tool path")
    work = build_work_orders_from_decision(contract, summary)[-1]
    assert contract.tool_policy.allowed_tools == ["mcp:stable_tool_path"]
    assert work.inputs["candidate_tool_reliability"][0]["tool"] == "mcp:stable_tool_path"

    async def fake_executor(_work_order):
        return json.dumps(
            {
                "ok": True,
                "detail": "tool_completed_and_verified",
                "result_id": "result-stable-1",
                "verified": True,
            },
            ensure_ascii=False,
        )

    result = asyncio.run(dispatch_existing_work_order(contract=contract, work_order=work, executor=fake_executor))
    assert result.verification.ok is True
    close_turn(
        turn_id=contract.turn_id,
        final_text="sent",
        executed_work_orders=[work.work_order_id],
        verification_reports=[result.verification],
        memory_context_refs=[stable_ref],
    )
    stable_row = next(row for row in memory_growth_status()["monitoring"]["artifact_usage"] if row["path"] == "playbooks/learned_success/success-stable-tool-path.md")
    assert stable_row["memory_success_count"] == 4

    degraded_stable_ref = {
        **stable_ref,
        "preview": (
            "tool=mcp:stable_tool_path; success_strategy=reuse_stable_tool_path; "
            "artifact_success_rate=0.25; artifact_use_count=12; artifact_failure_count=9; "
            "artifact_last_failure_reason=verification_missing"
        ),
    }
    healthier_alternate_ref = {
        **alternate_ref,
        "preview": (
            "tool=mcp:alternate_tool_path; success_strategy=reuse_alternate_tool_path; "
            "artifact_success_rate=0.82; artifact_use_count=9; artifact_failure_count=1"
        ),
    }
    second_summary = make_summary("turn-tool-switch-2", degraded_stable_ref, healthier_alternate_ref)
    second_contract = arbitrate_review_summary(second_summary, goal="run reliable tool path")
    second_work = build_work_orders_from_decision(second_contract, second_summary)[-1]
    assert second_contract.tool_policy.allowed_tools == ["mcp:alternate_tool_path"]
    assert second_work.inputs["candidate_tool_reliability"][0]["tool"] == "mcp:alternate_tool_path"
    stable_candidate = next(item for item in second_work.inputs["candidate_tool_reliability"] if item["tool"] == "mcp:stable_tool_path")
    assert stable_candidate["health"] == "degraded"


def test_role_execution_evidence_includes_success_execution_preference():
    from l3_node.cognitive_kernel.contracts import RiskLevel, ToolPolicy, WorkOrder
    from l3_node.cognitive_kernel.role_executors import RoleExecutionAdapter, RoleExecutionContext

    preference = {
        "source": "memory_growth_success_playbook",
        "selection_reason": "highest_confidence_verified_success_path",
        "selected_memory_id": "memory_growth:playbook:success-message-delivery-lark-neil",
        "selected_confidence": 0.93,
        "selected_success_rate": 0.91,
        "preferred_execution_strategy": "reuse_verified_lark_neil_delivery_chain",
        "preferred_work_order_chain": ["open_lark", "send_lark_message", "verify_delivery"],
        "candidate_count": 2,
    }
    work_order = WorkOrder(
        work_order_id="work-success-evidence",
        decision_id="decision-success-evidence",
        role_agent="ToolExecutionAgent",
        task="send Lark message",
        inputs={"tool": "mcp:windows_lark_send_message", "execution_preference": preference},
        tool_policy=ToolPolicy(allowed_tools=["mcp:windows_lark_send_message"], risk_level=RiskLevel.LOW),
    )
    context = RoleExecutionContext(
        turn_id="turn-success-evidence",
        goal="send Lark message",
        tool="mcp:windows_lark_send_message",
        role_id="ToolExecutionAgent",
        metadata={"execution_preference": preference},
    )
    evidence = RoleExecutionAdapter().describe_evidence(work_order, context)
    assert evidence["execution_preference"]["selected_memory_id"] == preference["selected_memory_id"]
    assert evidence["execution_preference"]["preferred_execution_strategy"] == "reuse_verified_lark_neil_delivery_chain"


def test_turn_closure_writes_success_playbook_usage_feedback(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.contracts import VerificationReport
    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold, memory_growth_dir
    from l3_node.cognitive_kernel.runtime import close_turn

    ensure_memory_growth_scaffold()
    root = memory_growth_dir()
    playbook_path = root / "playbooks" / "learned_success" / "success-message-delivery-lark-neil.md"
    playbook_path.parent.mkdir(parents=True, exist_ok=True)
    playbook_path.write_text(
        """---
id: "playbook:success-message-delivery-lark-neil"
type: "success_playbook"
summary: "Verified Lark message delivery to Neil"
confidence: 0.93
memory_use_count: 1
memory_success_count: 1
memory_failure_count: 0
memory_success_rate: 1.0
---

# Verified Lark message delivery to Neil
""",
        encoding="utf-8",
    )
    refs = [
        {
            "bucket": "success_playbooks",
            "memory_id": "memory_growth:playbook:success-message-delivery-lark-neil",
            "source": "Memory Growth Playbooks",
            "artifact_path": str(playbook_path),
            "preview": "playbook path=" + str(playbook_path) + "; success_strategy=reuse_verified_lark_neil_delivery_chain; artifact_success_rate=1.0",
        }
    ]

    close_turn(
        turn_id="turn-success-playbook-feedback",
        final_text="sent",
        executed_work_orders=["work-open-lark", "work-send-lark"],
        verification_reports=[VerificationReport(verification_id="verify-send", work_order_id="work-send-lark", ok=True)],
        memory_context_refs=refs,
    )

    text = playbook_path.read_text(encoding="utf-8")
    assert "memory_use_count: 2" in text
    assert "memory_success_count: 2" in text
    assert "memory_failure_count: 0" in text
    assert "memory_success_rate: 1.0" in text
    usage_index = json.loads((root / "indexes" / "artifact_usage.json").read_text(encoding="utf-8"))
    row = next(item for item in usage_index["artifacts"] if item["path"] == "playbooks/learned_success/success-message-delivery-lark-neil.md")
    assert row["memory_success_rate"] == 1.0


def test_recovery_planner_live_recalls_learned_playbook_without_contract_refs(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.contracts import DecisionContract, RiskLevel, ToolPolicy, VerificationReport, WorkOrder
    from l3_node.cognitive_kernel.daily_review import run_daily_review
    from l3_node.cognitive_kernel.memory_growth import append_raw_event
    from l3_node.cognitive_kernel.recovery_planner import RecoveryPlanner

    append_raw_event(
        category="evidence",
        source="unit_test_failure_learning",
        stream="failure_learning",
        payload={
            "turn_id": "turn-live-recall-recovery",
            "failure_learning": {
                "failure_id": "failure-live-recall-recovery",
                "task_type": "app_control",
                "tool": "mcp:windows_window_switch",
                "role_agent": "AppControlExecutorAgent",
                "failure_reason": "timeout waiting for foreground window",
                "failure_class": "timeout_or_connection",
                "attempt_count": 1,
                "next_strategy": "retry_with_longer_timeout_or_offline_path",
                "rationale": ["window focus timeout should retry with longer timeout before reporting failure"],
            },
        },
        review={"review_candidate": True, "promotion_targets": ["playbooks"], "priority": "high"},
    )
    run_daily_review()

    contract = DecisionContract(
        decision_id="decision-live-recall-recovery",
        turn_id="turn-live-recall-recovery-query",
        task_type="app_control",
        goal="open browser and focus foreground window",
        selected_roles=["AppControlExecutorAgent"],
        risk_level=RiskLevel.LOW,
        tool_policy=ToolPolicy(allowed_tools=["mcp:windows_window_switch"], risk_level=RiskLevel.LOW),
        execution_allowed=True,
        memory_context_refs=[],
    )
    work = WorkOrder(
        work_order_id="work-live-recall-recovery",
        decision_id=contract.decision_id,
        role_agent="AppControlExecutorAgent",
        task="app_control",
        inputs={"tool": "mcp:windows_window_switch", "work_order_input": '{"window_title":"Chrome"}'},
        tool_policy=contract.tool_policy,
    )
    verification = VerificationReport(
        verification_id="verify-live-recall-recovery",
        work_order_id=work.work_order_id,
        ok=False,
        failure_reason="timeout waiting for foreground window",
    )
    planner = RecoveryPlanner(max_attempts=3, registry=type("EmptyRegistry", (), {
        "max_attempts_for": lambda self, **kwargs: kwargs.get("default", 3),
        "select_next": lambda self, **kwargs: None,
        "candidate_snapshot": lambda self, **kwargs: [],
    })())

    attempt = planner.next_attempt(
        contract=contract,
        failed_work_order=work,
        verification=verification,
        attempt_records=[],
    )

    assert attempt is not None
    assert attempt.strategy == "retry_with_longer_timeout_or_offline_path"
    assert attempt.candidate_path["metadata"]["source"] == "memory_growth"
    assert attempt.candidate_path["metadata"]["memory_growth_lookup"]["learned_next_strategy"] == "retry_with_longer_timeout_or_offline_path"
    assert attempt.candidate_path["metadata"]["memory_growth_lookup"]["ref_count"] >= 1
    patched = json.loads(attempt.work_order.inputs["work_order_input"])
    assert patched["timeout"] == 12.0
    assert patched["recovery_strategy"] == "retry_with_longer_timeout_or_offline_path"


def test_recovery_planner_downranks_degraded_learned_playbook(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.contracts import DecisionContract, RiskLevel, ToolPolicy, VerificationReport, WorkOrder
    from l3_node.cognitive_kernel.recovery_planner import RecoveryPlanner

    degraded_ref = {
        "bucket": "tool_habits",
        "memory_id": "memory_growth:playbook:degraded-window-focus",
        "source": "Memory Growth Playbooks",
        "preview": (
            "next_strategy=retry_with_longer_timeout_or_offline_path; "
            "artifact_success_rate=0.10; artifact_use_count=10; "
            "artifact_failure_count=9; artifact_last_failure_reason=window_focus_timeout"
        ),
        "relevance_reason": "artifact_success_rate=0.10; artifact_failure_count=9",
    }
    contract = DecisionContract(
        decision_id="decision-degraded-playbook",
        turn_id="turn-degraded-playbook",
        task_type="app_control",
        goal="switch to browser window",
        selected_roles=["AppControlExecutorAgent"],
        risk_level=RiskLevel.LOW,
        tool_policy=ToolPolicy(allowed_tools=["mcp:windows_window_switch"], risk_level=RiskLevel.LOW),
        execution_allowed=True,
        memory_context_refs=[degraded_ref],
    )
    work = WorkOrder(
        work_order_id="work-degraded-playbook",
        decision_id=contract.decision_id,
        role_agent="AppControlExecutorAgent",
        task="app_control",
        inputs={"tool": "mcp:windows_window_switch", "work_order_input": '{"window_title":"Chrome"}'},
        tool_policy=contract.tool_policy,
    )
    verification = VerificationReport(
        verification_id="verify-degraded-playbook",
        work_order_id=work.work_order_id,
        ok=False,
        failure_reason="window_focus_timeout",
    )
    planner = RecoveryPlanner(max_attempts=3, registry=type("EmptyRegistry", (), {
        "max_attempts_for": lambda self, **kwargs: kwargs.get("default", 3),
        "select_next": lambda self, **kwargs: None,
        "candidate_snapshot": lambda self, **kwargs: [],
    })())

    attempt = planner.next_attempt(
        contract=contract,
        failed_work_order=work,
        verification=verification,
        attempt_records=[],
    )

    assert attempt is not None
    metadata = attempt.candidate_path["metadata"]
    assert metadata["artifact_usage_multiplier"] < 1.0
    assert metadata["artifact_usage_health"]["degraded"] is True
    assert "memory_growth:playbook:degraded-window-focus" in metadata["artifact_usage_health"]["degraded_refs"]
    assert attempt.candidate_path["priority"] < 80


def test_concept_curator_promotes_stable_candidates_and_quarantines_weak_ones(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.concept_curator import apply_concept_patch
    from l3_node.cognitive_kernel.memory_growth import append_raw_event, ensure_memory_growth_scaffold, memory_growth_dir

    ensure_memory_growth_scaffold()
    patch_path = memory_growth_dir() / "reviews" / "patches" / "2026-07-10.daily_review.patch.json"
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    patch_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "review_id": "daily_2026-07-10_unit",
                "date": "2026-07-10",
                "source": "daily_review_agent",
                "summary": {},
                "task_summaries": [],
                "concept_candidates": [
                    {
                        "candidate_id": "concept:turn-1:project_fact:abc",
                        "target_type": "project_fact",
                        "summary": "Jachin uses Memory Growth raw evidence before concept promotion.",
                        "confidence": 0.86,
                        "requires_user_confirmation": False,
                        "merge_policy": "dedupe_and_merge",
                        "source_refs": [{"type": "raw_event", "event_id": "raw-1"}],
                    },
                    {
                        "candidate_id": "concept:turn-2:project_fact:def",
                        "target_type": "project_fact",
                        "summary": "This weak fact should wait for more evidence.",
                        "confidence": 0.4,
                        "requires_user_confirmation": False,
                        "merge_policy": "dedupe_and_merge",
                        "source_refs": [{"type": "raw_event", "event_id": "raw-2"}],
                    },
                    {
                        "candidate_id": "concept:turn-3:preference:ghi",
                        "target_type": "preference",
                        "summary": "User confirmation is required before storing this preference.",
                        "confidence": 0.9,
                        "requires_user_confirmation": True,
                        "merge_policy": "dedupe_and_merge",
                        "source_refs": [{"type": "raw_event", "event_id": "raw-3"}],
                    },
                ],
                "playbook_candidates": [],
                "output_candidates": [],
                "warnings": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = apply_concept_patch(patch_path)
    assert result.promoted_count == 1
    assert result.quarantined_count == 2
    assert result.skipped_count == 0
    assert len(result.concept_paths) == 1
    assert len(result.conflict_paths) == 2
    assert result.report_path and result.report_path.exists()

    concept_text = result.concept_paths[0].read_text(encoding="utf-8")
    assert "Memory Growth raw evidence" in concept_text
    assert "raw-1" in concept_text
    assert (memory_growth_dir() / "indexes" / "concepts.json").exists()
    conflict_reasons = [json.loads(path.read_text(encoding="utf-8"))["reason"] for path in result.conflict_paths]
    assert any(reason.startswith("low_confidence") for reason in conflict_reasons)
    assert "requires_user_confirmation" in conflict_reasons


def test_concept_curator_appends_non_conflicting_updates(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.concept_curator import apply_concept_patch
    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold, memory_growth_dir

    ensure_memory_growth_scaffold()
    patch_path = memory_growth_dir() / "reviews" / "patches" / "2026-07-10.daily_review.patch.json"
    patch_path.parent.mkdir(parents=True, exist_ok=True)

    base = {
        "schema_version": 1,
        "review_id": "daily_2026-07-10_unit",
        "date": "2026-07-10",
        "source": "daily_review_agent",
        "summary": {},
        "task_summaries": [],
        "playbook_candidates": [],
        "output_candidates": [],
        "warnings": [],
    }
    base["concept_candidates"] = [
        {
            "candidate_id": "concept:turn-1:project_fact:abc",
            "target_type": "project_fact",
            "summary": "Jachin Memory Growth records raw evidence for later review.",
            "confidence": 0.86,
            "source_refs": [{"type": "raw_event", "event_id": "raw-1"}],
        }
    ]
    patch_path.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
    first = apply_concept_patch(patch_path)

    base["review_id"] = "daily_2026-07-11_unit"
    base["concept_candidates"] = [
        {
            "candidate_id": "concept:turn-1:project_fact:abc",
            "target_type": "project_fact",
            "summary": "Jachin Memory Growth records raw evidence and source refs for later review.",
            "confidence": 0.88,
            "source_refs": [{"type": "raw_event", "event_id": "raw-4"}],
        }
    ]
    patch_path.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
    second = apply_concept_patch(patch_path)

    assert first.concept_paths == second.concept_paths
    assert second.promoted_count == 1
    text = second.concept_paths[0].read_text(encoding="utf-8")
    assert "raw-1" in text
    assert "raw-4" in text
    assert "daily_2026-07-11_unit" in text


def test_playbook_builder_promotes_usable_candidates_and_quarantines_weak_ones(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold, memory_growth_dir
    from l3_node.cognitive_kernel.playbook_builder import apply_playbook_patch

    ensure_memory_growth_scaffold()
    patch_path = memory_growth_dir() / "reviews" / "patches" / "2026-07-10.daily_review.patch.json"
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    patch_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "review_id": "daily_2026-07-10_unit",
                "date": "2026-07-10",
                "source": "daily_review_agent",
                "summary": {},
                "task_summaries": [],
                "concept_candidates": [],
                "playbook_candidates": [
                    {
                        "candidate_id": "playbook:turn-1:success:abc",
                        "title": "Repeatable app control flow",
                        "trigger": {
                            "sources": ["turn_closure_agent"],
                            "categories": ["evidence"],
                            "memory_types": ["short_term_action"],
                        },
                        "recommended_flow": [
                            {
                                "step": "reuse_work_order_chain",
                                "work_order_ids": ["work-1"],
                                "verification_status": "passed",
                            }
                        ],
                        "evidence_required": ["work_order", "verification_report", "turn_closure"],
                        "source_refs": [{"type": "raw_event", "event_id": "raw-1"}],
                        "confidence": 0.72,
                    },
                    {
                        "candidate_id": "playbook:turn-2:success:def",
                        "title": "Weak flow",
                        "trigger": {},
                        "recommended_flow": [{"step": "maybe"}],
                        "evidence_required": [],
                        "source_refs": [{"type": "raw_event", "event_id": "raw-2"}],
                        "confidence": 0.2,
                    },
                ],
                "output_candidates": [],
                "warnings": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = apply_playbook_patch(patch_path)
    assert result.promoted_count == 1
    assert result.quarantined_count == 1
    assert result.skipped_count == 0
    assert len(result.playbook_paths) == 1
    assert len(result.quarantine_paths) == 1
    assert result.report_path and result.report_path.exists()

    text = result.playbook_paths[0].read_text(encoding="utf-8")
    assert "Repeatable app control flow" in text
    assert "reuse_work_order_chain" in text
    assert "work-1" in text
    assert "raw-1" in text
    assert (memory_growth_dir() / "indexes" / "playbooks.json").exists()
    reason = json.loads(result.quarantine_paths[0].read_text(encoding="utf-8"))["reason"]
    assert reason.startswith("low_confidence")


def test_playbook_builder_appends_existing_playbook(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold, memory_growth_dir
    from l3_node.cognitive_kernel.playbook_builder import apply_playbook_patch

    ensure_memory_growth_scaffold()
    patch_path = memory_growth_dir() / "reviews" / "patches" / "2026-07-10.daily_review.patch.json"
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    patch = {
        "schema_version": 1,
        "review_id": "daily_2026-07-10_unit",
        "date": "2026-07-10",
        "source": "daily_review_agent",
        "summary": {},
        "task_summaries": [],
        "concept_candidates": [],
        "playbook_candidates": [
            {
                "candidate_id": "playbook:turn-1:success:abc",
                "title": "Repeatable message flow",
                "trigger": {"sources": ["turn_closure_agent"]},
                "recommended_flow": [{"step": "send_message", "work_order_ids": ["work-1"]}],
                "evidence_required": ["message_id"],
                "source_refs": [{"type": "raw_event", "event_id": "raw-1"}],
                "confidence": 0.7,
            }
        ],
        "output_candidates": [],
        "warnings": [],
    }
    patch_path.write_text(json.dumps(patch, ensure_ascii=False), encoding="utf-8")
    first = apply_playbook_patch(patch_path)

    patch["review_id"] = "daily_2026-07-11_unit"
    patch["playbook_candidates"][0]["recommended_flow"] = [{"step": "verify_send", "work_order_ids": ["work-2"]}]
    patch["playbook_candidates"][0]["source_refs"] = [{"type": "raw_event", "event_id": "raw-3"}]
    patch_path.write_text(json.dumps(patch, ensure_ascii=False), encoding="utf-8")
    second = apply_playbook_patch(patch_path)

    assert first.playbook_paths == second.playbook_paths
    assert second.promoted_count == 1
    text = second.playbook_paths[0].read_text(encoding="utf-8")
    assert "work-1" in text
    assert "work-2" in text
    assert "raw-1" in text
    assert "raw-3" in text
    assert "daily_2026-07-11_unit" in text


def test_output_review_promotes_user_facing_outputs(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold, memory_growth_dir
    from l3_node.cognitive_kernel.output_review import apply_output_patch

    ensure_memory_growth_scaffold()
    patch_path = memory_growth_dir() / "reviews" / "patches" / "2026-07-10.daily_review.patch.json"
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    patch_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "review_id": "daily_2026-07-10_unit",
                "date": "2026-07-10",
                "source": "daily_review_agent",
                "summary": {},
                "task_summaries": [],
                "concept_candidates": [],
                "playbook_candidates": [],
                "output_candidates": [
                    {
                        "candidate_id": "output:turn-1:raw-1",
                        "target_type": "lark_messages",
                        "summary": "Task turn-1 sent a user-facing Lark summary.",
                        "content": "Neil，Jachin 今日已完成 Memory Growth 输出回流。",
                        "verification_status": "passed",
                        "closure_type": "completed",
                        "source_refs": [{"type": "raw_event", "event_id": "raw-1"}],
                        "confidence": 0.45,
                    },
                    {
                        "candidate_id": "output:turn-2:raw-2",
                        "target_type": "work_records",
                        "summary": "",
                        "content": "",
                        "source_refs": [{"type": "raw_event", "event_id": "raw-2"}],
                        "confidence": 0.9,
                    },
                ],
                "warnings": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = apply_output_patch(patch_path)
    assert result.promoted_count == 1
    assert result.skipped_count == 1
    assert result.quarantined_count == 0
    assert len(result.output_paths) == 1
    assert result.report_path and result.report_path.exists()

    text = result.output_paths[0].read_text(encoding="utf-8")
    assert "Memory Growth 输出回流" in text
    assert "raw-1" in text
    assert "Verification status" in text
    assert (memory_growth_dir() / "indexes" / "outputs.json").exists()


def test_growth_scheduler_runs_full_pipeline(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.growth_scheduler import run_growth_pipeline
    from l3_node.cognitive_kernel.runtime import (
        build_decision_contract,
        build_work_order,
        close_turn,
        verify_work_order,
    )

    contract = build_decision_contract(
        turn_id="ck-memory-growth-pipeline-1",
        goal="read project summary source",
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
        observation='{"ok":true,"content":"Jachin memory growth source"}',
        elapsed_ms=1.0,
    )
    close_turn(
        turn_id=contract.turn_id,
        final_text="Neil，Jachin 已完成一条自生长知识系统简报。",
        executed_work_orders=[work.work_order_id],
        verification_reports=[report],
    )

    result = run_growth_pipeline()
    assert result.daily_review.raw_event_count == 1
    assert result.concept_result is not None
    assert result.playbook_result is not None
    assert result.output_result is not None
    assert result.concept_result.promoted_count >= 1
    assert result.playbook_result.promoted_count >= 1
    assert result.output_result.promoted_count >= 1
    assert result.report_path.exists()

    payload = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert payload["summary"]["output_promoted"] >= 1
    assert payload["stages"]["daily_review"]["raw_event_count"] == 1


def test_weekly_review_detects_lifecycle_issues(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold, memory_growth_dir
    from l3_node.cognitive_kernel.weekly_review import run_weekly_review

    ensure_memory_growth_scaffold()
    root = memory_growth_dir()
    concept_dir = root / "concepts" / "project_fact"
    concept_dir.mkdir(parents=True, exist_ok=True)
    concept_text = (
        "---\n"
        'summary: "Jachin Memory Growth keeps source-backed concepts."\n'
        'source_refs: [{"type":"raw_event","event_id":"raw-1"}]\n'
        "confidence: 0.86\n"
        'last_verified: "2026-05-01T00:00:00+0800"\n'
        'valid_until: ""\n'
        "---\n\n"
        "# Jachin Memory Growth keeps source-backed concepts.\n\n"
        "## Source Evidence\n\n"
        '- `{"type":"raw_event","event_id":"raw-1"}`\n'
    )
    (concept_dir / "memory-growth-source-backed-a.md").write_text(concept_text, encoding="utf-8")
    (concept_dir / "memory-growth-source-backed-b.md").write_text(concept_text.replace("raw-1", "raw-2"), encoding="utf-8")

    output_dir = root / "outputs" / "lark_messages"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "weak-output.md").write_text(
        "---\n"
        'summary: "Weak output"\n'
        "source_refs: []\n"
        "confidence: 0.1\n"
        'verification_status: "failed"\n'
        "---\n\n"
        "# Weak output\n\n"
        "short\n",
        encoding="utf-8",
    )
    conflict_dir = root / "conflicts" / "project_fact"
    conflict_dir.mkdir(parents=True, exist_ok=True)
    (conflict_dir / "conflict.json").write_text(
        json.dumps({"reason": "low_confidence:0.20", "candidate": {"candidate_id": "c1"}}, ensure_ascii=False),
        encoding="utf-8",
    )

    result = run_weekly_review(week_start="2026-07-06", stale_after_days=30)
    assert result.concept_count == 2
    assert result.output_count == 1
    assert result.conflict_count == 1
    assert result.duplicate_cluster_count == 1
    assert result.stale_concept_count == 2
    assert result.weak_output_count == 1
    assert result.report_path.exists()
    assert result.markdown_path.exists()

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["summary"]["duplicate_cluster_count"] == 1
    assert any(item["pattern"] == "low_confidence:0.20" for item in report["failure_patterns"])
    assert (root / "indexes" / "weekly_lifecycle.json").exists()
    assert "Weekly Memory Growth Review" in result.markdown_path.read_text(encoding="utf-8")


def test_growth_scheduler_can_include_weekly_lifecycle_review(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.growth_scheduler import run_growth_pipeline
    from l3_node.cognitive_kernel.runtime import (
        build_decision_contract,
        build_work_order,
        close_turn,
        verify_work_order,
    )

    contract = build_decision_contract(
        turn_id="ck-memory-growth-weekly-pipeline-1",
        goal="write a weekly-ready summary",
        tool="core:reply",
        work_order_input='{"message":"weekly summary"}',
    )
    work = build_work_order(
        contract=contract,
        tool="core:reply",
        work_order_input='{"message":"weekly summary"}',
    )
    report = verify_work_order(
        turn_id=contract.turn_id,
        work_order=work,
        observation='{"ok":true}',
        elapsed_ms=1.0,
    )
    close_turn(
        turn_id=contract.turn_id,
        final_text="本周 Memory Growth 管线已完成初步闭环。",
        executed_work_orders=[work.work_order_id],
        verification_reports=[report],
    )

    result = run_growth_pipeline(weekly_lifecycle_review=True)
    assert result.weekly_result is not None
    assert result.weekly_result.report_path.exists()

    payload = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert payload["stages"]["weekly_review"] is not None
    assert "weekly_lifecycle_issues" in payload["summary"]


def test_graph_sync_adapter_derives_nodes_and_edges(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.graph_sync_adapter import sync_memory_growth_graph
    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold, memory_growth_dir

    ensure_memory_growth_scaffold()
    root = memory_growth_dir()
    concept_dir = root / "concepts" / "project_fact"
    concept_dir.mkdir(parents=True, exist_ok=True)
    (concept_dir / "jachin-memory-growth.md").write_text(
        "---\n"
        'summary: "Jachin Memory Growth stores source-backed concepts."\n'
        'source_refs: [{"type":"raw_event","event_id":"raw-graph-1"}]\n'
        "confidence: 0.9\n"
        "---\n\n"
        "# Jachin Memory Growth stores source-backed concepts.\n\n"
        "Memory Growth keeps evidence and concepts connected.\n",
        encoding="utf-8",
    )
    output_dir = root / "outputs" / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "memory-growth-report.md").write_text(
        "---\n"
        'summary: "Memory Growth report explains source-backed concepts."\n'
        'source_refs: [{"type":"raw_event","event_id":"raw-graph-2"}]\n'
        "confidence: 0.8\n"
        'verification_status: "passed"\n'
        "---\n\n"
        "# Memory Growth report explains source-backed concepts.\n\n"
        "The report explains how Memory Growth connects evidence and concepts.\n",
        encoding="utf-8",
    )

    result = sync_memory_growth_graph()
    assert result.node_count >= 4
    assert result.edge_count >= 3
    assert result.event_path.exists()
    assert result.node_index_path.exists()
    assert result.edge_index_path.exists()

    nodes = json.loads(result.node_index_path.read_text(encoding="utf-8"))["nodes"]
    edges = json.loads(result.edge_index_path.read_text(encoding="utf-8"))["edges"]
    assert any(node["kind"] == "concept" for node in nodes)
    assert any(node["kind"] == "output" for node in nodes)
    assert any(edge["relation"] == "DERIVED_FROM" for edge in edges)
    assert any(edge["relation"] == "RELATED_BY_KEYWORDS" for edge in edges)


def test_growth_scheduler_can_sync_graph(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.growth_scheduler import run_growth_pipeline
    from l3_node.cognitive_kernel.runtime import (
        build_decision_contract,
        build_work_order,
        close_turn,
        verify_work_order,
    )

    contract = build_decision_contract(
        turn_id="ck-memory-growth-graph-pipeline-1",
        goal="create graph ready output",
        tool="core:reply",
        work_order_input='{"message":"graph output"}',
    )
    work = build_work_order(
        contract=contract,
        tool="core:reply",
        work_order_input='{"message":"graph output"}',
    )
    report = verify_work_order(
        turn_id=contract.turn_id,
        work_order=work,
        observation='{"ok":true}',
        elapsed_ms=1.0,
    )
    close_turn(
        turn_id=contract.turn_id,
        final_text="Memory Growth Graph Sync 已生成本地实体关系事件。",
        executed_work_orders=[work.work_order_id],
        verification_reports=[report],
    )

    result = run_growth_pipeline(sync_graph=True)
    assert result.graph_sync_result is not None
    assert result.graph_sync_result.node_count > 0
    assert result.graph_sync_result.edge_count > 0

    payload = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert payload["stages"]["graph_sync"] is not None
    assert payload["summary"]["graph_nodes"] > 0


def test_graph_connectors_sync_local_and_report_unconfigured_external_connectors(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))
    monkeypatch.delenv("JACHIN_COGNEE_ENDPOINT", raising=False)
    monkeypatch.delenv("JACHIN_GRAPHITI_ENDPOINT", raising=False)

    from l3_node.cognitive_kernel.graph_connectors import sync_graph_engine_connectors
    from l3_node.cognitive_kernel.graph_sync_adapter import sync_memory_growth_graph
    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold, memory_growth_dir

    ensure_memory_growth_scaffold()
    root = memory_growth_dir()
    concept_dir = root / "concepts" / "project_fact"
    concept_dir.mkdir(parents=True, exist_ok=True)
    (concept_dir / "connector-source.md").write_text(
        "---\n"
        'summary: "Connector source concept for graph sync."\n'
        'source_refs: [{"type":"raw_event","event_id":"raw-connector-1"}]\n'
        "confidence: 0.9\n"
        "---\n\n"
        "# Connector source concept for graph sync.\n",
        encoding="utf-8",
    )
    sync_memory_growth_graph()

    results = sync_graph_engine_connectors(["local_json_graph", "cognee", "graphiti"])
    by_id = {item.connector_id: item for item in results}
    assert by_id["local_json_graph"].ok is True
    assert by_id["local_json_graph"].status == "synced"
    assert by_id["cognee"].ok is False
    assert by_id["cognee"].status == "not_configured"
    assert by_id["graphiti"].ok is False
    assert by_id["graphiti"].status == "not_configured"
    assert (root / "graph" / "connectors" / "local_json_graph" / "latest_snapshot.json").exists()
    assert (root / "indexes" / "graph_connectors.json").exists()


def test_growth_scheduler_can_run_graph_connectors(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.growth_scheduler import run_growth_pipeline
    from l3_node.cognitive_kernel.runtime import (
        build_decision_contract,
        build_work_order,
        close_turn,
        verify_work_order,
    )

    contract = build_decision_contract(
        turn_id="ck-memory-growth-connector-pipeline-1",
        goal="create connector ready output",
        tool="core:reply",
        work_order_input='{"message":"connector output"}',
    )
    work = build_work_order(
        contract=contract,
        tool="core:reply",
        work_order_input='{"message":"connector output"}',
    )
    report = verify_work_order(
        turn_id=contract.turn_id,
        work_order=work,
        observation='{"ok":true}',
        elapsed_ms=1.0,
    )
    close_turn(
        turn_id=contract.turn_id,
        final_text="Memory Growth Connector 已准备好同步本地图谱。",
        executed_work_orders=[work.work_order_id],
        verification_reports=[report],
    )

    result = run_growth_pipeline(sync_graph=True, graph_connector_ids=["local_json_graph"])
    assert result.graph_connector_results
    assert result.graph_connector_results[0].connector_id == "local_json_graph"
    assert result.graph_connector_results[0].ok is True

    payload = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert payload["stages"]["graph_connectors"][0]["connector_id"] == "local_json_graph"
    assert payload["summary"]["graph_connectors_ok"] == 1


def test_memory_growth_http_registers_routes():
    from l3_node.memory_growth_http import register_memory_growth_routes

    class Router:
        def __init__(self):
            self.routes = []

        def add_get(self, path, handler):
            self.routes.append(("GET", path, handler.__name__))

        def add_post(self, path, handler):
            self.routes.append(("POST", path, handler.__name__))

    class App:
        def __init__(self):
            self.router = Router()

    app = App()
    register_memory_growth_routes(app)
    routes = {(method, path) for method, path, _ in app.router.routes}

    assert ("GET", "/api/v1/memory-growth/status") in routes
    assert ("POST", "/api/v1/memory-growth/pipeline") in routes
    assert ("POST", "/api/v1/memory-growth/weekly-review") in routes
    assert ("POST", "/api/v1/memory-growth/graph-sync") in routes
    assert ("POST", "/api/v1/memory-growth/connector-sync") in routes
    assert ("POST", "/api/v1/memory-growth/governance") in routes
    assert ("POST", "/api/v1/memory-growth/batch-governance") in routes


def test_memory_growth_http_pipeline_endpoint(tmp_path, monkeypatch):
    import asyncio

    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.runtime import (
        build_decision_contract,
        build_work_order,
        close_turn,
        verify_work_order,
    )
    from l3_node.memory_growth_http import handle_memory_growth_pipeline

    contract = build_decision_contract(
        turn_id="ck-memory-growth-http-pipeline-1",
        goal="create http pipeline evidence",
        tool="core:reply",
        work_order_input='{"message":"http pipeline"}',
    )
    work = build_work_order(
        contract=contract,
        tool="core:reply",
        work_order_input='{"message":"http pipeline"}',
    )
    report = verify_work_order(
        turn_id=contract.turn_id,
        work_order=work,
        observation='{"ok":true}',
        elapsed_ms=1.0,
    )
    close_turn(
        turn_id=contract.turn_id,
        final_text="Memory Growth HTTP 入口可以触发自生长管线。",
        executed_work_orders=[work.work_order_id],
        verification_reports=[report],
    )

    class Request:
        body_exists = True

        async def json(self):
            return {"sync_graph": True, "graph_connector_ids": ["local_json_graph"]}

    async def run_endpoint():
        response = await handle_memory_growth_pipeline(Request())
        assert response.status == 200
        return json.loads(response.text)

    payload = asyncio.run(run_endpoint())
    assert payload["ok"] is True
    assert payload["result"]["graph_sync_result"]["node_count"] > 0
    assert payload["result"]["graph_connector_results"][0]["connector_id"] == "local_json_graph"
    assert payload["status"]["counts"]["raw_events"] >= 1
    assert payload["status"]["counts"]["graph_nodes"] > 0


def test_memory_growth_http_status_includes_quality_monitoring(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.contracts import MemoryWriteRequest
    from l3_node.cognitive_kernel.memory_lifecycle import record_lifecycle_memory_feedback, write_lifecycle_memory
    from l3_node.cognitive_kernel.memory_growth import append_raw_event, ensure_memory_growth_scaffold, memory_growth_dir
    from l3_node.memory_growth_http import apply_memory_growth_governance, memory_growth_status

    ensure_memory_growth_scaffold()
    root = memory_growth_dir()
    concept_dir = root / "concepts" / "project_fact"
    concept_dir.mkdir(parents=True, exist_ok=True)
    (concept_dir / "old-fact.md").write_text(
        "---\n"
        'summary: "Old project fact that needs review."\n'
        "last_verified: 2020-01-01\n"
        "confidence: 0.8\n"
        "---\n\n"
        "# Old project fact that needs review.\n",
        encoding="utf-8",
    )
    conflict_dir = root / "conflicts"
    conflict_dir.mkdir(parents=True, exist_ok=True)
    (conflict_dir / "needs-confirmation.json").write_text(
        json.dumps(
            {
                "reason": "requires_user_confirmation",
                "date": "2026-07-13",
                "candidate": {
                    "candidate_id": "concept:confirm:1",
                    "summary": "User prefers memory growth changes to be source backed.",
                    "requires_user_confirmation": True,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (conflict_dir / "tool-failed.json").write_text(
        json.dumps(
            {
                "reason": "tool_result_conflict",
                "date": "2026-07-13",
                "candidate": {"candidate_id": "concept:failed:1", "summary": "Tool result conflicted."},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    append_raw_event(
        category="evidence",
        source="unit_test",
        stream="turn_closure",
        payload={
            "turn_id": "failed-turn-1",
            "closure": {
                "verification_status": "failed",
                "failure_reason": "ocr_mismatch",
            },
        },
    )
    correction_content = json.dumps(
        {"type": "app_entity_correction", "surface_norm": "lock", "target_app": "Lark"},
        ensure_ascii=False,
        sort_keys=True,
    )
    write_lifecycle_memory(
        MemoryWriteRequest(
            turn_id="ck-memory-growth-correction-1",
            source_event="entity_correction_confirmed",
            memory_type="correction",
            content=correction_content,
            confidence=0.86,
            ttl="permanent",
            evidence=[{"ok": True}],
        )
    )
    record_lifecycle_memory_feedback(
        memory_type="correction",
        content=correction_content,
        ok=False,
        turn_id="ck-memory-growth-correction-2",
        failure_reason="app_focus_failed",
    )
    record_lifecycle_memory_feedback(
        memory_type="correction",
        content=correction_content,
        ok=False,
        turn_id="ck-memory-growth-correction-3",
        failure_reason="app_focus_failed",
    )

    status = memory_growth_status()
    monitoring = status["monitoring"]

    assert len(monitoring["trends"]["days_7"]) == 7
    assert len(monitoring["trends"]["days_14"]) == 14
    assert len(monitoring["trends"]["days_30"]) == 30
    assert any(row["raw_events"] >= 1 for row in monitoring["trends"]["days_7"])
    assert any(row["reason"] == "requires_user_confirmation" for row in monitoring["conflict_types"])
    assert any(row["reason"] == "tool_result_conflict" for row in monitoring["conflict_types"])
    assert any(row["reason"] == "last_verified_stale" for row in monitoring["stale_concepts"])
    assert any(row["reason"] == "requires_user_confirmation" for row in monitoring["pending_confirmation_queue"])
    assert any(row["kind"] == "memory_lifecycle_review" and row["reason"] == "app_focus_failed" for row in monitoring["pending_confirmation_queue"])
    assert any(str(row["pattern"]).startswith("conflict:tool_result_conflict") for row in monitoring["failure_patterns"])
    assert any(str(row["pattern"]).startswith("failed_turn:ocr_mismatch") for row in monitoring["failure_patterns"])
    assert monitoring["health"]["pending_confirmation_count"] >= 1


def test_memory_growth_governance_confirms_pending_and_writes_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.memory_growth import append_raw_event, ensure_memory_growth_scaffold, memory_growth_dir
    from l3_node.memory_growth_http import apply_memory_growth_governance, memory_growth_status

    ensure_memory_growth_scaffold()
    root = memory_growth_dir()
    conflict_dir = root / "conflicts"
    conflict_dir.mkdir(parents=True, exist_ok=True)
    conflict_path = conflict_dir / "needs-confirmation.json"
    conflict_path.write_text(
        json.dumps(
            {
                "reason": "requires_user_confirmation",
                "date": "2026-07-13",
                "candidate": {
                    "candidate_id": "concept:confirm:governance",
                    "summary": "Confirmed governance knowledge should become a concept.",
                    "confidence": 0.91,
                    "requires_user_confirmation": True,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = apply_memory_growth_governance(
        action="confirm_pending",
        item={"path": "conflicts/needs-confirmation.json", "summary": "Confirmed governance knowledge"},
        note="unit test confirm",
    )

    assert result["action"] == "confirm_pending"
    assert result["report_path"]
    assert result["raw_event_path"]
    side_effect_types = {item["type"] for item in result["side_effects"]}
    assert "conflict_governance_status" in side_effect_types
    assert "confirmed_concept_written" in side_effect_types

    updated_conflict = json.loads(conflict_path.read_text(encoding="utf-8"))
    assert updated_conflict["governance"]["status"] == "confirmed"
    assert (root / "reviews" / "governance").exists()
    assert list((root / "concepts" / "confirmed").glob("*.md"))
    assert list((root / "raw" / "evidence").glob("*.governance.jsonl"))

    status = memory_growth_status()
    assert not status["monitoring"]["pending_confirmation_queue"]


def test_memory_growth_governance_generates_failure_playbook(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold, memory_growth_dir
    from l3_node.memory_growth_http import apply_memory_growth_governance

    ensure_memory_growth_scaffold()
    root = memory_growth_dir()
    result = apply_memory_growth_governance(
        action="generate_failure_playbook",
        item={"pattern": "failed_turn:ocr_mismatch", "count": 3, "examples": ["raw/evidence/example.jsonl"]},
        note="unit test playbook",
    )

    assert result["action"] == "generate_failure_playbook"
    assert any(item["type"] == "failure_playbook_written" for item in result["side_effects"])
    playbooks = list((root / "playbooks" / "recovery").glob("*.md"))
    assert playbooks
    text = playbooks[0].read_text(encoding="utf-8")
    assert "ocr_mismatch" in text
    assert "Recommended Recovery" in text


def test_memory_growth_status_includes_governance_history_and_recommendations(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.memory_growth import append_raw_event, ensure_memory_growth_scaffold, memory_growth_dir
    from l3_node.memory_growth_http import apply_memory_growth_governance, memory_growth_status

    ensure_memory_growth_scaffold()
    root = memory_growth_dir()
    conflict_dir = root / "conflicts"
    conflict_dir.mkdir(parents=True, exist_ok=True)
    (conflict_dir / "needs-confirmation.json").write_text(
        json.dumps(
            {
                "reason": "requires_user_confirmation",
                "date": "2026-07-13",
                "candidate": {
                    "candidate_id": "concept:governance:queue",
                    "summary": "A governance queue item needs user confirmation.",
                    "requires_user_confirmation": True,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (conflict_dir / "tool-conflict.json").write_text(
        json.dumps({"reason": "tool_result_conflict", "date": "2026-07-13"}, ensure_ascii=False),
        encoding="utf-8",
    )
    stale_dir = root / "concepts" / "project"
    stale_dir.mkdir(parents=True, exist_ok=True)
    (stale_dir / "old.md").write_text(
        "---\nsummary: \"Old project concept\"\nlast_verified: \"2026-01-01\"\n---\n\n# Old project concept\n",
        encoding="utf-8",
    )

    apply_memory_growth_governance(
        action="generate_failure_playbook",
        item={"pattern": "failed_turn:ocr_mismatch", "count": 3, "examples": ["raw/evidence/example.jsonl"]},
        note="history seed",
    )
    append_raw_event(
        category="evidence",
        source="unit_test",
        stream="turn_closure",
        payload={
            "turn_id": "failed-after-governance",
            "closure": {
                "verification_status": "failed",
                "failure_reason": "ocr_mismatch",
            },
        },
    )
    status = memory_growth_status()
    monitoring = status["monitoring"]

    assert monitoring["governance_history"]
    assert monitoring["governance_history"][0]["action"] == "generate_failure_playbook"
    assert monitoring["health"]["governance_history_count"] >= 1

    effectiveness = monitoring["governance_effectiveness"]
    assert effectiveness["action_count"] >= 1
    assert effectiveness["generated_playbook_count"] >= 1
    assert effectiveness["post_governance_failure_count"] >= 1
    assert monitoring["health"]["governance_effectiveness_score"] == effectiveness["score"]

    recommendations = monitoring["governance_recommendations"]
    actions = {row["action"] for row in recommendations}
    assert "confirm_pending" in actions
    assert "revalidate_stale" in actions
    assert "generate_failure_playbook" in actions
    assert monitoring["health"]["recommendation_count"] >= 3


def test_memory_growth_batch_governance_executes_multiple_operations(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold, memory_growth_dir
    from l3_node.memory_growth_http import apply_memory_growth_batch_governance, memory_growth_status

    ensure_memory_growth_scaffold()
    root = memory_growth_dir()
    conflict_dir = root / "conflicts"
    conflict_dir.mkdir(parents=True, exist_ok=True)
    (conflict_dir / "needs-confirmation.json").write_text(
        json.dumps(
            {
                "reason": "requires_user_confirmation",
                "date": "2026-07-13",
                "candidate": {
                    "candidate_id": "concept:batch:confirm",
                    "summary": "Batch confirmed knowledge should become a concept.",
                    "requires_user_confirmation": True,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    stale_dir = root / "concepts" / "project"
    stale_dir.mkdir(parents=True, exist_ok=True)
    stale_path = stale_dir / "old.md"
    stale_path.write_text(
        "---\nsummary: \"Old batch concept\"\nlast_verified: \"2026-01-01\"\n---\n\n# Old batch concept\n",
        encoding="utf-8",
    )

    result = apply_memory_growth_batch_governance(
        operations=[
            {"action": "confirm_pending", "item": {"path": "conflicts/needs-confirmation.json"}, "note": "batch confirm"},
            {"action": "revalidate_stale", "item": {"path": "concepts/project/old.md"}, "note": "batch revalidate"},
        ],
        note="unit test batch",
    )

    assert result["executed_count"] == 2
    assert result["failed_count"] == 0
    assert result["report_path"].endswith(".batch.json")
    assert list((root / "reviews" / "governance").glob("*.batch.json"))
    assert list((root / "raw" / "evidence").glob("*.batch_governance.jsonl"))
    updated_conflict = json.loads((conflict_dir / "needs-confirmation.json").read_text(encoding="utf-8"))
    assert updated_conflict["governance"]["status"] == "confirmed"
    assert "revalidated_by_governance" in stale_path.read_text(encoding="utf-8")

    status = memory_growth_status()
    history = status["monitoring"]["governance_history"]
    assert any(row["action"] == "batch_governance" for row in history)


def test_weekly_review_includes_governance_effect_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold
    from l3_node.cognitive_kernel.weekly_review import run_weekly_review
    from l3_node.memory_growth_http import apply_memory_growth_batch_governance

    ensure_memory_growth_scaffold()
    apply_memory_growth_batch_governance(
            operations=[
                {"action": "generate_failure_playbook", "item": {"pattern": "failed_turn:ocr_mismatch", "count": 3}},
                {"action": "confirm_pending", "item": {"summary": "missing path should fail"}},
            ],
        note="weekly governance batch",
    )

    result = run_weekly_review(week_start="2026-07-13", stale_after_days=30)
    payload = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert payload["summary"]["governance_action_count"] >= 2
    assert payload["summary"]["governance_batch_count"] >= 1
    assert payload["summary"]["governance_failed_count"] >= 1
    assert payload["summary"]["governance_effectiveness_score"] >= 0
    assert payload["governance_effectiveness"]["action_count"] >= 2
    assert payload["governance_effectiveness"]["failure_count"] >= 1
    assert payload["governance_actions"]
    markdown = result.markdown_path.read_text(encoding="utf-8")
    assert "Governance actions" in markdown
    assert "Governance effectiveness score" in markdown


def test_governance_effectiveness_index_and_status_trends(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold
    from l3_node.cognitive_kernel.weekly_review import run_weekly_review
    from l3_node.memory_growth_http import apply_memory_growth_batch_governance, memory_growth_status

    root = ensure_memory_growth_scaffold()
    apply_memory_growth_batch_governance(
        operations=[
            {"action": "generate_failure_playbook", "item": {"pattern": "failed_turn:ocr_mismatch", "count": 3}},
            {"action": "confirm_pending", "item": {"summary": "missing path should fail"}},
        ],
        note="trend seed",
    )

    result = run_weekly_review(week_start="2026-07-13", stale_after_days=30)
    index_path = root / "indexes" / "governance_effectiveness.json"
    assert index_path.exists()
    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert index["latest"]["week_id"] == result.week_id
    assert index["history"]
    assert "effective_actions" in index["attribution"]
    assert "ineffective_actions" in index["attribution"]

    status = memory_growth_status()
    monitoring = status["monitoring"]
    trends = monitoring["governance_effectiveness_trends"]
    assert trends["days_30"]
    assert trends["days_30"][-1]["week_id"] == result.week_id
    attribution = monitoring["governance_effectiveness_attribution"]
    assert attribution["latest"]["week_id"] == result.week_id
    assert attribution["ineffective_actions"]
    assert index["strategy_policy"]["action_policy"]
    assert monitoring["governance_strategy_policy"]["action_policy"]


def test_memory_growth_strategy_policy_adjusts_recommendation_priority(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold
    from l3_node.memory_growth_http import memory_growth_status

    root = ensure_memory_growth_scaffold()
    (root / "indexes").mkdir(parents=True, exist_ok=True)
    (root / "indexes" / "governance_effectiveness.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "latest": {"week_id": "2026-W29", "score": 50},
                "history": [
                    {"week_id": "2026-W28", "date": "2026-07-06", "score": 75},
                    {"week_id": "2026-W29", "date": "2026-07-13", "score": 50},
                ],
                "attribution": {
                    "effective_actions": [{"action": "confirm_pending", "success_count": 3}],
                    "ineffective_actions": [{"action": "generate_failure_playbook", "failure_count": 4}],
                    "repeated_failures": [],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    conflict_dir = root / "conflicts"
    conflict_dir.mkdir(parents=True, exist_ok=True)
    (conflict_dir / "needs-confirmation.json").write_text(
        json.dumps(
            {
                "reason": "requires_user_confirmation",
                "date": "2026-07-13",
                "candidate": {
                    "candidate_id": "concept:strategy:confirm",
                    "summary": "Strategy policy should allow this confirmation to be batched.",
                    "requires_user_confirmation": True,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (conflict_dir / "tool-conflict.json").write_text(
        json.dumps({"reason": "tool_result_conflict", "date": "2026-07-13"}, ensure_ascii=False),
        encoding="utf-8",
    )

    status = memory_growth_status()
    monitoring = status["monitoring"]
    policy = monitoring["governance_strategy_policy"]
    assert policy["global_mode"] == "cautious"
    assert policy["action_policy"]["confirm_pending"]["execution_mode"] == "batch_ok"
    assert policy["action_policy"]["generate_failure_playbook"]["execution_mode"] == "manual_review"

    recommendations = monitoring["governance_recommendations"]
    confirm = next(row for row in recommendations if row["action"] == "confirm_pending")
    playbook = next(row for row in recommendations if row["action"] == "generate_failure_playbook")
    assert confirm["strategy"]["execution_mode"] == "batch_ok"
    assert playbook["strategy"]["execution_mode"] == "manual_review"
    assert playbook["strategy"]["requires_more_evidence"] is True
    assert confirm["priority_score"] > playbook["priority_score"]


def test_weekly_review_writes_strategy_policy_to_artifact_frontmatter(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold, memory_growth_dir
    from l3_node.cognitive_kernel.weekly_review import run_weekly_review
    from l3_node.memory_growth_http import apply_memory_growth_governance

    ensure_memory_growth_scaffold()
    root = memory_growth_dir()
    conflict_dir = root / "conflicts"
    conflict_dir.mkdir(parents=True, exist_ok=True)
    (conflict_dir / "needs-confirmation.json").write_text(
        json.dumps(
            {
                "reason": "requires_user_confirmation",
                "date": "2026-07-13",
                "candidate": {
                    "candidate_id": "concept:strategy:persist",
                    "summary": "Strategy persistence should be written into confirmed concepts.",
                    "requires_user_confirmation": True,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    apply_memory_growth_governance(
        action="confirm_pending",
        item={"path": "conflicts/needs-confirmation.json"},
        note="confirm strategy seed",
    )
    apply_memory_growth_governance(
        action="generate_failure_playbook",
        item={"pattern": "failed_turn:window_focus_timeout", "count": 3},
        note="playbook strategy seed",
    )

    run_weekly_review(week_start="2026-07-13", stale_after_days=30)

    concept_path = next((root / "concepts" / "confirmed").glob("*.md"))
    playbook_path = next((root / "playbooks" / "recovery").glob("*.md"))
    concept_text = concept_path.read_text(encoding="utf-8")
    playbook_text = playbook_path.read_text(encoding="utf-8")
    assert 'governance_strategy_action: "confirm_pending"' in concept_text
    assert 'governance_execution_mode: "batch_ok"' in concept_text
    assert 'governance_strategy_action: "generate_failure_playbook"' in playbook_text
    assert 'governance_execution_mode: "batch_ok"' in playbook_text


def test_turn_closure_updates_memory_growth_artifact_usage(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.contracts import VerificationReport
    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold, memory_growth_dir
    from l3_node.cognitive_kernel.runtime import close_turn
    from l3_node.memory_growth_http import memory_growth_status

    ensure_memory_growth_scaffold()
    root = memory_growth_dir()
    playbook_path = root / "playbooks" / "browser-open-focus.md"
    playbook_path.write_text(
        """---
id: "playbook:browser-open-focus"
type: "playbook"
summary: "Open browser and verify focus"
confidence: 0.82
last_verified: "2026-07-10T00:00:00Z"
---

# Open browser and verify focus
""",
        encoding="utf-8",
    )
    refs = [
        {
            "bucket": "failure_hints",
            "memory_id": "memory_growth:playbook:browser-open-focus",
            "source": "Memory Growth Playbooks",
            "artifact_path": str(playbook_path),
            "preview": "playbook path=" + str(playbook_path) + "; summary=Open browser and verify focus",
        }
    ]

    close_turn(
        turn_id="turn-artifact-success",
        final_text="ok",
        executed_work_orders=["work-1"],
        verification_reports=[VerificationReport(verification_id="verify-1", work_order_id="work-1", ok=True)],
        memory_context_refs=refs,
    )
    close_turn(
        turn_id="turn-artifact-failure",
        final_text="failed",
        executed_work_orders=["work-2"],
        verification_reports=[
            VerificationReport(
                verification_id="verify-2",
                work_order_id="work-2",
                ok=False,
                failure_reason="window_focus_timeout",
            )
        ],
        aborted=True,
        memory_context_refs=refs,
    )

    text = playbook_path.read_text(encoding="utf-8")
    assert "memory_use_count: 2" in text
    assert "memory_success_count: 1" in text
    assert "memory_failure_count: 1" in text
    assert "memory_success_rate: 0.5" in text
    assert 'memory_last_failure_reason: "window_focus_timeout"' in text

    usage_index = json.loads((root / "indexes" / "artifact_usage.json").read_text(encoding="utf-8"))
    row = next(item for item in usage_index["artifacts"] if item["path"] == "playbooks/browser-open-focus.md")
    assert row["memory_use_count"] == 2
    assert row["memory_success_count"] == 1
    assert row["memory_failure_count"] == 1
    assert row["memory_last_failure_reason"] == "window_focus_timeout"
    status_row = next(item for item in memory_growth_status()["monitoring"]["artifact_usage"] if item["path"] == "playbooks/browser-open-focus.md")
    assert status_row["memory_use_count"] == 2


def test_memory_growth_status_reports_success_path_health(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold, memory_growth_dir
    from l3_node.memory_growth_http import memory_growth_status

    ensure_memory_growth_scaffold()
    root = memory_growth_dir()
    index_dir = root / "indexes"
    index_dir.mkdir(parents=True, exist_ok=True)
    (index_dir / "artifact_usage.json").write_text(
        json.dumps(
            {
                "artifacts": [
                    {
                        "path": "playbooks/learned_success/success-lark-neil.md",
                        "id": "playbook:success-lark-neil",
                        "type": "success_playbook",
                        "summary": "Reliable Lark delivery to Neil",
                        "memory_use_count": 5,
                        "memory_success_count": 5,
                        "memory_failure_count": 0,
                        "memory_success_rate": 1.0,
                    },
                    {
                        "path": "playbooks/learned_success/success-window-focus.md",
                        "id": "playbook:success-window-focus",
                        "type": "success_playbook",
                        "summary": "Degraded window focus path",
                        "memory_use_count": 6,
                        "memory_success_count": 1,
                        "memory_failure_count": 5,
                        "memory_success_rate": 0.166,
                        "memory_last_failure_reason": "window_focus_timeout",
                    },
                    {
                        "path": "playbooks/recovery/generic-timeout.md",
                        "id": "playbook:generic-timeout",
                        "type": "playbook",
                        "summary": "Generic timeout playbook",
                        "memory_use_count": 8,
                        "memory_success_count": 8,
                        "memory_failure_count": 0,
                        "memory_success_rate": 1.0,
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    monitoring = memory_growth_status()["monitoring"]
    health = monitoring["success_path_health"]
    assert health["summary"]["total_paths"] == 2
    assert health["summary"]["reliable_count"] == 1
    assert health["summary"]["degraded_count"] == 1
    assert monitoring["health"]["success_path_reliable_count"] == 1
    assert monitoring["health"]["success_path_degraded_count"] == 1
    assert health["reliable_paths"][0]["path"] == "playbooks/learned_success/success-lark-neil.md"
    assert health["degraded_paths"][0]["memory_last_failure_reason"] == "window_focus_timeout"


def test_memory_growth_status_reports_memory_trust_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.contracts import MemoryWriteRequest
    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold
    from l3_node.cognitive_kernel.memory_lifecycle import write_lifecycle_memory
    from l3_node.memory_growth_http import memory_growth_status

    ensure_memory_growth_scaffold()
    write_lifecycle_memory(
        MemoryWriteRequest(
            turn_id="trust-1",
            source_event="user_confirmed_alias",
            memory_type="alias",
            content="Neil uses Lark for messages.",
            trust_state="confirmed",
            confidence=0.9,
        )
    )
    write_lifecycle_memory(
        MemoryWriteRequest(
            turn_id="trust-2",
            source_event="system_inferred_alias",
            memory_type="alias",
            content="Maybe Lock means Lark.",
            confidence=0.5,
        )
    )
    write_lifecycle_memory(
        MemoryWriteRequest(
            turn_id="trust-3",
            source_event="user_rejected_alias",
            memory_type="alias",
            content="Lock means Lark.",
            confidence=0.8,
        )
    )
    write_lifecycle_memory(
        MemoryWriteRequest(
            turn_id="trust-4",
            source_event="conflict_requires_user_confirmation",
            memory_type="correction",
            content="Conflicting alias needs confirmation.",
            requires_user_confirmation=True,
            trust_state="conflicted",
            confidence=0.6,
        )
    )

    trust = memory_growth_status()["monitoring"]["memory_trust"]

    assert trust["summary"]["confirmed_count"] == 1
    assert trust["summary"]["floating_count"] == 1
    assert trust["summary"]["rejected_count"] == 1
    assert trust["summary"]["conflicted_count"] == 1
    assert trust["summary"]["recall_blocked_count"] == 1
    assert trust["requires_confirmation"][0]["trust_state"] == "conflicted"
    assert trust["review_queue"][0]["trust_state"] == "conflicted"
    assert trust["review_queue"][0]["review_priority"] == 100
    assert any(row["trust_state"] == "floating" for row in trust["review_queue"])
    assert any(row["trust_state"] == "rejected" for row in trust["review_queue"])
    assert trust["recent_floating"][0]["content"] == "Maybe Lock means Lark."
    assert trust["recent_rejected"][0]["recall_allowed"] is False


def test_memory_growth_status_reports_memory_trust_analytics(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.contracts import MemoryWriteRequest
    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold
    from l3_node.cognitive_kernel.memory_lifecycle import govern_lifecycle_memory, write_lifecycle_memory
    from l3_node.cognitive_kernel.weekly_review import run_weekly_review
    from l3_node.memory_growth_http import apply_memory_growth_governance, memory_growth_status

    ensure_memory_growth_scaffold()

    rejected_a = write_lifecycle_memory(
        MemoryWriteRequest(
            turn_id="trust-analytics-reject-1",
            source_event="system_inferred_alias",
            memory_type="alias",
            content="Lock means Lark in voice input.",
            confidence=0.62,
        )
    )
    rejected_b = write_lifecycle_memory(
        MemoryWriteRequest(
            turn_id="trust-analytics-reject-2",
            source_event="system_inferred_alias",
            memory_type="alias",
            content="Lock means Lark from voice command.",
            confidence=0.64,
        )
    )
    govern_lifecycle_memory(memory_id=rejected_a.memory_id, action="reject", note="wrong alias")
    govern_lifecycle_memory(memory_id=rejected_b.memory_id, action="reject", note="wrong alias")

    confirmed_a = write_lifecycle_memory(
        MemoryWriteRequest(
            turn_id="trust-follow-up-confirm-1",
            source_event="user_confirmed_preference",
            memory_type="user_preference",
            content="Neil Lark updates are preferred.",
            trust_state="confirmed",
            confidence=0.87,
        )
    )
    confirmed_b = write_lifecycle_memory(
        MemoryWriteRequest(
            turn_id="trust-follow-up-confirm-2",
            source_event="user_confirmed_preference",
            memory_type="user_preference",
            content="Neil Lark updates are confirmed.",
            trust_state="confirmed",
            confidence=0.89,
        )
    )
    govern_lifecycle_memory(memory_id=confirmed_a.memory_id, action="confirm", note="stable preference")
    govern_lifecycle_memory(memory_id=confirmed_b.memory_id, action="confirm", note="stable preference")

    confirmed_a = write_lifecycle_memory(
        MemoryWriteRequest(
            turn_id="trust-analytics-confirm-1",
            source_event="user_confirmed_preference",
            memory_type="user_preference",
            content="Neil Lark updates are preferred.",
            trust_state="confirmed",
            confidence=0.86,
        )
    )
    confirmed_b = write_lifecycle_memory(
        MemoryWriteRequest(
            turn_id="trust-analytics-confirm-2",
            source_event="user_confirmed_preference",
            memory_type="user_preference",
            content="Neil Lark updates are confirmed.",
            trust_state="confirmed",
            confidence=0.88,
        )
    )
    govern_lifecycle_memory(memory_id=confirmed_a.memory_id, action="confirm", note="stable preference")
    govern_lifecycle_memory(memory_id=confirmed_b.memory_id, action="confirm", note="stable preference")

    analytics = memory_growth_status()["monitoring"]["memory_trust"]["analytics"]

    assert analytics["summary"]["rejected_pattern_count"] >= 1
    assert analytics["summary"]["promotion_candidate_count"] >= 1
    assert analytics["rejected_patterns"][0]["rejected_count"] >= 2
    assert analytics["promotion_candidates"][0]["confirmed_count"] >= 2

    recommendations = memory_growth_status()["monitoring"]["governance_recommendations"]
    actions = {row["action"] for row in recommendations}
    assert "review_rejected_memory_pattern" in actions
    assert "promote_memory_pattern" in actions

    rejected_rec = next(row for row in recommendations if row["action"] == "review_rejected_memory_pattern")
    rejected_result = apply_memory_growth_governance(
        action=rejected_rec["action"],
        item=rejected_rec["item"],
        note="unit reviews rejected pattern",
    )
    assert rejected_result["side_effects"][0]["type"] == "memory_trust_rejected_pattern_review_written"

    promote_rec = next(row for row in recommendations if row["action"] == "promote_memory_pattern")
    promote_result = apply_memory_growth_governance(
        action=promote_rec["action"],
        item=promote_rec["item"],
        note="unit promotes stable pattern",
    )
    assert promote_result["side_effects"][0]["type"] == "memory_trust_method_memory_proposal_written"

    stale_result = apply_memory_growth_governance(
        action="revalidate_confirmed_memory",
        item={"memory_id": confirmed_a.memory_id, "sample": confirmed_a.content, "age_days": 45},
        note="unit revalidates stale confirmed memory",
    )
    assert stale_result["side_effects"][0]["type"] == "memory_trust_revalidation_request_written"

    review = memory_growth_status()["monitoring"]["trust_governance_review"]
    assert review["summary"]["executed_count"] >= 3
    assert review["summary"]["converted_count"] >= 3
    assert review["summary"]["conversion_rate"] == 1.0
    assert review["summary"]["follow_up_count"] == 0
    assert review["summary"]["next_action_count"] == 0
    conversion_types = {row["conversion_type"] for row in review["converted"]}
    assert "memory_trust_rejected_pattern_review_written" in conversion_types
    assert "memory_trust_method_memory_proposal_written" in conversion_types
    assert "memory_trust_revalidation_request_written" in conversion_types

    monitoring = memory_growth_status()["monitoring"]
    effectiveness = monitoring["governance_effectiveness"]
    assert effectiveness["trust_conversion_rate"] == 1.0
    assert effectiveness["trust_converted_count"] >= 3
    assert "trust_governance_converted" in effectiveness["signals"]
    assert monitoring["health"]["trust_governance_conversion_rate"] == 1.0

    weekly = run_weekly_review(week_start="2026-07-13", stale_after_days=30)
    weekly_payload = json.loads(weekly.report_path.read_text(encoding="utf-8"))
    assert weekly_payload["summary"]["trust_governance_conversion_rate"] == 1.0
    assert weekly_payload["summary"]["trust_governance_next_action_count"] == 0
    assert weekly_payload["governance_effectiveness"]["trust_conversion_rate"] == 1.0
    index = json.loads((weekly.report_path.parents[2] / "indexes" / "governance_effectiveness.json").read_text(encoding="utf-8"))
    assert index["latest"]["trust_conversion_rate"] == 1.0
    assert index["history"][0]["trust_conversion_rate"] == 1.0


def test_memory_growth_trust_governance_follow_up_actions(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.contracts import MemoryWriteRequest
    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold
    from l3_node.cognitive_kernel.memory_lifecycle import govern_lifecycle_memory, write_lifecycle_memory
    from l3_node.cognitive_kernel.weekly_review import run_weekly_review
    from l3_node.memory_growth_http import apply_memory_growth_auto_governance, memory_growth_status

    root = ensure_memory_growth_scaffold()
    rejected_a = write_lifecycle_memory(
        MemoryWriteRequest(
            turn_id="trust-follow-up-reject-1",
            source_event="system_inferred_alias",
            memory_type="alias",
            content="Lock means Lark in speech.",
            confidence=0.61,
        )
    )
    rejected_b = write_lifecycle_memory(
        MemoryWriteRequest(
            turn_id="trust-follow-up-reject-2",
            source_event="system_inferred_alias",
            memory_type="alias",
            content="Lock means Lark command.",
            confidence=0.63,
        )
    )
    govern_lifecycle_memory(memory_id=rejected_a.memory_id, action="reject", note="wrong alias")
    govern_lifecycle_memory(memory_id=rejected_b.memory_id, action="reject", note="wrong alias")

    confirmed_a = write_lifecycle_memory(
        MemoryWriteRequest(
            turn_id="trust-follow-up-confirm-a",
            source_event="user_confirmed_preference",
            memory_type="user_preference",
            content="Neil Lark updates are preferred.",
            trust_state="confirmed",
            confidence=0.87,
        )
    )
    confirmed_b = write_lifecycle_memory(
        MemoryWriteRequest(
            turn_id="trust-follow-up-confirm-b",
            source_event="user_confirmed_preference",
            memory_type="user_preference",
            content="Neil Lark updates are confirmed.",
            trust_state="confirmed",
            confidence=0.89,
        )
    )
    govern_lifecycle_memory(memory_id=confirmed_a.memory_id, action="confirm", note="stable preference")
    govern_lifecycle_memory(memory_id=confirmed_b.memory_id, action="confirm", note="stable preference")

    failed_dir = root / "reviews" / "governance"
    failed_dir.mkdir(parents=True, exist_ok=True)
    failed_report = {
        "governance_id": "failed-trust-conversion",
        "action": "review_rejected_memory_pattern",
        "created_at": "2026-07-17T00:00:00Z",
        "item": {"pattern_key": "lock lark", "sample": "Lock means Lark", "rejected_count": 2},
        "side_effects": [],
        "error": "write_failed",
    }
    (failed_dir / "failed-trust-conversion.json").write_text(json.dumps(failed_report, ensure_ascii=False), encoding="utf-8")

    monitoring = memory_growth_status()["monitoring"]
    review = monitoring["trust_governance_review"]
    assert review["summary"]["pending_count"] >= 1
    assert review["summary"]["failed_count"] >= 1
    assert review["summary"]["follow_up_count"] >= 2
    assert review["summary"]["next_action_count"] >= 2
    assert review["follow_up_queue"][0]["priority_score"] >= review["follow_up_queue"][-1]["priority_score"]
    assert any(row["kind"] == "failed_trust_conversion" for row in review["follow_up_queue"])
    assert any(row["source"] == "trust_governance_pending" for row in review["next_actions"])
    assert any(row["source"] == "trust_governance_failed" for row in review["next_actions"])

    effectiveness = monitoring["governance_effectiveness"]
    assert "trust_governance_pending" in effectiveness["signals"]
    assert "trust_governance_failed" in effectiveness["signals"]
    assert any("Trust-governance recommendations are pending" in item for item in effectiveness["recommendations"])

    weekly = run_weekly_review(week_start="2026-07-13", stale_after_days=30)
    payload = json.loads(weekly.report_path.read_text(encoding="utf-8"))
    assert payload["summary"]["trust_governance_next_action_count"] >= 2
    assert payload["memory_governance_next_actions"]
    assert any("memory governance next-action" in item for item in payload["recommendations"])

    first_auto = apply_memory_growth_auto_governance(source="unit", max_items=5)
    assert first_auto["executed_count"] >= 1
    assert first_auto["report_path"].endswith(".auto.json")
    assert first_auto["raw_event_path"]
    assert any(
        row.get("kind") == "failed_trust_conversion" and row.get("ok")
        for row in first_auto["results"]
        if isinstance(row, dict)
    )

    second_auto = apply_memory_growth_auto_governance(source="unit", max_items=5)
    assert any(
        row.get("reason") == "auto_retry_limit_reached"
        for row in second_auto["skipped"]
        if isinstance(row, dict)
    )


def test_daily_review_runs_memory_governance_auto_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.contracts import MemoryWriteRequest
    from l3_node.cognitive_kernel.daily_review import run_daily_review
    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold
    from l3_node.cognitive_kernel.memory_lifecycle import govern_lifecycle_memory, write_lifecycle_memory

    ensure_memory_growth_scaffold()
    first = write_lifecycle_memory(
        MemoryWriteRequest(
            turn_id="daily-auto-confirm-1",
            source_event="user_confirmed_preference",
            memory_type="user_preference",
            content="Neil Lark summaries should include concise source links.",
            trust_state="confirmed",
            confidence=0.88,
        )
    )
    second = write_lifecycle_memory(
        MemoryWriteRequest(
            turn_id="daily-auto-confirm-2",
            source_event="user_confirmed_preference",
            memory_type="user_preference",
            content="Neil Lark summaries should include compact source links.",
            trust_state="confirmed",
            confidence=0.9,
        )
    )
    govern_lifecycle_memory(memory_id=first.memory_id, action="confirm", note="stable delivery preference")
    govern_lifecycle_memory(memory_id=second.memory_id, action="confirm", note="stable delivery preference")

    result = run_daily_review(date="2026-07-17")
    patch = json.loads(result.patch_path.read_text(encoding="utf-8"))
    auto = patch["memory_governance_auto_policy"]
    assert auto["source"] == "daily_review"
    assert auto["executed_count"] >= 1
    assert auto["failed_count"] == 0
    assert any(row["action"] == "promote_memory_pattern" for row in auto["results"])
    assert patch["memory_governance_auto_recommendation"]["recommended_mode"] == "safe_auto"
    history = patch["memory_governance_auto_history"]
    assert history["entry_id"]
    assert history["summary"]["last_30_records"] >= 1
    assert Path(history["index_path"]).exists()
    review_text = result.review_path.read_text(encoding="utf-8")
    assert "Memory Governance Auto Policy" in review_text
    assert "Memory Governance Mode Recommendation" in review_text
    assert "Memory Governance Auto History" in review_text


def test_memory_governance_auto_policy_can_disable_daily_auto_run(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.contracts import MemoryWriteRequest
    from l3_node.cognitive_kernel.daily_review import run_daily_review
    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold
    from l3_node.cognitive_kernel.memory_lifecycle import govern_lifecycle_memory, write_lifecycle_memory
    from l3_node.cognitive_kernel.weekly_review import run_weekly_review
    from l3_node.memory_growth_http import memory_growth_status, save_memory_growth_auto_governance_policy

    ensure_memory_growth_scaffold()
    first = write_lifecycle_memory(
        MemoryWriteRequest(
            turn_id="daily-auto-off-confirm-1",
            source_event="user_confirmed_preference",
            memory_type="user_preference",
            content="Neil Lark summaries should be short.",
            trust_state="confirmed",
            confidence=0.88,
        )
    )
    second = write_lifecycle_memory(
        MemoryWriteRequest(
            turn_id="daily-auto-off-confirm-2",
            source_event="user_confirmed_preference",
            memory_type="user_preference",
            content="Neil Lark summaries should be concise.",
            trust_state="confirmed",
            confidence=0.9,
        )
    )
    govern_lifecycle_memory(memory_id=first.memory_id, action="confirm", note="stable delivery preference")
    govern_lifecycle_memory(memory_id=second.memory_id, action="confirm", note="stable delivery preference")

    policy = save_memory_growth_auto_governance_policy(mode="off", max_items=3)
    assert policy["mode"] == "off"
    assert policy["max_items"] == 3

    result = run_daily_review(date="2026-07-17")
    patch = json.loads(result.patch_path.read_text(encoding="utf-8"))
    auto = patch["memory_governance_auto_policy"]
    assert auto["mode"] == "off"
    assert auto["executed_count"] == 0
    assert auto["skipped"][0]["reason"] == "auto_governance_disabled"

    monitoring = memory_growth_status()["monitoring"]
    assert monitoring["memory_governance_auto_policy"]["mode"] == "off"
    assert monitoring["memory_governance_auto_latest"]["mode"] == "off"
    assert monitoring["memory_governance_auto_trends"]["days_7"][-1]["runs"] >= 1
    assert monitoring["memory_governance_auto_trends"]["days_7"][-1]["skipped"] >= 1
    assert monitoring["memory_governance_auto_mode_history"]["summary"]["last_30_records"] >= 1
    assert monitoring["memory_governance_auto_mode_history"]["summary"]["last_30_change_recommended"] >= 1
    recommendation = monitoring["memory_governance_auto_recommendation"]
    assert recommendation["current_mode"] == "off"
    assert recommendation["recommended_mode"] == "manual"
    assert recommendation["should_change"] is True

    weekly = run_weekly_review(week_start="2026-07-13", stale_after_days=30)
    weekly_payload = json.loads(weekly.report_path.read_text(encoding="utf-8"))
    assert weekly_payload["summary"]["memory_governance_auto_current_mode"] == "off"
    assert weekly_payload["summary"]["memory_governance_auto_recommended_mode"] == "manual"
    assert weekly_payload["summary"]["memory_governance_auto_should_change"] is True
    assert weekly_payload["summary"]["memory_governance_auto_history_risk"] in {"watch", "noisy", "stable", "unknown"}
    assert weekly_payload["memory_governance_auto"]["recommendation"]["recommended_mode"] == "manual"
    assert weekly_payload["memory_governance_auto_history"]["summary"]["last_30_records"] >= 2
    assert any("Memory governance auto mode should be reviewed" in item for item in weekly_payload["recommendations"])
    assert "Memory Governance Auto Recommendation" in weekly.markdown_path.read_text(encoding="utf-8")
    assert "Memory Governance Auto History" in weekly.markdown_path.read_text(encoding="utf-8")


def test_memory_governance_auto_recommends_manual_after_repeated_failures(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold
    from l3_node.memory_growth_http import memory_growth_status, save_memory_growth_auto_governance_policy

    root = ensure_memory_growth_scaffold()
    save_memory_growth_auto_governance_policy(mode="safe_auto", max_items=5)
    reports = root / "reviews" / "governance"
    reports.mkdir(parents=True, exist_ok=True)
    for index in range(2):
        payload = {
            "schema_version": 1,
            "auto_governance_id": f"auto-failed-{index}",
            "created_at": "2026-07-17T00:00:00Z",
            "source": "unit",
            "mode": "safe_auto",
            "requested_count": 3,
            "selected_count": 1,
            "executed_count": 0,
            "failed_count": 1,
            "skipped": [{"reason": "auto_retry_limit_reached"}],
            "results": [{"ok": False, "error": "write_failed"}],
        }
        (reports / f"auto-failed-{index}.auto.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    recommendation = memory_growth_status()["monitoring"]["memory_governance_auto_recommendation"]
    assert recommendation["current_mode"] == "safe_auto"
    assert recommendation["recommended_mode"] == "manual"
    assert recommendation["should_change"] is True
    assert "recent_auto_governance_failures_or_retry_limits" in recommendation["reasons"]


def test_memory_governance_auto_recommends_keep_safe_auto_when_clean(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.contracts import MemoryWriteRequest
    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold
    from l3_node.cognitive_kernel.memory_lifecycle import govern_lifecycle_memory, write_lifecycle_memory
    from l3_node.memory_growth_http import apply_memory_growth_auto_governance, memory_growth_status, save_memory_growth_auto_governance_policy

    ensure_memory_growth_scaffold()
    save_memory_growth_auto_governance_policy(mode="safe_auto", max_items=5)
    first = write_lifecycle_memory(
        MemoryWriteRequest(
            turn_id="auto-healthy-confirm-1",
            source_event="user_confirmed_preference",
            memory_type="user_preference",
            content="Neil Lark summaries should include concise source links.",
            trust_state="confirmed",
            confidence=0.89,
        )
    )
    second = write_lifecycle_memory(
        MemoryWriteRequest(
            turn_id="auto-healthy-confirm-2",
            source_event="user_confirmed_preference",
            memory_type="user_preference",
            content="Neil Lark summaries should include compact source links.",
            trust_state="confirmed",
            confidence=0.9,
        )
    )
    govern_lifecycle_memory(memory_id=first.memory_id, action="confirm", note="stable preference")
    govern_lifecycle_memory(memory_id=second.memory_id, action="confirm", note="stable preference")
    assert apply_memory_growth_auto_governance(source="unit")["executed_count"] >= 1

    recommendation = memory_growth_status()["monitoring"]["memory_governance_auto_recommendation"]
    assert recommendation["current_mode"] == "safe_auto"
    assert recommendation["recommended_mode"] == "safe_auto"
    assert recommendation["should_change"] is False
    assert "safe_auto_is_converting_cleanly" in recommendation["reasons"]


def test_memory_growth_governance_updates_memory_trust(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.contracts import MemoryWriteRequest
    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold
    from l3_node.cognitive_kernel.memory_lifecycle import recall_lifecycle_memories, write_lifecycle_memory
    from l3_node.memory_growth_http import apply_memory_growth_governance, memory_growth_status

    ensure_memory_growth_scaffold()
    record = write_lifecycle_memory(
        MemoryWriteRequest(
            turn_id="trust-governance-1",
            source_event="system_inferred_alias",
            memory_type="alias",
            content="Maybe Lock means Lark.",
            confidence=0.5,
        )
    )

    confirmed = apply_memory_growth_governance(
        action="confirm_memory",
        item={"memory_id": record.memory_id},
        note="unit confirms the alias",
    )
    assert confirmed["side_effects"][0]["type"] == "memory_trust_governed"
    assert confirmed["side_effects"][0]["trust_state"] == "confirmed"
    assert memory_growth_status()["monitoring"]["memory_trust"]["summary"]["confirmed_count"] == 1
    assert recall_lifecycle_memories("Lock Lark", limit=3)

    rejected = apply_memory_growth_governance(
        action="reject_memory",
        item={"memory_id": record.memory_id},
        note="unit rejects the alias",
    )
    assert rejected["side_effects"][0]["trust_state"] == "rejected"
    trust = memory_growth_status()["monitoring"]["memory_trust"]
    assert trust["summary"]["rejected_count"] == 1
    assert trust["summary"]["recall_blocked_count"] == 1
    assert recall_lifecycle_memories("Lock Lark", limit=3) == []

    corrected = apply_memory_growth_governance(
        action="correct_memory",
        item={"memory_id": record.memory_id, "corrected_content": "Lock in voice input usually means Lark."},
        note="unit corrects the alias",
    )
    assert corrected["side_effects"][0]["trust_state"] == "confirmed"
    evidence = recall_lifecycle_memories("voice input Lark", limit=3)
    assert evidence
    assert "usually means Lark" in evidence[0].content


def test_success_path_health_changes_after_multi_round_feedback(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.contracts import VerificationReport
    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold, memory_growth_dir
    from l3_node.cognitive_kernel.runtime import close_turn
    from l3_node.memory_growth_http import memory_growth_status

    ensure_memory_growth_scaffold()
    root = memory_growth_dir()
    playbook_path = root / "playbooks" / "learned_success" / "success-window-focus.md"
    playbook_path.parent.mkdir(parents=True, exist_ok=True)
    playbook_path.write_text(
        """---
id: "playbook:success-window-focus"
type: "success_playbook"
summary: "Verified window focus path"
confidence: 0.88
memory_use_count: 0
memory_success_count: 0
memory_failure_count: 0
memory_success_rate: 0.0
---

# Verified window focus path
""",
        encoding="utf-8",
    )
    refs = [
        {
            "bucket": "success_playbooks",
            "memory_id": "memory_growth:playbook:success-window-focus",
            "source": "Memory Growth Playbooks",
            "artifact_path": str(playbook_path),
            "preview": "success_strategy=reuse_window_focus_chain; artifact_success_rate=0.0",
        }
    ]
    for index in range(3):
        close_turn(
            turn_id=f"success-window-focus-{index}",
            final_text="ok",
            executed_work_orders=[f"work-success-{index}"],
            verification_reports=[VerificationReport(verification_id=f"verify-success-{index}", work_order_id=f"work-success-{index}", ok=True)],
            memory_context_refs=refs,
        )

    health = memory_growth_status()["monitoring"]["success_path_health"]
    assert health["summary"]["reliable_count"] == 1
    assert health["summary"]["degraded_count"] == 0
    assert health["reliable_paths"][0]["path"] == "playbooks/learned_success/success-window-focus.md"

    for index in range(4):
        close_turn(
            turn_id=f"failure-window-focus-{index}",
            final_text="failed",
            executed_work_orders=[f"work-failure-{index}"],
            verification_reports=[
                VerificationReport(
                    verification_id=f"verify-failure-{index}",
                    work_order_id=f"work-failure-{index}",
                    ok=False,
                    failure_reason="window_focus_timeout",
                )
            ],
            aborted=True,
            memory_context_refs=refs,
        )

    health = memory_growth_status()["monitoring"]["success_path_health"]
    assert health["summary"]["reliable_count"] == 0
    assert health["summary"]["degraded_count"] == 1
    assert health["degraded_paths"][0]["memory_success_rate"] < 0.5
    assert health["degraded_paths"][0]["memory_last_failure_reason"] == "window_focus_timeout"


def test_weekly_review_indexes_artifact_usage_trends_and_recommendations(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.contracts import VerificationReport
    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold, memory_growth_dir
    from l3_node.cognitive_kernel.runtime import close_turn
    from l3_node.cognitive_kernel.weekly_review import run_weekly_review
    from l3_node.memory_growth_http import memory_growth_status

    ensure_memory_growth_scaffold()
    root = memory_growth_dir()
    good_path = root / "playbooks" / "browser-open-focus.md"
    bad_path = root / "playbooks" / "lark-send-unstable.md"
    for path, summary in (
        (good_path, "Open browser and verify focus"),
        (bad_path, "Send Lark message with unstable selector"),
    ):
        path.write_text(
            f"""---
id: "playbook:{path.stem}"
type: "playbook"
summary: "{summary}"
confidence: 0.82
last_verified: "2026-07-10T00:00:00Z"
---

# {summary}
""",
            encoding="utf-8",
        )

    good_refs = [{"bucket": "failure_hints", "memory_id": "memory_growth:playbook:browser-open-focus", "source": "Memory Growth Playbooks", "artifact_path": str(good_path)}]
    bad_refs = [{"bucket": "failure_hints", "memory_id": "memory_growth:playbook:lark-send-unstable", "source": "Memory Growth Playbooks", "artifact_path": str(bad_path)}]
    close_turn(
        turn_id="artifact-good-1",
        final_text="ok",
        executed_work_orders=["work-good-1"],
        verification_reports=[VerificationReport(verification_id="verify-good-1", work_order_id="work-good-1", ok=True)],
        memory_context_refs=good_refs,
    )
    close_turn(
        turn_id="artifact-good-2",
        final_text="ok",
        executed_work_orders=["work-good-2"],
        verification_reports=[VerificationReport(verification_id="verify-good-2", work_order_id="work-good-2", ok=True)],
        memory_context_refs=good_refs,
    )
    for index in range(2):
        close_turn(
            turn_id=f"artifact-bad-{index}",
            final_text="failed",
            executed_work_orders=[f"work-bad-{index}"],
            verification_reports=[
                VerificationReport(
                    verification_id=f"verify-bad-{index}",
                    work_order_id=f"work-bad-{index}",
                    ok=False,
                    failure_reason="ocr_send_button_missing",
                )
            ],
            aborted=True,
            memory_context_refs=bad_refs,
        )

    result = run_weekly_review(week_start="2026-07-13", stale_after_days=30)
    payload = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert payload["summary"]["artifact_usage_count"] >= 2
    assert payload["summary"]["artifact_total_use_count"] == 4
    assert payload["artifact_usage"]["top_successful_assets"]
    assert payload["artifact_usage"]["low_success_assets"]

    trend_path = root / "indexes" / "artifact_usage_trends.json"
    assert trend_path.exists()
    trend = json.loads(trend_path.read_text(encoding="utf-8"))
    assert trend["latest"]["week_id"] == result.week_id
    assert trend["latest"]["total_use_count"] == 4
    assert trend["attribution"]["best_playbooks"]
    assert trend["attribution"]["low_success_assets"]
    assert any(row["action"] == "rewrite_or_downrank" for row in trend["recommendations"])

    monitoring = memory_growth_status()["monitoring"]
    assert monitoring["artifact_usage_trends"]["days_30"]
    assert monitoring["artifact_usage_trends"]["days_30"][-1]["week_id"] == result.week_id
    assert monitoring["artifact_usage_attribution"]["low_success_assets"]
    assert monitoring["artifact_usage_recommendations"]


def test_artifact_governance_actions_update_artifacts(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold, memory_growth_dir
    from l3_node.memory_growth_http import apply_memory_growth_governance, memory_growth_status

    ensure_memory_growth_scaffold()
    root = memory_growth_dir()
    low_path = root / "playbooks" / "low-success.md"
    good_path = root / "playbooks" / "good-playbook.md"
    stale_path = root / "concepts" / "project_fact" / "unused-concept.md"
    stale_path.parent.mkdir(parents=True, exist_ok=True)
    low_path.write_text(
        """---
id: "playbook:low-success"
type: "playbook"
summary: "Low success playbook"
memory_use_count: 3
memory_success_count: 0
memory_failure_count: 3
memory_success_rate: 0.0
memory_last_failure_reason: "ocr_send_button_missing"
---

# Low success playbook
""",
        encoding="utf-8",
    )
    good_path.write_text(
        """---
id: "playbook:good-playbook"
type: "playbook"
summary: "Good playbook"
memory_use_count: 4
memory_success_count: 4
memory_failure_count: 0
memory_success_rate: 1.0
---

# Good playbook
""",
        encoding="utf-8",
    )
    stale_path.write_text(
        """---
id: "concept:unused-concept"
type: "project_fact"
summary: "Unused concept"
memory_use_count: 0
memory_success_count: 0
memory_failure_count: 0
memory_success_rate: 0.0
---

# Unused concept
""",
        encoding="utf-8",
    )

    downrank = apply_memory_growth_governance(
        action="rewrite_or_downrank",
        item={"target": "playbooks/low-success.md", "reason": "low_success_rate"},
        note="unit downrank",
    )
    assert any(effect["type"] == "artifact_downranked" for effect in downrank["side_effects"])
    low_text = low_path.read_text(encoding="utf-8")
    assert 'governance_strategy_weight: "0.45"' in low_text
    assert 'artifact_review_status: "needs_rewrite"' in low_text

    recovery = apply_memory_growth_governance(
        action="create_or_update_recovery_playbook",
        item={"target": "playbooks/low-success.md", "reason": "ocr_send_button_missing"},
        note="unit recovery",
    )
    assert any(effect["type"] == "artifact_recovery_playbook_written" for effect in recovery["side_effects"])
    recovery_effect = next(effect for effect in recovery["side_effects"] if effect["type"] == "artifact_recovery_playbook_written")
    assert (root / recovery_effect["path"]).exists()

    promoted = apply_memory_growth_governance(
        action="promote_preferred_guidance",
        item={"target": "playbooks/good-playbook.md"},
        note="unit promote",
    )
    assert any(effect["type"] == "artifact_promoted_preferred_guidance" for effect in promoted["side_effects"])
    good_text = good_path.read_text(encoding="utf-8")
    assert 'preferred_guidance: "true"' in good_text
    assert 'governance_strategy_weight: "1.50"' in good_text

    archived = apply_memory_growth_governance(
        action="archive_or_revalidate",
        item={"target": "concepts/project_fact/unused-concept.md"},
        note="unit archive",
    )
    assert any(effect["type"] == "artifact_archived" for effect in archived["side_effects"])
    assert not stale_path.exists()
    assert (root / "archive" / "artifacts" / "concepts" / "project_fact" / "unused-concept.md").exists()

    status = memory_growth_status()
    actions = status["available_actions"]
    assert "artifact-governance" in actions


def test_artifact_curator_turns_rewrite_requests_into_drafts(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.artifact_curator import run_artifact_curator
    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold, memory_growth_dir
    from l3_node.memory_growth_http import apply_memory_growth_governance, memory_growth_status

    ensure_memory_growth_scaffold()
    root = memory_growth_dir()
    artifact_path = root / "playbooks" / "weak-lark-send.md"
    artifact_path.write_text(
        """---
id: "playbook:weak-lark-send"
type: "playbook"
summary: "Weak Lark send playbook"
memory_use_count: 4
memory_success_count: 1
memory_failure_count: 3
memory_success_rate: 0.25
memory_last_failure_reason: "ocr_send_button_missing"
---

# Weak Lark send playbook

## Recommended Flow

1. Click send directly.
""",
        encoding="utf-8",
    )
    apply_memory_growth_governance(
        action="rewrite_or_downrank",
        item={"target": "playbooks/weak-lark-send.md", "reason": "low_success_rate"},
        note="unit curator seed",
    )

    result = run_artifact_curator(max_items=5)
    assert result.processed_count == 1
    assert result.skipped_count == 0
    assert result.draft_paths
    assert result.confirmation_paths
    draft = json.loads(result.draft_paths[0].read_text(encoding="utf-8"))
    assert draft["artifact_path"] == "playbooks/weak-lark-send.md"
    assert "ocr_send_button_missing" in draft["draft_markdown"]
    assert result.draft_paths[0].with_suffix(".md").exists()
    confirmation = json.loads(result.confirmation_paths[0].read_text(encoding="utf-8"))
    assert confirmation["reason"] == "artifact_rewrite_requires_user_confirmation"
    assert confirmation["candidate"]["requires_user_confirmation"] is True
    assert confirmation["candidate"]["draft_path"] == str(result.draft_paths[0].relative_to(root))

    request_path = next((root / "reviews" / "artifact_rewrites").glob("*.json"))
    request = json.loads(request_path.read_text(encoding="utf-8"))
    assert request["curation_status"] == "drafted"
    status = memory_growth_status()
    assert "artifact-curator" in status["available_actions"]
    assert status["latest"]["artifact_curator_report"]


def test_artifact_draft_merge_updates_source_with_backup(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.artifact_curator import run_artifact_curator
    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold, memory_growth_dir
    from l3_node.memory_growth_http import apply_memory_growth_governance

    ensure_memory_growth_scaffold()
    root = memory_growth_dir()
    artifact_path = root / "playbooks" / "weak-browser-open.md"
    artifact_path.write_text(
        """---
id: "playbook:weak-browser-open"
type: "playbook"
summary: "Weak browser open playbook"
memory_use_count: 3
memory_success_count: 1
memory_failure_count: 2
memory_success_rate: 0.33
memory_last_failure_reason: "app_focus_failed"
---

# Weak browser open playbook

## Recommended Flow

1. Open browser once.
""",
        encoding="utf-8",
    )
    apply_memory_growth_governance(
        action="rewrite_or_downrank",
        item={"target": "playbooks/weak-browser-open.md", "reason": "low_success_rate"},
        note="unit merge seed",
    )
    curator = run_artifact_curator(max_items=5)
    draft_rel = str(curator.draft_paths[0].relative_to(root))
    confirmation_rel = str(curator.confirmation_paths[0].relative_to(root))

    merged = apply_memory_growth_governance(
        action="merge_artifact_draft",
        item={"draft_path": draft_rel, "confirmation_path": confirmation_rel},
        note="unit merge",
    )
    assert any(effect["type"] == "artifact_rewrite_merged" for effect in merged["side_effects"])
    assert any(effect["type"] == "artifact_backup_written" for effect in merged["side_effects"])
    updated = artifact_path.read_text(encoding="utf-8")
    assert "Rewrite Draft: Weak browser open playbook" in updated
    assert 'artifact_review_status: "rewritten"' in updated
    assert 'governance_strategy_action: "artifact_rewrite_merged"' in updated
    draft_payload = json.loads(curator.draft_paths[0].read_text(encoding="utf-8"))
    assert draft_payload["merge_status"] == "merged"
    assert (root / draft_payload["backup_path"]).exists()
    confirmation = json.loads(curator.confirmation_paths[0].read_text(encoding="utf-8"))
    assert confirmation["governance"]["status"] == "confirmed"


def test_confirm_pending_artifact_rewrite_merges_draft(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.artifact_curator import run_artifact_curator
    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold, memory_growth_dir
    from l3_node.memory_growth_http import apply_memory_growth_governance

    ensure_memory_growth_scaffold()
    root = memory_growth_dir()
    artifact_path = root / "concepts" / "project_fact" / "weak-fact.md"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        """---
id: "concept:weak-fact"
type: "project_fact"
summary: "Weak fact"
memory_use_count: 2
memory_success_count: 0
memory_failure_count: 2
memory_success_rate: 0.0
memory_last_failure_reason: "state_conflict"
---

# Weak fact
""",
        encoding="utf-8",
    )
    apply_memory_growth_governance(
        action="rewrite_or_downrank",
        item={"target": "concepts/project_fact/weak-fact.md", "reason": "low_success_rate"},
        note="unit pending merge seed",
    )
    curator = run_artifact_curator(max_items=5)
    confirmation_rel = str(curator.confirmation_paths[0].relative_to(root))
    result = apply_memory_growth_governance(
        action="confirm_pending",
        item={"path": confirmation_rel},
        note="confirm artifact rewrite",
    )
    assert any(effect["type"] == "artifact_rewrite_confirmed_via_pending" for effect in result["side_effects"])
    updated = artifact_path.read_text(encoding="utf-8")
    assert "Rewrite Draft: Weak fact" in updated
    confirmation = json.loads(curator.confirmation_paths[0].read_text(encoding="utf-8"))
    assert confirmation["governance"]["status"] == "confirmed"


def test_memory_growth_e2e_governance_curator_weekly_recall_loop(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.artifact_curator import run_artifact_curator
    from l3_node.cognitive_kernel.contracts import AgentInputEnvelope, InputSource, MemoryRecallRequest
    from l3_node.cognitive_kernel.memory_growth import append_raw_event, ensure_memory_growth_scaffold, memory_growth_dir
    from l3_node.cognitive_kernel.memory_growth_recall import recall_memory_growth
    from l3_node.cognitive_kernel.weekly_review import run_weekly_review
    from l3_node.memory_growth_http import apply_memory_growth_governance, memory_growth_status

    ensure_memory_growth_scaffold()
    root = memory_growth_dir()
    artifact_path = root / "playbooks" / "browser-open-focus.md"
    artifact_path.write_text(
        """---
id: "playbook:browser-open-focus"
type: "playbook"
summary: "Browser open focus recovery"
memory_use_count: 6
memory_success_count: 1
memory_failure_count: 5
memory_success_rate: 0.16
memory_last_failure_reason: "app_focus_failed"
---

# Browser open focus recovery

## Recommended Flow

1. Open browser and assume it is focused.
""",
        encoding="utf-8",
    )
    append_raw_event(
        category="evidence",
        source="unit_e2e",
        stream="verification",
        payload={
            "task_type": "app_control",
            "tool": "windows_app_open",
            "ok": False,
            "failure_reason": "app_focus_failed",
            "artifact_refs": ["playbooks/browser-open-focus.md"],
        },
        review={
            "review_candidate": True,
            "promotion_targets": ["playbooks"],
            "priority": "high",
            "reason": "e2e weak artifact",
        },
    )

    governance = apply_memory_growth_governance(
        action="rewrite_or_downrank",
        item={"target": "playbooks/browser-open-focus.md", "reason": "low_success_rate"},
        note="e2e governance seed",
    )
    assert any(effect["type"] == "artifact_downranked" for effect in governance["side_effects"])
    assert any(effect["type"] == "artifact_rewrite_request_written" for effect in governance["side_effects"])

    curator = run_artifact_curator(max_items=5)
    assert curator.processed_count == 1
    assert curator.confirmation_paths
    confirmation_rel = str(curator.confirmation_paths[0].relative_to(root))
    confirm = apply_memory_growth_governance(
        action="confirm_pending",
        item={"path": confirmation_rel},
        note="e2e approve rewrite",
    )
    assert any(effect["type"] == "artifact_rewrite_confirmed_via_pending" for effect in confirm["side_effects"])
    backup_effect = next(effect for effect in confirm["side_effects"] if effect["type"] == "artifact_backup_written")
    assert (root / backup_effect["path"]).exists()

    weekly = run_weekly_review(week_start="2026-07-13", stale_after_days=30)
    assert weekly.report_path.exists()
    status = memory_growth_status()
    assert status["latest"]["weekly_report"]
    assert status["latest"]["artifact_curator_report"]
    assert status["monitoring"]["artifact_usage"]

    envelope = AgentInputEnvelope(
        turn_id="memory-growth-e2e",
        source=InputSource.TEXT,
        raw_text="打开浏览器后焦点失败应该如何恢复",
        normalized_text="打开浏览器后焦点失败应该如何恢复",
    )
    request = MemoryRecallRequest(
        turn_id="memory-growth-e2e",
        input_envelope=envelope,
        candidate_intents=["app_control"],
        candidate_task_domains=["browser", "window_focus"],
        candidate_entities=["browser", "focus"],
        multi_queries={"task": "browser open app_focus_failed recovery"},
        retrieval_channels=["memory_growth_playbook_memory"],
    )
    recalled, gaps = recall_memory_growth(request, limit=5)
    recalled_ids = {item.memory_id for item in recalled}
    assert "memory_growth:playbook:browser-open-focus" in recalled_ids
    recalled_text = "\n".join(item.content for item in recalled)
    assert "app_focus_failed" in recalled_text
    assert "memory_growth_no_relevant_concepts_or_playbooks" not in gaps
