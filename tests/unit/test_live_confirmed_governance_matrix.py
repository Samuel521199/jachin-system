import asyncio
import json
from pathlib import Path

from l3_node.cognitive_kernel.contracts import DecisionContract, RiskLevel, ToolPolicy, WorkOrder
from l3_node.cognitive_kernel.dispatcher import dispatch_existing_work_order
from l3_node.cognitive_kernel.ledger import current_ledger_path


def _contract(*, turn_id: str, task_type: str, goal: str, tool: str, role: str) -> DecisionContract:
    return DecisionContract(
        decision_id=f"decision-{turn_id}",
        turn_id=turn_id,
        task_type=task_type,
        goal=goal,
        selected_workflow="live_confirmed_governance_matrix",
        selected_roles=[role, "VerificationAgent", "RecoveryAgent", "TurnClosureAgent"],
        risk_level=RiskLevel.LOW,
        tool_policy=ToolPolicy(allowed_tools=[tool], risk_level=RiskLevel.LOW),
        execution_allowed=True,
        verification_criteria=["role execution evidence", "tool quality gate passed"],
    )


def _work_order(
    *,
    turn_id: str,
    contract: DecisionContract,
    role: str,
    task: str,
    tool: str,
    payload: dict,
    governance: dict | None = None,
    recovery_paths: list[dict] | None = None,
) -> WorkOrder:
    inputs = {
        "tool": tool,
        "work_order_input": json.dumps(payload, ensure_ascii=False),
    }
    if governance:
        inputs["governance_policy"] = governance
    if recovery_paths:
        inputs["capability_profile"] = {
            "capability_id": governance.get("capability") if governance else "skill:test.live",
            "recovery_paths": recovery_paths,
        }
    return WorkOrder(
        work_order_id=f"work-{turn_id}",
        decision_id=contract.decision_id,
        role_agent=role,
        task=task,
        inputs=inputs,
        tool_policy=contract.tool_policy,
        verification_criteria=contract.verification_criteria,
    )


def _ledger_events() -> list[dict]:
    path = current_ledger_path()
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_live_confirmed_governance_blocks_manual_review_before_executor(monkeypatch, tmp_path):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))
    called = False

    async def executor(_work_order: WorkOrder) -> str:
        nonlocal called
        called = True
        return json.dumps({"ok": True}, ensure_ascii=False)

    contract = _contract(
        turn_id="live-governance-manual-review",
        task_type="app_control",
        goal="打开低健康 App",
        tool="mcp:windows_open_app",
        role="AppControlExecutorAgent",
    )
    work_order = _work_order(
        turn_id=contract.turn_id,
        contract=contract,
        role="AppControlExecutorAgent",
        task="app_control",
        tool="mcp:windows_open_app",
        payload={"app": "Calculator"},
        governance={
            "capability": "skill:test.low.health.app",
            "score": 42,
            "level": "critical",
            "execution_mode": "manual_review",
            "requires_confirmation": True,
            "reason": "capability_health_critical_requires_confirmation",
        },
    )

    result = asyncio.run(
        dispatch_existing_work_order(contract=contract, work_order=work_order, executor=executor)
    )

    assert called is False
    assert result.verification.ok is False
    assert result.recovery_plan is not None
    assert result.contract.tool_policy.requires_confirmation is True
    assert "capability_health_critical_requires_confirmation" in result.contract.clarification_question
    events = _ledger_events()
    assert any(e["event_type"] == "failure_learning_recorded" for e in events)
    assert not any(e["event_type"] == "role_execution_started" for e in events)


