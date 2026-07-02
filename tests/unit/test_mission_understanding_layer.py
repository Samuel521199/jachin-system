from l3_node.mission_intent_schema import MissionTaskType


def test_semantic_parser_handles_fuzzy_project_delivery() -> None:
    from l3_node.semantic_slot_parser import parse_mission_intent

    intent = parse_mission_intent("看看 Jachin 这几天干了啥，整理成几条发给 Neil")

    assert intent.task_type == MissionTaskType.PROJECT_BRIEFING_DELIVERY
    assert intent.confidence >= 0.75
    assert intent.slots.project_name == "Jachin"
    assert intent.slots.recipients == ["Neil"]
    assert intent.slots.output_format == "bullet_points"


def test_semantic_parser_handles_plain_lark_message() -> None:
    from l3_node.semantic_slot_parser import parse_mission_intent

    intent = parse_mission_intent("给 Vivian 发 你好，我已经在测试")

    assert intent.task_type == MissionTaskType.LARK_MESSAGE_SEND
    assert intent.slots.recipients == ["Vivian"]
    assert intent.slots.message == "你好，我已经在测试"

def test_semantic_parser_keeps_explicit_lark_send_over_open_lark_prefix() -> None:
    from l3_node.semantic_slot_parser import parse_mission_intent

    intent = parse_mission_intent("打开 Lark 给 Vivian 发 你好")

    assert intent.task_type == MissionTaskType.LARK_MESSAGE_SEND
    assert intent.slots.recipients == ["Vivian"]
    assert intent.slots.message == "你好"


def test_capability_router_forces_codex_for_project_delivery() -> None:
    from l3_node.capability_router import choose_capability_route
    from l3_node.semantic_slot_parser import parse_mission_intent

    intent = parse_mission_intent("总结 Jachin 最近 3 天做了什么，发给 Neil")
    route = choose_capability_route(
        intent,
        [
            {"id": "util:lark_send_text"},
            {"id": "mcp:windows_project_latest_briefing"},
            {"id": "mcp:windows_codex_lark_workflow_template"},
        ],
    )

    assert route.ok is True
    assert route.tool_id == "mcp:windows_codex_lark_workflow_template"


def test_clarification_policy_asks_one_question_for_missing_project() -> None:
    from l3_node.capability_router import choose_capability_route
    from l3_node.clarification_policy import decide_clarification
    from l3_node.semantic_slot_parser import parse_mission_intent

    intent = parse_mission_intent("总结最近 3 天做了什么，发给 Neil")
    route = choose_capability_route(intent, [{"id": "mcp:windows_codex_lark_workflow_template"}])
    decision = decide_clarification(intent, route)

    assert decision.should_ask is True
    assert decision.reason == "missing_project"
    assert "哪个项目" in decision.question


def test_semantic_parser_handles_project_memory_assignment() -> None:
    from l3_node.semantic_slot_parser import parse_mission_intent

    intent = parse_mission_intent(r"Jachin = D:\Projects\jachi\jachin-system-main")

    assert intent.task_type == MissionTaskType.PROJECT_MEMORY_UPDATE
    assert intent.slots.project_name == "Jachin"
    assert intent.slots.project_path == r"D:\Projects\jachi\jachin-system-main"


def test_semantic_parser_handles_trailing_recipient_briefing() -> None:
    from l3_node.semantic_slot_parser import parse_mission_intent

    intent = parse_mission_intent("把最近改动整理几条给 Vivian")

    assert intent.task_type == MissionTaskType.PROJECT_BRIEFING_DELIVERY
    assert intent.slots.recipients == ["Vivian"]
    assert intent.slots.output_format == "bullet_points"
    assert intent.missing_slots == ["project"]


def test_semantic_parser_does_not_swallow_recent_marker_after_ascii_project() -> None:
    from l3_node.semantic_slot_parser import parse_mission_intent

    intent = parse_mission_intent("总结Jachin最近开发了什么新功能，使用codex总结然后发给Neil")

    assert intent.task_type == MissionTaskType.PROJECT_BRIEFING_DELIVERY
    assert intent.slots.project_name == "Jachin"
    assert intent.slots.recipients == ["Neil"]


