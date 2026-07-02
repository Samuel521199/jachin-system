import json

from l3_node.capability_router import choose_capability_route
from l3_node.mission_control_center import should_hold_for_confirmation
from l3_node.mission_intent_schema import CapabilityRoute, MissionIntent, MissionRiskLevel, MissionSlots, MissionTaskType
from l3_node.mission_runtime import build_plan_preview, classify_failure, execute_with_retry
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


def test_app_executable_not_found_is_classified_without_retry() -> None:
    intent = parse_mission_intent("打开 codex")
    route = CapabilityRoute(ok=True, tool_id="mcp:windows_open_app", workflow_id="windows_open_app")
    result = {
        "ok": False,
        "detail": "app_executable_not_found",
        "evidence": {"app": "codex", "candidate_paths": ["Codex.exe"]},
    }

    assert classify_failure(intent, route, result) == "app_executable_not_found"

    runtime = execute_with_retry(
        intent=intent,
        route=route,
        execute_once=lambda: json.dumps(result, ensure_ascii=False),
        parse_result=lambda text: json.loads(text),
    )

    assert runtime["metrics"]["attempt_count"] == 1
    assert runtime["retry"]["should_retry"] is False
    assert runtime["metrics"]["failure_class"] == "app_executable_not_found"

def test_classify_lark_preview_message_failure_before_recipient_fallback() -> None:
    intent = MissionIntent(
        task_type=MissionTaskType.LARK_MESSAGE_SEND,
        confidence=0.9,
        slots=MissionSlots(recipients=["vivian"], message="一条测试消息"),
        raw_text="打开lark给vivian发送一条测试消息",
    )
    route = CapabilityRoute(ok=True, tool_id="mcp:windows_lark_send_message", workflow_id="windows_lark_message_send")
    result = {
        "ok": False,
        "detail": "draft_preview_verification_failed",
        "evidence": {
            "deliveries": [
                {
                    "recipient": "vivian",
                    "ok": False,
                    "recipient_visible": True,
                    "message_visible": False,
                    "preview_verified": False,
                    "failure_stage": "message_preview_verification_failed",
                    "attempts": [
                        {
                            "preview_recipient_visible": True,
                            "preview_message_visible": False,
                        }
                    ],
                }
            ]
        },
    }

    assert classify_failure(intent, route, result) == "message_preview_not_verified"

    runtime = execute_with_retry(
        intent=intent,
        route=route,
        execute_once=lambda: json.dumps(result, ensure_ascii=False),
        parse_result=lambda text: json.loads(text),
    )

    assert runtime["attempts"][0]["failure_class"] == "message_preview_not_verified"
    assert runtime["retry"]["safe_to_retry"] is True



def test_wrong_foreground_failure_is_safely_retried() -> None:
    intent = MissionIntent(
        task_type=MissionTaskType.LARK_MESSAGE_SEND,
        confidence=0.9,
        slots=MissionSlots(recipients=["vivian"], message="hello"),
    )
    route = CapabilityRoute(ok=True, tool_id="mcp:windows_lark_send_message", workflow_id="windows_lark_message_send")
    result = {"ok": False, "detail": "wrong_foreground_app", "evidence": {"environment_guard": {"active": {"process": "Cursor.exe"}}}}

    assert classify_failure(intent, route, result) == "wrong_foreground_app"
    retry = execute_with_retry(
        intent=intent,
        route=route,
        execute_once=lambda: json.dumps(result, ensure_ascii=False),
        parse_result=lambda text: json.loads(text),
    )

    assert retry["metrics"]["attempt_count"] == 2
    assert retry["retry"]["should_retry"] is True
    assert retry["retry"]["safe_to_retry"] is True
    assert retry["metrics"]["failure_class"] == "wrong_foreground_app"


