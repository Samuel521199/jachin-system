import json

from l3_node.capability_router import choose_capability_route
from l3_node.mission_intent_schema import MissionTaskType
from l3_node.mission_user_feedback import build_mission_user_result, format_mission_user_reply
from l3_node.semantic_slot_parser import parse_mission_intent


def test_calculator_visual_warning_becomes_user_completed_result() -> None:
    intent = parse_mission_intent(
        "\u7ed9\u6211\u6253\u5f00 windows \u7684\u8ba1\u7b97\u5668\u7b97\u4e00\u4e0b\u56db\u5341\u4e58\u4ee5\u4e94\u5341\u52a0\u4e00\u767e\u7b49\u4e8e\u51e0"
    )
    intent.task_type = MissionTaskType.CALCULATOR_CALCULATE
    intent.slots.expression = "40*50+100"
    route = choose_capability_route(intent, [{"id": "mcp:windows_calculator_calculate"}])
    result_data = {
        "task": "calculator",
        "ok": False,
        "detail": "visual_or_result_mismatch",
        "evidence": {
            "expect": "2100",
            "clipboard_raw": "2100",
            "clipboard_norm": "2100",
            "visual": {
                "ok": True,
                "expression_norm": "2000+100",
                "result": "2,100",
                "result_norm": "2100",
            },
        },
    }

    task_result = build_mission_user_result(
        intent=intent,
        route=route,
        result_data=result_data,
        runtime={"metrics": {"attempt_count": 1}},
    )

    assert task_result.success is True
    assert task_result.status == "completed_with_warning"
    assert task_result.user_result == "2100"
    assert "40 乘以 50 加 100 等于 2100" in task_result.user_summary

    reply = format_mission_user_reply(
        task_result=task_result,
        intent=intent,
        route=route,
        router_evidence="D:/evidence/router.json",
        result_data=result_data,
    )

    first_line = reply.splitlines()[0]
    assert "Task Preview" not in first_line
    assert "visual_or_result_mismatch" not in first_line
    assert "\u6211\u7b97\u597d\u4e86" in first_line


def test_generic_failed_mission_keeps_technical_detail_out_of_first_line() -> None:
    intent = parse_mission_intent("\u6253\u5f00 Lark")
    route = choose_capability_route(intent, [{"id": "mcp:windows_open_app"}])
    result_data = json.loads('{"task":"windows_open_app","ok":false,"detail":"workflow_failed"}')

    task_result = build_mission_user_result(
        intent=intent,
        route=route,
        result_data=result_data,
        runtime={"metrics": {"attempt_count": 1}},
    )
    reply = format_mission_user_reply(
        task_result=task_result,
        intent=intent,
        route=route,
        router_evidence="D:/evidence/router.json",
        result_data=result_data,
    )

    assert task_result.success is False
    assert "\u5de5\u4f5c\u6d41\u6ca1\u6709\u5b8c\u6210" in reply.splitlines()[0]
    assert "workflow_failed" not in reply.splitlines()[0]
    assert "Router Evidence" not in reply
    assert "workflow" not in reply



def test_codex_ask_missing_executable_feedback_is_human_readable() -> None:
    from l3_node.mission_intent_schema import CapabilityRoute, MissionIntent, MissionSlots, MissionTaskType

    intent = MissionIntent(
        task_type=MissionTaskType.CODEX_ASK_LARK_SEND,
        confidence=0.95,
        slots=MissionSlots(recipients=["vivian"]),
    )
    route = CapabilityRoute(ok=True, tool_id="mcp:windows_codex_ask_lark_send", workflow_id="codex_ask_lark_send")
    result_data = {
        "ok": False,
        "detail": "app_executable_not_found",
        "evidence": {"app": "codex", "candidate_paths": ["Codex.exe"]},
    }

    task_result = build_mission_user_result(
        intent=intent,
        route=route,
        result_data=result_data,
        runtime={"metrics": {"failure_class": "app_executable_not_found", "attempt_count": 1}},
    )
    reply = format_mission_user_reply(task_result=task_result, intent=intent, route=route, router_evidence="", result_data=result_data)

    first_line = reply.splitlines()[0]
    assert task_result.success is False
    assert "没有找到目标应用的启动程序" in first_line
    assert "FileNotFoundError" not in first_line
    assert "app_executable_not_found" not in first_line