def test_live_confirmed_degraded_app_recovery_switches_path(monkeypatch, tmp_path):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))
    calls: list[dict] = []

    async def executor(work_order: WorkOrder) -> str:
        recovery = work_order.inputs.get("recovery") if isinstance(work_order.inputs.get("recovery"), dict) else {}
        calls.append({"tool": work_order.inputs.get("tool"), "strategy": recovery.get("strategy") or "initial"})
        if not recovery:
            return json.dumps({"ok": False, "error": "app_focus_failed"}, ensure_ascii=False)
        return json.dumps(
            {
                "ok": True,
                "task": "open_app",
                "active_window": "Calculator",
                "foreground": True,
                "screenshot": "fake.png",
            },
            ensure_ascii=False,
        )

    contract = _contract(
        turn_id="live-governance-app-recovery",
        task_type="app_control",
        goal="打开计算器并校验前台",
        tool="mcp:app_open",
        role="AppControlExecutorAgent",
    )
    work_order = _work_order(
        turn_id=contract.turn_id,
        contract=contract,
        role="AppControlExecutorAgent",
        task="app_control",
        tool="mcp:app_open",
        payload={"app": "Calculator"},
        governance={
            "capability": "skill:test.degraded.app",
            "score": 61,
            "level": "degraded",
            "execution_mode": "degraded_auto",
        },
        recovery_paths=[
            {
                "role_agent": "AppControlExecutorAgent",
                "tools": ["mcp:app_open"],
                "max_attempts": 3,
                "steps": [
                    {
                        "strategy": "retry_same_path",
                        "tool": "$same",
                        "when": {"failure_any": ["focus"]},
                        "priority": 1,
                    },
                    {
                        "strategy": "switch_to_window_focus",
                            "tool": "mcp:app_switch",
                        "when": {"failure_any": ["focus"]},
                        "action_patch": {"keywords": "Calculator"},
                        "priority": 30,
                    },
                ],
            }
        ],
    )

    result = asyncio.run(
        dispatch_existing_work_order(contract=contract, work_order=work_order, executor=executor)
    )

    assert result.verification.ok is True
    assert [call["strategy"] for call in calls] == ["initial", "switch_to_window_focus"]
    assert result.attempts is not None
    assert result.attempts[-1]["ok"] is True
    assert result.attempts[-1]["strategy"] == "switch_to_window_focus"
    events = _ledger_events()
    assert any(e["event_type"] == "recovery_attempt_planned" for e in events)
    assert any(e["event_type"] == "recovery_execution_finished" and e["payload"]["ok"] is True for e in events)
    role_events = [e for e in events if e["event_type"] == "role_execution_started"]
    assert any(
        ((e["payload"].get("evidence") or {}).get("governance_policy") or {}).get("score") == 61
        for e in role_events
    )


def test_live_confirmed_message_without_post_send_evidence_cannot_fake_success(monkeypatch, tmp_path):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    async def executor(_work_order: WorkOrder) -> str:
        return json.dumps(
            {
                "ok": True,
                "recipient": "Neil",
                "message": "你好",
            },
            ensure_ascii=False,
        )

    contract = _contract(
        turn_id="live-governance-message-unverified",
        task_type="message_delivery",
        goal="给 Neil 发消息",
        tool="mcp:windows_lark_send_message",
        role="MessageExecutorAgent",
    )
    work_order = _work_order(
        turn_id=contract.turn_id,
        contract=contract,
        role="MessageExecutorAgent",
        task="message_delivery",
        tool="mcp:windows_lark_send_message",
        payload={"recipient": "Neil", "message": "你好"},
        governance={
            "capability": "skill:test.message",
            "score": 88,
            "level": "healthy",
            "execution_mode": "normal",
        },
    )

    result = asyncio.run(
        dispatch_existing_work_order(contract=contract, work_order=work_order, executor=executor)
    )

    assert result.verification.ok is False
    assert result.verification.failure_reason == "message_post_send_verification_missing"
    events = _ledger_events()
    quality_events = [
        item
        for e in events
        for item in (e.get("payload") or {}).get("evidence", [])
        if isinstance(item, dict) and item.get("type") == "tool_quality"
    ]
    assert any(item["tool"] == "mcp:windows_lark_send_message" for item in quality_events)
    assert any("message_post_send_unverified" in item.get("issues", []) for item in quality_events)
    assert any(e["event_type"] == "failure_learning_recorded" for e in events)
