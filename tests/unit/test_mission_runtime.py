import json

from l3_node.capability_router import choose_capability_route
from l3_node.mission_control_center import should_hold_for_confirmation
from l3_node.mission_intent_schema import CapabilityRoute, MissionIntent, MissionRiskLevel, MissionSlots, MissionTaskType
from l3_node.mission_runtime import build_plan_preview, execute_with_retry
from l3_node.semantic_slot_parser import parse_mission_intent


def test_plan_preview_for_codex_lark_project_briefing() -> None:
    intent = parse_mission_intent("总结 Jachin 最近 3 天进展，按条列出来，发给 Vivian")
    route = choose_capability_route(intent, [{"id": "mcp:windows_codex_lark_workflow_template"}])

    plan = build_plan_preview(intent, route)

    assert plan.auto_execute is True
    assert plan.requires_confirmation is False
    assert "Codex" in plan.apps
    assert "Lark" in plan.apps
    assert plan.recipients == ["Vivian"]
    assert [step.stage for step in plan.steps] == [
        "resolve_project",
        "open_codex",
        "wait_codex",
        "open_lark",
        "send_lark",
    ]


def test_default_confirmation_policy_only_holds_high_risk(monkeypatch) -> None:
    monkeypatch.delenv("JACHIN_OS_MISSION_CONFIRM_MODE", raising=False)
    route = CapabilityRoute(ok=True, tool_id="mcp:windows_codex_lark_workflow_template", workflow_id="codex_project_briefing_to_lark")
    low_risk_intent = MissionIntent(
        task_type=MissionTaskType.PROJECT_BRIEFING_DELIVERY,
        confidence=0.95,
        slots=MissionSlots(project_name="Jachin", recipients=["Neil"], since_days=3),
        risk_level=MissionRiskLevel.LOW,
        raw_text="summarize Jachin and send to Neil",
    )
    low_risk_plan = build_plan_preview(low_risk_intent, route)

    assert low_risk_plan.requires_confirmation is False
    assert should_hold_for_confirmation(low_risk_intent, low_risk_plan) is False

    high_risk_intent = MissionIntent(
        task_type=MissionTaskType.UNKNOWN,
        confidence=0.95,
        slots=MissionSlots(file_path="D:/dangerous.zip", app_name="Lark", recipients=["Neil"]),
        risk_level=MissionRiskLevel.HIGH,
        raw_text="send a high risk file to Neil",
    )
    high_risk_plan = build_plan_preview(high_risk_intent, CapabilityRoute(ok=True, tool_id="mcp:dangerous_operation", workflow_id="dangerous_operation"))

    assert high_risk_plan.requires_confirmation is True
    assert should_hold_for_confirmation(high_risk_intent, high_risk_plan) is True


def test_execute_with_retry_retries_safe_app_failure_once() -> None:
    intent = parse_mission_intent("打开 Lark")
    route = choose_capability_route(intent, [{"id": "mcp:windows_open_app"}])
    calls = 0

    def run_once() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            return json.dumps({"ok": False, "detail": "lark_open_failed"}, ensure_ascii=False)
        return json.dumps({"ok": True, "detail": "app_ready"}, ensure_ascii=False)

    runtime = execute_with_retry(
        intent=intent,
        route=route,
        execute_once=run_once,
        parse_result=lambda text: json.loads(text),
    )

    assert calls == 2
    assert runtime["metrics"]["final_ok"] is True
    assert runtime["metrics"]["attempt_count"] == 2
    assert runtime["attempts"][0]["failure_class"] == "lark_open_failed"


def test_execute_with_retry_does_not_retry_missing_project() -> None:
    intent = parse_mission_intent("总结 Jachin 最近进展发给 Vivian")
    route = choose_capability_route(intent, [{"id": "mcp:windows_codex_lark_workflow_template"}])

    runtime = execute_with_retry(
        intent=intent,
        route=route,
        execute_once=lambda: json.dumps({"ok": False, "detail": "project_path_required"}, ensure_ascii=False),
        parse_result=lambda text: json.loads(text),
    )

    assert runtime["metrics"]["final_ok"] is False
    assert runtime["metrics"]["attempt_count"] == 1
    assert runtime["retry"]["should_retry"] is False
    assert runtime["metrics"]["failure_class"] == "missing_project_path"