def test_calculator_wrong_foreground_feedback_names_active_window() -> None:
    from l3_node.mission_intent_schema import CapabilityRoute, MissionIntent, MissionSlots, MissionTaskType

    intent = MissionIntent(
        task_type=MissionTaskType.CALCULATOR_CALCULATE,
        confidence=0.95,
        slots=MissionSlots(expression="40+30"),
    )
    route = CapabilityRoute(ok=True, tool_id="mcp:windows_calculator_calculate", workflow_id="windows_calculator_calculate")
    result_data = {
        "ok": False,
        "detail": "wrong_foreground_app",
        "evidence": {
            "expect": "70",
            "environment_guard": {"active": {"title": "Codex", "process": "Codex.exe"}},
        },
    }

    task_result = build_mission_user_result(intent=intent, route=route, result_data=result_data, runtime={})
    reply = format_mission_user_reply(task_result=task_result, intent=intent, route=route, router_evidence="", result_data=result_data)

    assert task_result.success is False
    assert task_result.user_result == ""
    assert "Codex.exe" in reply
    assert "\u4e0d\u662f\u8ba1\u7b97\u5668" in reply
    assert "\u6ca1\u6709\u7ee7\u7eed\u8f93\u5165" in reply


def test_lark_wrong_foreground_feedback_is_honest() -> None:
    from l3_node.mission_intent_schema import CapabilityRoute, MissionIntent, MissionSlots, MissionTaskType

    intent = MissionIntent(
        task_type=MissionTaskType.LARK_MESSAGE_SEND,
        confidence=0.95,
        slots=MissionSlots(recipients=["Vivian"], message="hello"),
    )
    route = CapabilityRoute(ok=True, tool_id="mcp:windows_lark_send_message", workflow_id="windows_lark_message_send")
    result_data = {"ok": False, "detail": "wrong_foreground_app", "evidence": {"environment_guard": {"active": {"process": "Cursor.exe"}}}}

    task_result = build_mission_user_result(intent=intent, route=route, result_data=result_data, runtime={})
    reply = format_mission_user_reply(task_result=task_result, intent=intent, route=route, router_evidence="", result_data=result_data)

    assert "Vivian" in reply
    assert "目标应用窗口被其他窗口抢到前台" in reply
    assert "尝试重新切回" in reply
    assert "\u907f\u514d\u8bef\u64cd\u4f5c" in reply


def test_lark_wrong_recipient_feedback_stops_before_sending() -> None:
    from l3_node.mission_intent_schema import CapabilityRoute, MissionIntent, MissionSlots, MissionTaskType

    intent = MissionIntent(
        task_type=MissionTaskType.LARK_MESSAGE_SEND,
        confidence=0.95,
        slots=MissionSlots(recipients=["Vivian"], message="hello"),
    )
    route = CapabilityRoute(ok=True, tool_id="mcp:windows_lark_send_message", workflow_id="windows_lark_message_send")
    result_data = {
        "ok": False,
        "detail": "wrong_recipient",
        "evidence": {"deliveries": [{"failure_stage": "wrong_recipient_opened"}]},
    }

    task_result = build_mission_user_result(intent=intent, route=route, result_data=result_data, runtime={})
    reply = format_mission_user_reply(task_result=task_result, intent=intent, route=route, router_evidence="", result_data=result_data)

    assert task_result.success is False
    assert "Vivian" in reply
    assert "\u5f53\u524d\u6253\u5f00\u7684 Lark \u4f1a\u8bdd\u4e0d\u662f\u76ee\u6807\u8054\u7cfb\u4eba" in reply
    assert "\u907f\u514d\u53d1\u9519\u4eba" in reply


