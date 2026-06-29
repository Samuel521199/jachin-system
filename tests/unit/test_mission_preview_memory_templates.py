from l3_node.capability_router import choose_capability_route
from l3_node.mission_intent_schema import MissionTaskType
from l3_node.mission_memory_center import apply_memory_to_intent, remember_recipient_alias
from l3_node.mission_preview import build_mission_preview
from l3_node.mission_runtime import build_plan_preview
from l3_node.mission_template_library import list_mission_templates, select_mission_template
from l3_node.project_memory import remember_project
from l3_node.semantic_slot_parser import parse_mission_intent


def test_template_library_selects_codex_project_briefing_template() -> None:
    intent = parse_mission_intent("总结 Jachin 最近 3 天进展，按条列出来，发给 Vivian")
    route = choose_capability_route(intent, [{"id": "mcp:windows_codex_lark_workflow_template"}])

    template = select_mission_template(intent, route)

    assert template is not None
    assert template.id == "codex_project_briefing_to_lark"
    assert template.task_type == MissionTaskType.PROJECT_BRIEFING_DELIVERY.value
    assert "Codex" in template.apps
    assert any(row["id"] == "lark_verified_message_send" for row in list_mission_templates())


def test_memory_center_hydrates_project_and_recipient_alias(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JACHIN_OS_PROJECT_MEMORY_PATH", str(tmp_path / "project_memory.json"))
    monkeypatch.setenv("JACHIN_OS_MISSION_MEMORY_PATH", str(tmp_path / "mission_memory.json"))
    project = tmp_path / "jachin-system-main"
    project.mkdir()
    remember_project("Jachin", project)
    remember_recipient_alias("研发群", "测试备注冒烟草稿", "group")

    intent = parse_mission_intent("总结 Jachin 最近进展，发给 研发群")
    memory = apply_memory_to_intent(intent)

    assert intent.slots.project_path == str(project.resolve())
    assert intent.slots.recipients == ["测试备注冒烟草稿"]
    assert memory["project_hit"] is True
    assert memory["recipient_alias_hits"][0]["alias"] == "研发群"


def test_mission_preview_includes_template_memory_route_and_steps(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JACHIN_OS_PROJECT_MEMORY_PATH", str(tmp_path / "project_memory.json"))
    monkeypatch.setenv("JACHIN_OS_MISSION_MEMORY_PATH", str(tmp_path / "mission_memory.json"))
    project = tmp_path / "jachin-system-main"
    project.mkdir()
    remember_project("Jachin", project)

    intent = parse_mission_intent("总结 Jachin 最近 3 天进展，按条列出来，发给 Neil")
    memory = apply_memory_to_intent(intent)
    route = choose_capability_route(intent, [{"id": "mcp:windows_codex_lark_workflow_template"}])
    template = select_mission_template(intent, route)
    plan = build_plan_preview(intent, route)
    preview = build_mission_preview(
        intent=intent,
        route=route,
        plan=plan,
        template=template,
        clarification=__import__("l3_node.mission_intent_schema", fromlist=["ClarificationDecision"]).ClarificationDecision(False),
        memory_evidence=memory,
    )

    data = preview.to_dict()
    assert data["template_id"] == "codex_project_briefing_to_lark"
    assert data["workflow_id"] == "codex_project_briefing_to_lark"
    assert data["slots"]["project_path"] == str(project.resolve())
    assert data["memory"]["project_hit"] is True
    assert [step["stage"] for step in data["steps"]][:2] == ["resolve_project", "open_codex"]
