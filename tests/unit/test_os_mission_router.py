import json

import pytest

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def isolate_os_mission_control(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JACHIN_OS_PENDING_MISSION_PATH", str(tmp_path / "pending.json"))
    monkeypatch.setenv("JACHIN_OS_MISSION_MEMORY_PATH", str(tmp_path / "mission_memory.json"))
    monkeypatch.setenv("JACHIN_OS_MISSION_CONFIRM_MODE", "never")


def test_parse_codex_lark_mission_project_name_days_recipient() -> None:
    from l3_node.os_mission_router import parse_codex_lark_mission

    mission = parse_codex_lark_mission("总结 Jachin 最近 3 天做了什么，发给 Neil")

    assert mission is not None
    assert mission.project_name == "Jachin"
    assert mission.project_path == ""
    assert mission.since_days == 3
    assert mission.recipients == ("Neil",)
    assert "latest" not in mission.feature_query.lower()


def test_parse_codex_lark_mission_project_path_multi_recipients() -> None:
    from l3_node.os_mission_router import parse_codex_lark_mission

    mission = parse_codex_lark_mission(
        r"总结 D:\Projects\jachi\jachin-system-main 项目最新进展，按条列出来，发给 Vivian 和 Samuel"
    )

    assert mission is not None
    assert mission.project_path == r"D:\Projects\jachi\jachin-system-main"
    assert mission.project_name == "jachin-system-main"
    assert mission.recipients == ("Vivian", "Samuel")
    assert "按条列输出" in mission.feature_query


def test_parse_codex_lark_mission_codex_feature_group() -> None:
    from l3_node.os_mission_router import parse_codex_lark_mission

    mission = parse_codex_lark_mission("让 Codex 分析 Jachin 的 OS assistant workflow，然后发给测试备注冒烟草稿")

    assert mission is not None
    assert mission.project_name == "Jachin"
    assert mission.recipients == ("测试备注冒烟草稿",)
    assert "OS assistant workflow" in mission.feature_query


async def test_maybe_run_codex_lark_mission_invokes_windows_workflow(monkeypatch) -> None:
    from l3_client.local_mcps.windows_uia_mcp import server as windows_uia_server
    from l3_node.os_mission_router import maybe_run_codex_lark_mission

    calls: list[dict] = []

    def fake_workflow(**kwargs) -> str:
        calls.append(kwargs)
        return json.dumps(
            {
                "task": "windows_codex_lark_workflow_template",
                "ok": True,
                "detail": "template_workflow_completed",
                "evidence": {
                    "evidence_path": "D:/evidence/run.evidence.json",
                    "evidence_panel_path": "D:/evidence/panel.html",
                    "report_path": "D:/evidence/report.md",
                },
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(windows_uia_server, "windows_codex_lark_workflow_template", fake_workflow)

    reply = await maybe_run_codex_lark_mission(
        user_input="总结 Jachin 最近 3 天做了什么，发给 Neil",
        tools=[{"id": "mcp:windows_codex_lark_workflow_template"}],
        allowed=None,
    )

    assert reply is not None
    assert "我已经完成 Jachin 的项目总结" in reply
    assert "D:/evidence/run.evidence.json" not in reply
    assert "Router Evidence:" not in reply
    assert calls[0]["project_name"] == "Jachin"
    assert calls[0]["recipients_json"] == '["Neil"]'
    assert calls[0]["since_days"] == 3
    assert calls[0]["send_summary"] is True


async def test_agent_preflight_short_circuits_to_os_mission_router(monkeypatch) -> None:
    from l3_node import os_mission_router
    from l3_node.agent_preflight import apply_inbound_preflight

    async def fake_router(**kwargs):
        return "os mission routed"

    monkeypatch.setattr(os_mission_router, "maybe_run_codex_lark_mission", fake_router)

    result = await apply_inbound_preflight(
        user_input="总结 Jachin 最近 3 天做了什么，发给 Neil",
        messages=[{"role": "user", "content": "总结 Jachin 最近 3 天做了什么，发给 Neil"}],
        prior_messages=[],
        tools=[{"id": "mcp:windows_codex_lark_workflow_template"}],
        allowed=None,
        lark_cid="",
    )

    assert result == "os mission routed"


async def test_os_mission_router_executes_lark_message(monkeypatch) -> None:
    from l3_client.local_mcps.windows_uia_mcp import server as windows_uia_server
    from l3_node.os_mission_router import maybe_run_codex_lark_mission

    calls: list[tuple[str, str]] = []

    def fake_send(recipients_json: str, message: str, out_dir: str = "", max_attempts: int = 2) -> str:
        calls.append((recipients_json, message))
        return json.dumps({"task": "windows_lark_send_message", "ok": True, "detail": "message_sent"}, ensure_ascii=False)

    monkeypatch.setattr(windows_uia_server, "windows_lark_send_message", fake_send)

    reply = await maybe_run_codex_lark_mission(
        user_input="给 Vivian 发 你好，我已经在测试",
        tools=[{"id": "mcp:windows_lark_send_message"}],
    )

    assert reply is not None
    assert "我已经把消息发送给 Vivian" in reply
    assert "识别意图:" not in reply
    assert "Router Evidence:" not in reply
    assert calls == [('["Vivian"]', "你好，我已经在测试")]


async def test_os_mission_router_executes_app_control(monkeypatch) -> None:
    from l3_client.local_mcps.windows_uia_mcp import server as windows_uia_server
    from l3_node.os_mission_router import maybe_run_codex_lark_mission

    calls: list[str] = []

    def fake_open(app_name: str, args_json: str = "[]", out_dir: str = "") -> str:
        calls.append(app_name)
        return json.dumps({"task": "windows_open_app", "ok": True, "detail": "app_ready"}, ensure_ascii=False)

    monkeypatch.setattr(windows_uia_server, "windows_open_app", fake_open)

    reply = await maybe_run_codex_lark_mission(
        user_input="打开 Lark",
        tools=[{"id": "mcp:windows_open_app"}],
    )

    assert reply is not None
    assert "我已经打开或切换到 lark" in reply
    assert "识别意图:" not in reply
    assert "Router Evidence:" not in reply
    assert calls == ["lark"]


async def test_agent_preflight_new_os_task_clears_stale_pending(monkeypatch) -> None:
    from l3_client.local_mcps.windows_uia_mcp import server as windows_uia_server
    from l3_node.agent_preflight import apply_inbound_preflight
    from l3_node.mission_control_center import load_pending_mission, save_pending_mission

    save_pending_mission(
        {
            "intent": {
                "task_type": "lark_message_send",
                "confidence": 0.62,
                "slots": {"recipients": ["Vivian"], "message": ""},
                "missing_slots": ["message"],
                "risk_level": "low",
                "reasoning": ["test stale pending"],
                "raw_text": "给 Vivian 发消息",
            },
            "route": {
                "ok": False,
                "tool_id": "mcp:windows_lark_send_message",
                "workflow_id": "windows_lark_message_send",
                "reason": "missing_message",
                "required_slots": ["recipients", "message"],
                "missing_slots": ["message"],
            },
        }
    )
    calls: list[str] = []

    def fake_open(app_name: str, args_json: str = "[]", out_dir: str = "") -> str:
        calls.append(app_name)
        return json.dumps({"task": "windows_open_app", "ok": True, "detail": "app_ready"}, ensure_ascii=False)

    monkeypatch.setattr(windows_uia_server, "windows_open_app", fake_open)

    reply = await apply_inbound_preflight(
        user_input="打开 Lark",
        messages=[{"role": "user", "content": "打开 Lark"}],
        prior_messages=[],
        tools=[{"id": "mcp:windows_open_app"}],
        allowed=None,
        lark_cid="voice-test",
        implicit_attribution={"channel": "websocket_terminal"},
    )

    assert reply is not None
    assert "没补全" not in reply
    assert "缺少的内容" not in reply
    assert calls == ["lark"]
    assert load_pending_mission() is None


async def test_os_mission_router_remembers_project_then_hydrates_path(tmp_path, monkeypatch) -> None:
    from l3_client.local_mcps.windows_uia_mcp import server as windows_uia_server
    from l3_node.os_mission_router import maybe_run_codex_lark_mission

    monkeypatch.setenv("JACHIN_OS_PROJECT_MEMORY_PATH", str(tmp_path / "memory.json"))
    project = tmp_path / "jachin-system-main"
    project.mkdir()
    calls: list[dict] = []

    def fake_workflow(**kwargs) -> str:
        calls.append(kwargs)
        return json.dumps({"task": "windows_codex_lark_workflow_template", "ok": True, "detail": "template_workflow_completed"}, ensure_ascii=False)

    monkeypatch.setattr(windows_uia_server, "windows_codex_lark_workflow_template", fake_workflow)

    remembered = await maybe_run_codex_lark_mission(
        user_input=f"Jachin = {project}",
        tools=[],
    )
    assert remembered is not None
    assert "我已经记住 Jachin 的本地路径" in remembered
    assert "project_memory_update" not in remembered

    reply = await maybe_run_codex_lark_mission(
        user_input="总结 Jachin 最近进展发给 Vivian",
        tools=[{"id": "mcp:windows_codex_lark_workflow_template"}],
    )

    assert reply is not None
    assert calls[0]["project_name"] == "Jachin"
    assert calls[0]["project_path"] == str(project.resolve())
    assert calls[0]["recipients_json"] == '["Vivian"]'


async def test_os_mission_router_uses_single_remembered_project_for_fuzzy_briefing(tmp_path, monkeypatch) -> None:
    from l3_client.local_mcps.windows_uia_mcp import server as windows_uia_server
    from l3_node.project_memory import remember_project
    from l3_node.os_mission_router import maybe_run_codex_lark_mission

    monkeypatch.setenv("JACHIN_OS_PROJECT_MEMORY_PATH", str(tmp_path / "memory.json"))
    project = tmp_path / "only-project"
    project.mkdir()
    remember_project("Jachin", project)
    calls: list[dict] = []

    def fake_workflow(**kwargs) -> str:
        calls.append(kwargs)
        return json.dumps({"task": "windows_codex_lark_workflow_template", "ok": True, "detail": "template_workflow_completed"}, ensure_ascii=False)

    monkeypatch.setattr(windows_uia_server, "windows_codex_lark_workflow_template", fake_workflow)

    reply = await maybe_run_codex_lark_mission(
        user_input="把最近改动整理几条给 Vivian",
        tools=[{"id": "mcp:windows_codex_lark_workflow_template"}],
    )

    assert reply is not None
    assert calls[0]["project_name"] == "Jachin"
    assert calls[0]["project_path"] == str(project.resolve())
    assert "按条列输出" in calls[0]["feature_query"]


async def test_os_mission_router_asks_one_question_when_group_name_is_missing(tmp_path, monkeypatch) -> None:
    from l3_node.project_memory import remember_project
    from l3_node.os_mission_router import maybe_run_codex_lark_mission

    monkeypatch.setenv("JACHIN_OS_PROJECT_MEMORY_PATH", str(tmp_path / "memory.json"))
    project = tmp_path / "jachin-system-main"
    project.mkdir()
    remember_project("Jachin", project)

    reply = await maybe_run_codex_lark_mission(
        user_input="让 Codex 看一下 OS 助手这块，发群里",
        tools=[{"id": "mcp:windows_codex_lark_workflow_template"}],
    )

    assert reply is not None
    assert "要发送给谁" in reply
    assert "Router Evidence:" not in reply


async def test_os_mission_router_preview_patch_confirm_flow(tmp_path, monkeypatch) -> None:
    from l3_client.local_mcps.windows_uia_mcp import server as windows_uia_server
    from l3_node.project_memory import remember_project
    from l3_node.os_mission_router import maybe_run_codex_lark_mission

    monkeypatch.setenv("JACHIN_OS_MISSION_CONFIRM_MODE", "external_effects")
    monkeypatch.setenv("JACHIN_OS_PROJECT_MEMORY_PATH", str(tmp_path / "memory.json"))
    project = tmp_path / "jachin-system-main"
    project.mkdir()
    remember_project("Jachin", project)
    calls: list[dict] = []

    def fake_workflow(**kwargs) -> str:
        calls.append(kwargs)
        return json.dumps({"task": "windows_codex_lark_workflow_template", "ok": True, "detail": "template_workflow_completed"}, ensure_ascii=False)

    monkeypatch.setattr(windows_uia_server, "windows_codex_lark_workflow_template", fake_workflow)

    preview = await maybe_run_codex_lark_mission(
        user_input="总结 Jachin 最近 3 天进展，按条列出来，发给 Neil",
        tools=[{"id": "mcp:windows_codex_lark_workflow_template"}],
    )

    assert preview is not None
    assert "我先确认一下" in preview
    assert "确认后我再执行" in preview
    assert "Task Preview" not in preview
    assert "Router Evidence:" not in preview
    assert calls == []

    patched = await maybe_run_codex_lark_mission(
        user_input="改发给 Vivian，时间范围改成 7 天",
        tools=[{"id": "mcp:windows_codex_lark_workflow_template"}],
    )

    assert patched is not None
    assert "确认" in patched
    assert "Task Preview" not in patched
    assert "Router Evidence:" not in patched
    assert calls == []

    done = await maybe_run_codex_lark_mission(
        user_input="确认执行",
        tools=[{"id": "mcp:windows_codex_lark_workflow_template"}],
    )

    assert done is not None
    assert calls[0]["recipients_json"] == '["Vivian"]'
    assert calls[0]["since_days"] == 7


async def test_os_mission_router_preview_cancel_flow(tmp_path, monkeypatch) -> None:
    from l3_client.local_mcps.windows_uia_mcp import server as windows_uia_server
    from l3_node.project_memory import remember_project
    from l3_node.os_mission_router import maybe_run_codex_lark_mission

    monkeypatch.setenv("JACHIN_OS_MISSION_CONFIRM_MODE", "external_effects")
    monkeypatch.setenv("JACHIN_OS_PROJECT_MEMORY_PATH", str(tmp_path / "memory.json"))
    project = tmp_path / "jachin-system-main"
    project.mkdir()
    remember_project("Jachin", project)
    calls: list[dict] = []

    def fake_workflow(**kwargs) -> str:
        calls.append(kwargs)
        return json.dumps({"task": "windows_codex_lark_workflow_template", "ok": True, "detail": "template_workflow_completed"}, ensure_ascii=False)

    monkeypatch.setattr(windows_uia_server, "windows_codex_lark_workflow_template", fake_workflow)

    preview = await maybe_run_codex_lark_mission(
        user_input="总结 Jachin 最近 3 天进展，发给 Neil",
        tools=[{"id": "mcp:windows_codex_lark_workflow_template"}],
    )
    assert preview is not None

    cancelled = await maybe_run_codex_lark_mission(
        user_input="取消",
        tools=[{"id": "mcp:windows_codex_lark_workflow_template"}],
    )

    assert cancelled is not None
    assert "已取消" in cancelled
    assert calls == []
