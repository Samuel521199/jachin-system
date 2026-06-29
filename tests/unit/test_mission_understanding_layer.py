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