def test_semantic_parser_handles_path_to_group_briefing() -> None:
    from l3_node.semantic_slot_parser import parse_mission_intent

    intent = parse_mission_intent(r"把 D:\Projects\demo 的进展总结一下发给研发群")

    assert intent.task_type == MissionTaskType.PROJECT_BRIEFING_DELIVERY
    assert intent.slots.project_path == r"D:\Projects\demo"
    assert intent.slots.recipients == ["研发群"]


def test_semantic_parser_routes_command_prefix_calculator_to_local_calculator() -> None:
    from l3_node.capability_router import choose_capability_route
    from l3_node.semantic_slot_parser import parse_mission_intent

    intent = parse_mission_intent("给我打开windows上原生的计算器给我算20+70等于几")
    route = choose_capability_route(
        intent,
        [
            {"id": "mcp:windows_lark_send_message"},
            {"id": "mcp:windows_open_app"},
            {"id": "mcp:windows_calculator_calculate"},
        ],
    )

    assert intent.task_type == MissionTaskType.CALCULATOR_CALCULATE
    assert intent.slots.app_name == "calculator"
    assert intent.slots.expression == "20+70"
    assert route.ok is True
    assert route.tool_id == "mcp:windows_calculator_calculate"


def test_semantic_parser_treats_geiwo_open_calculator_as_app_control() -> None:
    from l3_node.capability_router import choose_capability_route
    from l3_node.semantic_slot_parser import parse_mission_intent

    intent = parse_mission_intent("给我打开计算器")
    route = choose_capability_route(
        intent,
        [{"id": "mcp:windows_lark_send_message"}, {"id": "mcp:windows_open_app"}],
    )

    assert intent.task_type == MissionTaskType.APP_CONTROL
    assert intent.slots.app_name == "calculator"
    assert route.ok is True
    assert route.tool_id == "mcp:windows_open_app"


def test_semantic_parser_does_not_turn_geiwo_send_message_into_lark_send() -> None:
    from l3_node.capability_router import choose_capability_route
    from l3_node.semantic_slot_parser import parse_mission_intent

    intent = parse_mission_intent("给我发消息")
    route = choose_capability_route(intent, [{"id": "mcp:windows_lark_send_message"}])

    assert intent.task_type == MissionTaskType.UNKNOWN
    assert route.ok is False


def test_lark_route_sanity_rejects_local_task_shaped_recipient() -> None:
    from l3_node.capability_router import choose_capability_route
    from l3_node.mission_intent_schema import MissionIntent, MissionSlots

    intent = MissionIntent(
        task_type=MissionTaskType.LARK_MESSAGE_SEND,
        confidence=0.78,
        slots=MissionSlots(recipients=["我打开windows上原生的计算器"], message="20+70"),
        raw_text="给我打开windows上原生的计算器给我算20+70等于几",
    )

    route = choose_capability_route(intent, [{"id": "mcp:windows_lark_send_message"}])

    assert route.ok is False
    assert route.reason in {"command_prefix_not_lark_send", "recipient_looks_like_local_task"}

def test_semantic_parser_keeps_codex_question_lark_delivery_as_composite_task() -> None:
    from l3_node.capability_router import choose_capability_route
    from l3_node.semantic_slot_parser import parse_mission_intent

    utterance = (
        "\u8bf7\u4f60\u5728 codex \u91cc\u9762\u6253\u5f00\u4e00\u4e2a\u4f1a\u8bdd\u6846\uff0c"
        "\u95ee\u4ed6\u8fd9\u5468\u7684AI\u5927\u4e8b\u6709\u4ec0\u4e48\uff0c"
        "\u7136\u540e\u628a\u4ed6\u56de\u590d\u7684\u5185\u5bb9\u901a\u8fc7lark\u53d1\u9001\u7ed9vivian"
    )

    intent = parse_mission_intent(utterance)
    route = choose_capability_route(
        intent,
        [
            {"id": "mcp:windows_open_app"},
            {"id": "mcp:windows_lark_send_message"},
            {"id": "mcp:windows_codex_ask_lark_send"},
        ],
    )

    assert intent.task_type == MissionTaskType.CODEX_ASK_LARK_SEND
    assert intent.slots.app_name == "codex"
    assert intent.slots.recipients == ["vivian"]
    assert "AI" in intent.slots.feature_query
    assert route.ok is True
    assert route.tool_id == "mcp:windows_codex_ask_lark_send"
    assert route.workflow_id == "codex_ask_lark_send"