def test_calculator_expression_ocr_incomplete_is_completed_with_warning() -> None:
    from l3_node.mission_intent_schema import CapabilityRoute, MissionIntent, MissionSlots, MissionTaskType

    intent = MissionIntent(
        task_type=MissionTaskType.CALCULATOR_CALCULATE,
        confidence=0.95,
        slots=MissionSlots(expression="7*8"),
    )
    route = CapabilityRoute(ok=True, tool_id="mcp:windows_calculator_calculate", workflow_id="windows_calculator_calculate")
    result_data = {
        "task": "calculator",
        "ok": True,
        "detail": "result_verified_expression_ocr_incomplete",
        "evidence": {
            "expect": "56",
            "clipboard_norm": "56",
            "visual": {"result_norm": "56", "expression_norm": "*8"},
        },
    }

    task_result = build_mission_user_result(intent=intent, route=route, result_data=result_data, runtime={})
    reply = format_mission_user_reply(task_result=task_result, intent=intent, route=route, router_evidence="", result_data=result_data)

    assert task_result.success is True
    assert task_result.status == "completed_with_warning"
    assert task_result.user_result == "56"
    assert "result_verified_expression_ocr_incomplete" not in reply.splitlines()[0]
    assert "OCR" not in reply.splitlines()[0]


def test_codex_lark_mouse_failsafe_feedback_is_human_and_stage_aware() -> None:
    from l3_node.mission_intent_schema import CapabilityRoute, MissionIntent, MissionSlots, MissionTaskType

    intent = MissionIntent(
        task_type=MissionTaskType.CODEX_ASK_LARK_SEND,
        confidence=0.95,
        slots=MissionSlots(recipients=["vivian"], feature_query="Jachin skills"),
    )
    route = CapabilityRoute(ok=True, tool_id="mcp:windows_codex_ask_lark_send", workflow_id="codex_ask_lark_send")
    result_data = {
        "ok": False,
        "detail": "mouse_failsafe_triggered",
        "evidence": {
            "timeline": [{"stage": "open_codex", "status": "done"}],
            "mouse_failsafe": {"action": "screenshot_active_window"},
        },
    }

    task_result = build_mission_user_result(
        intent=intent,
        route=route,
        result_data=result_data,
        runtime={"metrics": {"failure_class": "mouse_failsafe_triggered", "attempt_count": 1}},
    )
    reply = format_mission_user_reply(task_result=task_result, intent=intent, route=route, router_evidence="", result_data=result_data)

    assert task_result.status == "interrupted"
    assert "vivian" in reply
    assert "open_codex" in reply
    assert "安全急停" in reply
    assert "没有继续发送 Lark 消息" in reply
    assert "FailSafeException" not in reply
    assert "mouse_failsafe_triggered" not in reply


def test_codex_lark_workflow_code_defect_feedback_is_human() -> None:
    from l3_node.mission_intent_schema import CapabilityRoute, MissionIntent, MissionSlots, MissionTaskType

    intent = MissionIntent(
        task_type=MissionTaskType.CODEX_ASK_LARK_SEND,
        confidence=0.95,
        slots=MissionSlots(recipients=["vivian"], feature_query="Jachin skills"),
    )
    route = CapabilityRoute(ok=True, tool_id="mcp:windows_codex_ask_lark_send", workflow_id="codex_ask_lark_send")
    result_data = {"ok": False, "detail": "failed:NameError(\"name '_choose_codex_generic_reply' is not defined\")", "evidence": {}}

    task_result = build_mission_user_result(
        intent=intent,
        route=route,
        result_data=result_data,
        runtime={"metrics": {"failure_class": "workflow_code_defect", "attempt_count": 1}},
    )
    reply = format_mission_user_reply(task_result=task_result, intent=intent, route=route, router_evidence="", result_data=result_data)

    assert task_result.success is False
    assert "vivian" in reply
    assert "Codex" in reply
    assert "Lark" in reply
    assert "NameError" not in reply
    assert "_choose_codex_generic_reply" not in reply