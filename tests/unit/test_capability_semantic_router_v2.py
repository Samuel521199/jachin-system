from l3_node.capability_matcher import match_task_to_capability
from l3_node.capability_semantic_registry import build_capability_registry, descriptor_for_tool
from l3_node.task_understanding_engine import infer_task_understanding
from l3_node.workflow_composer import compose_workflow


def test_capability_registry_builds_builtin_descriptors_from_tools() -> None:
    registry = build_capability_registry(
        [
            {"id": "mcp:windows_lark_send_message"},
            {"id": "mcp:windows_codex_lark_workflow_template"},
        ]
    )

    assert {item.id for item in registry} == {
        "mcp:windows_lark_send_message",
        "mcp:windows_codex_lark_workflow_template",
    }
    assert registry[0].domain.startswith("communication") or registry[1].domain.startswith("communication")


def test_registry_infers_descriptor_from_mcp_input_schema() -> None:
    desc = descriptor_for_tool(
        {
            "name": "windows_custom_file_pack",
            "description": "Find recent files in a folder and attach them to an app.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "directory_path": {"type": "string"},
                    "app_name": {"type": "string"},
                },
            },
        }
    )

    assert desc is not None
    assert desc.id == "mcp:windows_custom_file_pack"
    assert desc.domain == "os_assistant.file_ops"
    assert "find_file" in desc.actions
    assert desc.inputs == ["directory_path", "app_name"]
    assert desc.source == "tool_metadata"


def test_registry_infers_descriptor_from_openai_function_metadata() -> None:
    desc = descriptor_for_tool(
        {
            "type": "function",
            "function": {
                "name": "create_deck_from_outline",
                "description": "Create a PowerPoint slide presentation from an outline.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string"},
                        "outline": {"type": "string"},
                    },
                },
            },
        }
    )

    assert desc is not None
    assert desc.id == "mcp:create_deck_from_outline"
    assert desc.domain == "office.presentation"
    assert "create_presentation" in desc.actions
    assert desc.inputs == ["topic", "outline"]


def test_registry_uses_explicit_capability_override() -> None:
    desc = descriptor_for_tool(
        {
            "id": "mcp:custom_notify",
            "description": "Send a message through a custom channel.",
            "params": ["recipient", "message"],
            "x_jachin_capability": {
                "domain": "communication.custom",
                "actions": ["send_message"],
                "objects": ["custom_contact"],
                "risk": "external_effect",
                "workflow_id": "custom_notify_workflow",
                "task_type": "custom_notify",
                "examples": ["通知张三"],
                "evidence": ["delivery_receipt"],
            },
        }
    )

    assert desc is not None
    assert desc.domain == "communication.custom"
    assert desc.workflow_id == "custom_notify_workflow"
    assert desc.task_type == "custom_notify"
    assert desc.evidence == ["delivery_receipt"]


def test_registry_infers_descriptor_from_jpp_plugin_metadata() -> None:
    desc = descriptor_for_tool(
        {
            "id": "jpp:com.example.slides",
            "label": "Slides Maker",
            "desc": "Generate slides and save a PPT file.",
            "params": ["topic"],
            "_plugin_id": "com.example.slides",
            "_item_id": "main",
        }
    )

    assert desc is not None
    assert desc.id == "jpp:com.example.slides"
    assert desc.domain == "office.presentation"
    assert desc.metadata["plugin_id"] == "com.example.slides"


def test_task_understanding_project_briefing_delivery() -> None:
    understanding = infer_task_understanding("总结Jachin最近开发了什么新功能，使用codex总结然后发给Neil")

    assert understanding.goal == "project_briefing_delivery"
    assert understanding.intent.slots.project_name == "Jachin"
    assert understanding.intent.slots.recipients == ["Neil"]


def test_matcher_selects_codex_lark_for_project_delivery() -> None:
    match = match_task_to_capability(
        "总结 Jachin 最近开发了什么新功能，使用 Codex 总结然后发给 Neil",
        [{"id": "mcp:windows_codex_lark_workflow_template"}, {"id": "mcp:windows_lark_send_message"}],
    )

    assert match.selected is not None
    assert match.selected.id == "mcp:windows_codex_lark_workflow_template"
    assert match.route.workflow_id == "codex_project_briefing_to_lark"
    assert match.candidates


def test_workflow_composer_expands_codex_lark_chain() -> None:
    match = match_task_to_capability(
        "让 Codex 分析 Jachin 的 OS assistant workflow，然后发给测试群",
        [{"id": "mcp:windows_codex_lark_workflow_template"}, {"id": "mcp:windows_lark_send_message"}],
    )
    composition = compose_workflow(match)

    assert composition.mode == "multi_step_template"
    assert [step.stage for step in composition.steps] == [
        "resolve_project",
        "run_codex",
        "validate_summary",
        "send_lark",
        "verify_delivery",
    ]


def test_matcher_can_select_lark_message_for_notify_language() -> None:
    match = match_task_to_capability(
        "通知 Neil 一下今天测试完成",
        [{"id": "mcp:windows_lark_send_message"}],
    )

    assert match.selected is not None
    assert match.selected.id == "mcp:windows_lark_send_message"


def test_matcher_can_select_app_control() -> None:
    match = match_task_to_capability("帮我打开计算器", [{"id": "mcp:windows_open_app"}])

    assert match.selected is not None
    assert match.selected.id == "mcp:windows_open_app"


def test_matcher_can_select_system_status() -> None:
    match = match_task_to_capability("看看当前电脑 CPU 和内存状态", [{"id": "mcp:windows_system_status"}])

    assert match.selected is not None
    assert match.selected.id == "mcp:windows_system_status"


def test_matcher_can_select_dynamic_presentation_tool() -> None:
    match = match_task_to_capability("做一个项目汇报 PPT", [{"id": "mcp:create_presentation"}])

    assert match.selected is not None
    assert match.selected.id == "mcp:create_presentation"
    assert match.route.workflow_id == "office_powerpoint_create"