def test_wrong_recipient_failure_is_safely_retried() -> None:
    intent = MissionIntent(
        task_type=MissionTaskType.LARK_MESSAGE_SEND,
        confidence=0.9,
        slots=MissionSlots(recipients=["vivian"], message="hello"),
    )
    route = CapabilityRoute(ok=True, tool_id="mcp:windows_lark_send_message", workflow_id="windows_lark_message_send")
    result = {
        "ok": False,
        "detail": "wrong_recipient",
        "evidence": {"deliveries": [{"failure_stage": "wrong_recipient_opened"}]},
    }

    assert classify_failure(intent, route, result) == "wrong_recipient"
    runtime = execute_with_retry(
        intent=intent,
        route=route,
        execute_once=lambda: json.dumps(result, ensure_ascii=False),
        parse_result=lambda text: json.loads(text),
    )

    assert runtime["attempts"][0]["failure_class"] == "wrong_recipient"
    assert runtime["retry"]["should_retry"] is True
    assert runtime["retry"]["safe_to_retry"] is True

def test_mouse_failsafe_is_classified_and_safe_tasks_retry_once() -> None:
    intent = MissionIntent(
        task_type=MissionTaskType.CALCULATOR_CALCULATE,
        confidence=0.95,
        slots=MissionSlots(expression="1+1"),
    )
    route = CapabilityRoute(ok=True, tool_id="mcp:windows_calculator_calculate", workflow_id="windows_calculator_calculate")
    result = {"ok": False, "detail": "mouse_failsafe_triggered", "evidence": {"mouse_failsafe": {"action": "press"}}}

    assert classify_failure(intent, route, result) == "mouse_failsafe_triggered"
    runtime = execute_with_retry(
        intent=intent,
        route=route,
        execute_once=lambda: json.dumps(result, ensure_ascii=False),
        parse_result=lambda text: json.loads(text),
    )

    assert runtime["metrics"]["attempt_count"] == 2
    assert runtime["retry"]["safe_to_retry"] is True
    assert runtime["metrics"]["failure_class"] == "mouse_failsafe_triggered"


def test_mouse_failsafe_does_not_blindly_retry_side_effect_workflow() -> None:
    intent = MissionIntent(
        task_type=MissionTaskType.CODEX_ASK_LARK_SEND,
        confidence=0.95,
        slots=MissionSlots(recipients=["vivian"], feature_query="hello"),
    )
    route = CapabilityRoute(ok=True, tool_id="mcp:windows_codex_ask_lark_send", workflow_id="codex_ask_lark_send")
    result = {"ok": False, "detail": "mouse_failsafe_triggered", "evidence": {"side_effect_status": "unknown"}}

    runtime = execute_with_retry(
        intent=intent,
        route=route,
        execute_once=lambda: json.dumps(result, ensure_ascii=False),
        parse_result=lambda text: json.loads(text),
    )

    assert runtime["metrics"]["attempt_count"] == 1
    assert runtime["retry"]["safe_to_retry"] is False
    assert runtime["metrics"]["failure_class"] == "mouse_failsafe_triggered"


def test_nameerror_missing_workflow_dependency_is_code_defect() -> None:
    intent = MissionIntent(
        task_type=MissionTaskType.CODEX_ASK_LARK_SEND,
        confidence=0.95,
        slots=MissionSlots(recipients=["vivian"], feature_query="skills"),
    )
    route = CapabilityRoute(ok=True, tool_id="mcp:windows_codex_ask_lark_send", workflow_id="codex_ask_lark_send")
    result = {"ok": False, "detail": "failed:NameError(\"name '_choose_codex_generic_reply' is not defined\")"}

    assert classify_failure(intent, route, result) == "workflow_code_defect"
    runtime = execute_with_retry(
        intent=intent,
        route=route,
        execute_once=lambda: json.dumps(result, ensure_ascii=False),
        parse_result=lambda text: json.loads(text),
    )

    assert runtime["metrics"]["attempt_count"] == 1
    assert runtime["retry"]["safe_to_retry"] is False
    assert runtime["metrics"]["failure_class"] == "workflow_code_defect"