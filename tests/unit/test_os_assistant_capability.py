def test_os_assistant_domain_injects_for_windows_uia_tools() -> None:
    from l3_node.capability_catalog import build_capability_prompt_inject_for_tools

    prompt = build_capability_prompt_inject_for_tools(
        [
            {
                "id": "mcp:windows_calculator_calculate",
                "label": "mcp:windows_calculator_calculate",
                "desc": "Windows Calculator",
            }
        ]
    )

    assert "OS Assistant" in prompt
    assert "windows_calculator_calculate" in prompt
    assert "66*8+9-4" in prompt
    assert "windows_codex_lark_workflow_template" in prompt
    assert "Jachin must not summarize the code itself" in prompt


def test_l3_local_mcp_tools_include_windows_uia_os_assistant() -> None:
    from l3_node.primitives.mcp.registry import l3_local_mcp_raw_names

    raw_names = l3_local_mcp_raw_names()

    assert "uia_snapshot" in raw_names
    assert "uia_click" in raw_names
    assert "uia_set_text" in raw_names
    assert "windows_calculator_calculate" in raw_names
    assert "windows_notepad_save_text" in raw_names
    assert "windows_open_app" in raw_names
    assert "windows_lark_send_message" in raw_names
    assert "windows_lark_read_recent_messages" in raw_names
    assert "windows_lark_read_history" in raw_names
    assert "windows_lark_open_bitable" in raw_names
    assert "windows_lark_bitable_add_record" in raw_names
    assert "windows_lark_bitable_ai_paste_records" in raw_names
    assert "windows_lark_bitable_cdp_ai_paste_records" in raw_names
    assert "lark_bitable_list_fields" in raw_names
    assert "lark_bitable_get_records" in raw_names
    assert "lark_bitable_create_records" in raw_names
    assert "windows_active_window" in raw_names
    assert "windows_window_list" in raw_names
    assert "windows_window_switch" in raw_names
    assert "windows_disk_snapshot" in raw_names
    assert "windows_network_check" in raw_names
    assert "windows_power_status" in raw_names
    assert "windows_process_snapshot" in raw_names
    assert "windows_system_status" in raw_names
    assert "windows_recent_files" in raw_names
    assert "windows_folder_create" in raw_names
    assert "windows_file_write_text" in raw_names
    assert "windows_workspace_report" in raw_names
    assert "windows_evidence_panel" in raw_names
    assert "windows_project_remember" in raw_names
    assert "windows_project_latest_briefing" in raw_names
    assert "windows_codex_project_briefing_to_lark" in raw_names
    assert "windows_codex_lark_workflow_template" in raw_names
    assert "windows_codex_lark_standard_demo" in raw_names
    assert "windows_app_switch_matrix" in raw_names
    assert "windows_daily_office_briefing" in raw_names
    assert "windows_file_bridge_to_app" in raw_names
    assert "windows_os_mission_execute" in raw_names
    assert "windows_file_find" in raw_names
    assert "windows_file_copy" in raw_names
    assert "windows_file_move" in raw_names
    assert "windows_file_rename" in raw_names
    assert "windows_file_delete_with_confirm" in raw_names
    assert "windows_file_open" in raw_names
    assert "windows_file_reveal_in_explorer" in raw_names
    assert "windows_file_attach_to_app" in raw_names
    assert "windows_folder_summarize" in raw_names


def test_business_mcp_tools_hidden_from_default_os_assistant_pool() -> None:
    from l3_node.primitives.mcp.registry import l3_local_mcp_raw_names

    raw_names = l3_local_mcp_raw_names()

    assert "windows_calculator_calculate" in raw_names
    assert "atom_lark_notifier" not in raw_names
    assert "add_automated_recruitment_task" not in raw_names


async def test_windows_calculator_mcp_invokes_local_handler(monkeypatch) -> None:
    from l3_client.local_mcps.windows_uia_mcp import server as windows_uia_server
    from l3_node.primitives.mcp.registry import MCPToolRegistry

    calls: list[tuple[str, str, str]] = []

    def fake_calculator(expression: str, expected: str = "", out_dir: str = "") -> str:
        calls.append((expression, expected, out_dir))
        return '{"task":"calculator","ok":true,"evidence":{"result":"100"}}'

    monkeypatch.setattr(windows_uia_server, "windows_calculator_calculate", fake_calculator)

    registry = MCPToolRegistry()
    result = await registry._invoke_impl(
        "mcp:windows_calculator_calculate",
        '{"expression":"91+9","expected":"100"}',
        allow_l2_delegate=False,
    )

    assert calls == [("91+9", "100", "")]
    assert '"ok":true' in result


async def test_windows_open_app_mcp_invokes_local_handler(monkeypatch) -> None:
    from l3_client.local_mcps.windows_uia_mcp import server as windows_uia_server
    from l3_node.primitives.mcp.registry import MCPToolRegistry

    calls: list[tuple[str, str, str]] = []

    def fake_open_app(app_name: str, args_json: str = "[]", out_dir: str = "") -> str:
        calls.append((app_name, args_json, out_dir))
        return '{"task":"open_app","ok":true,"evidence":{"active_title":"Lark"}}'

    monkeypatch.setattr(windows_uia_server, "windows_open_app", fake_open_app)

    registry = MCPToolRegistry()
    result = await registry._invoke_impl(
        "mcp:windows_open_app",
        '{"app_name":"lark","args_json":"[]"}',
        allow_l2_delegate=False,
    )

    assert calls == [("lark", "[]", "")]
    assert '"ok":true' in result


async def test_windows_lark_send_message_mcp_invokes_local_handler(monkeypatch) -> None:
    from l3_client.local_mcps.windows_uia_mcp import server as windows_uia_server
    from l3_node.primitives.mcp.registry import MCPToolRegistry

    calls: list[tuple[str, str, str, int]] = []

    def fake_send(recipients_json: str, message: str, out_dir: str = "", max_attempts: int = 2) -> str:
        calls.append((recipients_json, message, out_dir, max_attempts))
        return '{"task":"lark_send_message","ok":true,"evidence":{"recipients":["Vivian","Samuel"]}}'

    monkeypatch.setattr(windows_uia_server, "windows_lark_send_message", fake_send)

    registry = MCPToolRegistry()
    result = await registry._invoke_impl(
        "mcp:windows_lark_send_message",
        '{"recipients_json":"[\\"Vivian\\",\\"Samuel\\"]","message":"hello"}',
        allow_l2_delegate=False,
    )

    assert calls == [('[\"Vivian\",\"Samuel\"]', "hello", "", 2)]
    assert '"ok":true' in result


async def test_windows_lark_read_recent_messages_mcp_invokes_local_handler(monkeypatch) -> None:
    from l3_client.local_mcps.windows_uia_mcp import server as windows_uia_server
    from l3_node.primitives.mcp.registry import MCPToolRegistry

    calls: list[tuple[str, int, int, str]] = []

    def fake_read(target: str, pages: int = 3, scroll_clicks: int = 5, out_dir: str = "") -> str:
        calls.append((target, pages, scroll_clicks, out_dir))
        return '{"task":"lark_read_recent_messages","ok":true,"evidence":{"deduped_lines":["hello"]}}'

    monkeypatch.setattr(windows_uia_server, "windows_lark_read_recent_messages", fake_read)

    registry = MCPToolRegistry()
    result = await registry._invoke_impl(
        "mcp:windows_lark_read_recent_messages",
        '{"target":"Vivian","pages":2,"scroll_clicks":4}',
        allow_l2_delegate=False,
    )

    assert calls == [("Vivian", 2, 4, "")]
    assert '"ok":true' in result


async def test_windows_lark_read_history_mcp_invokes_local_handler(monkeypatch) -> None:
    from l3_client.local_mcps.windows_uia_mcp import server as windows_uia_server
    from l3_node.primitives.mcp.registry import MCPToolRegistry

    calls: list[tuple[str, int, int, int, str]] = []

    def fake_history(target: str, days: int = 7, max_pages: int = 18, scroll_clicks: int = 6, out_dir: str = "") -> str:
        calls.append((target, days, max_pages, scroll_clicks, out_dir))
        return '{"task":"lark_read_history","ok":true,"evidence":{"requested_days":7}}'

    monkeypatch.setattr(windows_uia_server, "windows_lark_read_history", fake_history)

    registry = MCPToolRegistry()
    result = await registry._invoke_impl(
        "mcp:windows_lark_read_history",
        '{"target":"Vivian","days":7,"max_pages":12,"scroll_clicks":4}',
        allow_l2_delegate=False,
    )

    assert calls == [("Vivian", 7, 12, 4, "")]
    assert '"ok":true' in result


async def test_windows_lark_open_bitable_mcp_invokes_local_handler(monkeypatch) -> None:
    from l3_client.local_mcps.windows_uia_mcp import server as windows_uia_server
    from l3_node.primitives.mcp.registry import MCPToolRegistry

    calls: list[tuple[str, str, int]] = []

    def fake_open_bitable(table_name: str, out_dir: str = "", max_attempts: int = 2) -> str:
        calls.append((table_name, out_dir, max_attempts))
        return '{"task":"lark_open_bitable","ok":true,"evidence":{"table_name":"P28 AI项目进度"}}'

    monkeypatch.setattr(windows_uia_server, "windows_lark_open_bitable", fake_open_bitable)

    registry = MCPToolRegistry()
    result = await registry._invoke_impl(
        "mcp:windows_lark_open_bitable",
        '{"table_name":"P28 AI项目进度","max_attempts":3}',
        allow_l2_delegate=False,
    )

    assert calls == [("P28 AI项目进度", "", 3)]
    assert '"ok":true' in result


async def test_windows_lark_bitable_add_record_mcp_invokes_local_handler(monkeypatch) -> None:
    from l3_client.local_mcps.windows_uia_mcp import server as windows_uia_server
    from l3_node.primitives.mcp.registry import MCPToolRegistry

    calls: list[tuple[str, str, bool, bool, str, int]] = []

    def fake_add_record(
        table_name: str,
        fields_json: str,
        confirm: bool = False,
        allow_dangerous: bool = False,
        out_dir: str = "",
        max_attempts: int = 2,
    ) -> str:
        calls.append((table_name, fields_json, confirm, allow_dangerous, out_dir, max_attempts))
        return '{"task":"lark_bitable_add_record","ok":true,"evidence":{"verified_fields":["任务"]}}'

    monkeypatch.setattr(windows_uia_server, "windows_lark_bitable_add_record", fake_add_record)

    registry = MCPToolRegistry()
    result = await registry._invoke_impl(
        "mcp:windows_lark_bitable_add_record",
        '{"table_name":"P28 AI项目进度","fields_json":"{\\"任务\\":\\"测试 Jachin 写入\\"}","confirm":true,"max_attempts":3}',
        allow_l2_delegate=False,
    )

    assert calls == [("P28 AI项目进度", '{"任务":"测试 Jachin 写入"}', True, False, "", 3)]
    assert '"ok":true' in result


async def test_windows_lark_bitable_ai_paste_records_mcp_invokes_local_handler(monkeypatch) -> None:
    from l3_client.local_mcps.windows_uia_mcp import server as windows_uia_server
    from l3_node.primitives.mcp.registry import MCPToolRegistry

    calls: list[tuple[str, str, str, bool, bool, str, int]] = []

    def fake_ai_paste(
        table_name: str,
        records_text: str,
        target_group: str = "2026/6/22",
        confirm: bool = False,
        allow_dangerous: bool = False,
        out_dir: str = "",
        max_attempts: int = 2,
    ) -> str:
        calls.append((table_name, records_text, target_group, confirm, allow_dangerous, out_dir, max_attempts))
        return '{"task":"lark_bitable_ai_paste_records","ok":true,"evidence":{"target_group":"2026/6/22"}}'

    monkeypatch.setattr(windows_uia_server, "windows_lark_bitable_ai_paste_records", fake_ai_paste)

    registry = MCPToolRegistry()
    result = await registry._invoke_impl(
        "mcp:windows_lark_bitable_ai_paste_records",
        '{"table_name":"P28 AI项目进度","records_text":"任务 A\\n任务 B","target_group":"2026/6/22","confirm":true,"max_attempts":3}',
        allow_l2_delegate=False,
    )

    assert calls == [("P28 AI项目进度", "任务 A\n任务 B", "2026/6/22", True, False, "", 3)]
    assert '"ok":true' in result


async def test_lark_bitable_create_records_mcp_invokes_api_handler(monkeypatch) -> None:
    from l3_node.primitives.mcp.mcp_tools import lark_bitable_ops
    from l3_node.primitives.mcp.registry import MCPToolRegistry

    calls: list[tuple[str, str, str, str, bool, bool]] = []

    def fake_create_records(
        records_json: str,
        app_token: str = "",
        table_id: str = "",
        table_name: str = "",
        bitable_url: str = "",
        field_aliases_json: str = "{}",
        dry_run: bool = False,
        confirm: bool = False,
        allow_dangerous: bool = False,
        app_id: str = "",
        app_secret: str = "",
        api_base: str = "",
    ) -> dict:
        calls.append((records_json, app_token, table_id, table_name, dry_run, confirm))
        return {"ok": True, "record_ids": ["rec_mock"], "count": 1}

    monkeypatch.setattr(lark_bitable_ops, "lark_bitable_create_records", fake_create_records)

    registry = MCPToolRegistry()
    result = await registry._invoke_impl(
        "mcp:lark_bitable_create_records",
        '{"records":[{"任务":"测试 API 写入"}],"app_token":"app1","table_id":"tbl1","dry_run":true,"confirm":true}',
        allow_l2_delegate=False,
    )

    assert calls == [('[{"任务": "测试 API 写入"}]', "app1", "tbl1", "", True, True)]
    assert '"record_ids": ["rec_mock"]' in result


async def test_windows_lark_bitable_cdp_ai_paste_records_mcp_invokes_local_handler(monkeypatch) -> None:
    from l3_client.local_mcps.windows_uia_mcp import server as windows_uia_server
    from l3_node.primitives.mcp.registry import MCPToolRegistry

    calls: list[tuple[str, str, str, str, str, bool, bool, bool, bool, str]] = []

    def fake_cdp_ai_paste(
        table_name: str = "",
        bitable_url: str = "",
        records_text: str = "",
        target_group: str = "2026/6/22",
        cdp_url: str = "http://127.0.0.1:9222",
        launch_if_missing: bool = True,
        submit: bool = False,
        confirm: bool = False,
        allow_dangerous: bool = False,
        out_dir: str = "",
    ) -> str:
        calls.append((table_name, bitable_url, records_text, target_group, cdp_url, launch_if_missing, submit, confirm, allow_dangerous, out_dir))
        return '{"task":"lark_bitable_cdp_ai_paste_records","ok":true,"evidence":{"payload_visible":true}}'

    monkeypatch.setattr(windows_uia_server, "windows_lark_bitable_cdp_ai_paste_records", fake_cdp_ai_paste)

    registry = MCPToolRegistry()
    result = await registry._invoke_impl(
        "mcp:windows_lark_bitable_cdp_ai_paste_records",
        '{"table_name":"P28 AI项目进度","bitable_url":"https://example.larksuite.com/wiki/x?table=tbl1","records_text":"任务 A","target_group":"2026/6/22","cdp_url":"http://127.0.0.1:9222","submit":false,"confirm":true}',
        allow_l2_delegate=False,
    )

    assert calls == [(
        "P28 AI项目进度",
        "https://example.larksuite.com/wiki/x?table=tbl1",
        "任务 A",
        "2026/6/22",
        "http://127.0.0.1:9222",
        True,
        False,
        True,
        False,
        "",
    )]
    assert '"payload_visible":true' in result


def test_lark_bitable_create_records_requires_confirmation() -> None:
    from l3_node.primitives.mcp.mcp_tools.lark_bitable_ops import lark_bitable_create_records

    result = lark_bitable_create_records(
        records_json='[{"任务":"测试 API 写入"}]',
        app_token="app1",
        table_id="tbl1",
        confirm=False,
    )

    assert result["ok"] is False
    assert result["error"] == "confirmation_required"
    assert result["confirmation_required"] is True


async def test_windows_file_find_mcp_invokes_local_handler(monkeypatch) -> None:
    from l3_client.local_mcps.windows_uia_mcp import server as windows_uia_server
    from l3_node.primitives.mcp.registry import MCPToolRegistry

    calls: list[tuple[str, str, int, bool, str]] = []

    def fake_find(root: str, pattern: str = "*", max_results: int = 100, include_dirs: bool = True, out_dir: str = "") -> str:
        calls.append((root, pattern, max_results, include_dirs, out_dir))
        return '{"task":"file_find","ok":true,"evidence":{"count":1}}'

    monkeypatch.setattr(windows_uia_server, "windows_file_find", fake_find)

    registry = MCPToolRegistry()
    result = await registry._invoke_impl(
        "mcp:windows_file_find",
        '{"root":"C:/tmp","pattern":"*.txt","max_results":5,"include_dirs":false}',
        allow_l2_delegate=False,
    )

    assert calls == [("C:/tmp", "*.txt", 5, False, "")]
    assert '"ok":true' in result


async def test_windows_system_status_mcp_invokes_local_handler(monkeypatch) -> None:
    from l3_client.local_mcps.windows_uia_mcp import server as windows_uia_server
    from l3_node.primitives.mcp.registry import MCPToolRegistry

    calls: list[tuple[str, str]] = []

    def fake_status(network_host: str = "www.baidu.com", out_dir: str = "") -> str:
        calls.append((network_host, out_dir))
        return '{"task":"windows_system_status","ok":true,"evidence":{"network":{"host":"example.com"}}}'

    monkeypatch.setattr(windows_uia_server, "windows_system_status", fake_status)

    registry = MCPToolRegistry()
    result = await registry._invoke_impl(
        "mcp:windows_system_status",
        '{"network_host":"example.com"}',
        allow_l2_delegate=False,
    )

    assert calls == [("example.com", "")]
    assert '"ok":true' in result


async def test_windows_workspace_report_mcp_invokes_local_handler(monkeypatch) -> None:
    from l3_client.local_mcps.windows_uia_mcp import server as windows_uia_server
    from l3_node.primitives.mcp.registry import MCPToolRegistry

    calls: list[tuple[str, int, bool, str]] = []

    def fake_report(output_path: str = "", since_days: int = 1, open_folder: bool = False, out_dir: str = "") -> str:
        calls.append((output_path, since_days, open_folder, out_dir))
        return '{"task":"windows_workspace_report","ok":true,"evidence":{"report":{"path":"report.md"}}}'

    monkeypatch.setattr(windows_uia_server, "windows_workspace_report", fake_report)

    registry = MCPToolRegistry()
    result = await registry._invoke_impl(
        "mcp:windows_workspace_report",
        '{"output_path":"C:/tmp/report.md","since_days":2,"open_folder":true}',
        allow_l2_delegate=False,
    )

    assert calls == [("C:/tmp/report.md", 2, True, "")]
    assert '"report.md"' in result


async def test_windows_evidence_panel_mcp_invokes_local_handler(monkeypatch) -> None:
    from l3_client.local_mcps.windows_uia_mcp import server as windows_uia_server
    from l3_node.primitives.mcp.registry import MCPToolRegistry

    calls: list[tuple[str, str, bool, str]] = []

    def fake_panel(evidence_path: str, title: str = "", open_panel: bool = False, out_dir: str = "") -> str:
        calls.append((evidence_path, title, open_panel, out_dir))
        return '{"task":"windows_evidence_panel","ok":true,"evidence":{"evidence_panel_path":"panel.html"}}'

    monkeypatch.setattr(windows_uia_server, "windows_evidence_panel", fake_panel)

    registry = MCPToolRegistry()
    result = await registry._invoke_impl(
        "mcp:windows_evidence_panel",
        '{"evidence_path":"C:/tmp/run.evidence.json","title":"Demo","open_panel":true}',
        allow_l2_delegate=False,
    )

    assert calls == [("C:/tmp/run.evidence.json", "Demo", True, "")]
    assert '"panel.html"' in result


async def test_windows_daily_office_briefing_mcp_invokes_local_handler(monkeypatch) -> None:
    from l3_client.local_mcps.windows_uia_mcp import server as windows_uia_server
    from l3_node.primitives.mcp.registry import MCPToolRegistry

    calls: list[tuple[str, str, int, bool, bool, bool, int, str]] = []

    def fake_briefing(
        recipients_json: str = "[]",
        paths_json: str = "",
        since_days: int = 1,
        send_summary: bool = False,
        open_report: bool = True,
        reveal_key_file: bool = True,
        max_files: int = 60,
        out_dir: str = "",
    ) -> str:
        calls.append((recipients_json, paths_json, since_days, send_summary, open_report, reveal_key_file, max_files, out_dir))
        return '{"task":"windows_daily_office_briefing","ok":true,"evidence":{"report_path":"brief.md"}}'

    monkeypatch.setattr(windows_uia_server, "windows_daily_office_briefing", fake_briefing)

    registry = MCPToolRegistry()
    result = await registry._invoke_impl(
        "mcp:windows_daily_office_briefing",
        '{"recipients":["Vivian"],"paths":["C:/work"],"since_days":1,"send_summary":true,"open_report":false,"reveal_key_file":false,"max_files":9}',
        allow_l2_delegate=False,
    )

    assert calls == [("[\"Vivian\"]", "[\"C:/work\"]", 1, True, False, False, 9, "")]
    assert '"brief.md"' in result


async def test_windows_project_latest_briefing_mcp_invokes_local_handler(monkeypatch) -> None:
    from l3_client.local_mcps.windows_uia_mcp import server as windows_uia_server
    from l3_node.primitives.mcp.registry import MCPToolRegistry

    calls: list[tuple[str, str, str, str, int, bool, bool, bool, bool, int, str]] = []

    def fake_project_brief(
        project_name: str,
        project_path: str = "",
        feature_query: str = "",
        recipients_json: str = "[]",
        since_days: int = 3,
        send_summary: bool = False,
        open_report: bool = True,
        use_qwen: bool = True,
        remember: bool = True,
        max_files: int = 80,
        out_dir: str = "",
    ) -> str:
        calls.append((project_name, project_path, feature_query, recipients_json, since_days, send_summary, open_report, use_qwen, remember, max_files, out_dir))
        return '{"task":"windows_project_latest_briefing","ok":true,"evidence":{"report_path":"project.md"}}'

    monkeypatch.setattr(windows_uia_server, "windows_project_latest_briefing", fake_project_brief)

    registry = MCPToolRegistry()
    result = await registry._invoke_impl(
        "mcp:windows_project_latest_briefing",
        '{"project_name":"Jachin","project_path":"D:/Projects/jachi/jachin-system-main","feature_query":"OS","recipients":["Vivian"],"send_summary":true,"open_report":false,"use_qwen":false,"remember":true,"max_files":12}',
        allow_l2_delegate=False,
    )

    assert calls == [("Jachin", "D:/Projects/jachi/jachin-system-main", "OS", "[\"Vivian\"]", 3, True, False, False, True, 12, "")]
    assert '"project.md"' in result


def test_windows_project_latest_briefing_delegates_lark_send_to_codex(monkeypatch) -> None:
    from l3_client.local_mcps.windows_uia_mcp import server as windows_uia_server
    from l3_client.local_mcps.windows_uia_mcp.os_tasks import TaskResult

    calls: list[dict] = []

    class FakeAutomation:
        def codex_project_briefing_to_lark(self, **kwargs):
            calls.append({"method": "codex_project_briefing_to_lark", **kwargs})
            return TaskResult("windows_codex_project_briefing_to_lark", True, "codex_briefing_sent", {"ok": True})

        def project_latest_briefing(self, **kwargs):
            calls.append({"method": "project_latest_briefing", **kwargs})
            return TaskResult("windows_project_latest_briefing", True, "project_briefing_ready", {"ok": True})

    monkeypatch.setattr(windows_uia_server, "_os_auto", lambda out_dir="": FakeAutomation())

    result = windows_uia_server.windows_project_latest_briefing(
        project_name="Jachin",
        project_path="D:/Projects/jachi/jachin-system-main",
        feature_query="OS assistant Codex Lark workflow",
        recipients_json='["Neil"]',
        send_summary=False,
    )

    assert calls[0]["method"] == "codex_project_briefing_to_lark"
    assert calls[0]["recipients"] == ["Neil"]
    assert calls[0]["send_summary"] is True
    assert "windows_codex_project_briefing_to_lark" in result


async def test_windows_codex_project_briefing_to_lark_mcp_invokes_local_handler(monkeypatch) -> None:
    from l3_client.local_mcps.windows_uia_mcp import server as windows_uia_server
    from l3_node.primitives.mcp.registry import MCPToolRegistry

    calls: list[tuple[str, str, str, str, int, int, bool, bool, str]] = []

    def fake_codex(
        project_name: str,
        project_path: str = "",
        feature_query: str = "",
        recipients_json: str = "[]",
        since_days: int = 3,
        wait_seconds: int = 90,
        send_summary: bool = False,
        remember: bool = True,
        out_dir: str = "",
    ) -> str:
        calls.append((project_name, project_path, feature_query, recipients_json, since_days, wait_seconds, send_summary, remember, out_dir))
        return '{"task":"windows_codex_project_briefing_to_lark","ok":true,"detail":"codex_briefing_sent"}'

    monkeypatch.setattr(windows_uia_server, "windows_codex_project_briefing_to_lark", fake_codex)

    registry = MCPToolRegistry()
    result = await registry._invoke_impl(
        "mcp:windows_codex_project_briefing_to_lark",
        '{"project_name":"Jachin","project_path":"D:/Projects/jachi/jachin-system-main","feature_query":"Codex","recipients":["Vivian","Samuel"],"since_days":2,"wait_seconds":30,"send_summary":true,"remember":true}',
        allow_l2_delegate=False,
    )

    assert calls == [("Jachin", "D:/Projects/jachi/jachin-system-main", "Codex", "[\"Vivian\", \"Samuel\"]", 2, 30, True, True, "")]
    assert '"codex_briefing_sent"' in result


async def test_windows_codex_lark_workflow_template_mcp_invokes_local_handler(monkeypatch) -> None:
    from l3_client.local_mcps.windows_uia_mcp import server as windows_uia_server
    from l3_node.primitives.mcp.registry import MCPToolRegistry

    calls: list[tuple[str, str, str, str, str, str, int, int, bool, bool, str]] = []

    def fake_template(
        project_name: str = "",
        project_path: str = "",
        directory_path: str = "",
        feature_query: str = "",
        bug_query: str = "",
        recipients_json: str = "[]",
        since_days: int = 3,
        wait_seconds: int = 90,
        send_summary: bool = False,
        remember: bool = True,
        out_dir: str = "",
    ) -> str:
        calls.append((project_name, project_path, directory_path, feature_query, bug_query, recipients_json, since_days, wait_seconds, send_summary, remember, out_dir))
        return '{"task":"windows_codex_lark_workflow_template","ok":true,"evidence":{"evidence_panel_path":"panel.html"}}'

    monkeypatch.setattr(windows_uia_server, "windows_codex_lark_workflow_template", fake_template)

    registry = MCPToolRegistry()
    result = await registry._invoke_impl(
        "mcp:windows_codex_lark_workflow_template",
        '{"project_name":"Jachin","project_path":"D:/Projects/jachi/jachin-system-main","bug_query":"login fails","recipients":["Vivian","测试备注冒烟草稿"],"send_summary":true,"wait_seconds":45}',
        allow_l2_delegate=False,
    )

    assert calls == [(
        "Jachin",
        "D:/Projects/jachi/jachin-system-main",
        "",
        "",
        "login fails",
        "[\"Vivian\", \"测试备注冒烟草稿\"]",
        3,
        45,
        True,
        True,
        "",
    )]
    assert '"panel.html"' in result


async def test_windows_codex_lark_standard_demo_mcp_invokes_local_handler(monkeypatch) -> None:
    from l3_client.local_mcps.windows_uia_mcp import server as windows_uia_server
    from l3_node.primitives.mcp.registry import MCPToolRegistry

    calls: list[tuple[str, str, str, int, int, bool, bool, str]] = []

    def fake_demo(
        project_name: str = "Jachin",
        project_path: str = "",
        recipients_json: str = "[]",
        since_days: int = 3,
        wait_seconds: int = 120,
        send_summary: bool = True,
        remember: bool = True,
        out_dir: str = "",
    ) -> str:
        calls.append((project_name, project_path, recipients_json, since_days, wait_seconds, send_summary, remember, out_dir))
        return '{"task":"windows_codex_lark_standard_demo","ok":true,"evidence":{"evidence_panel_path":"panel.html"}}'

    monkeypatch.setattr(windows_uia_server, "windows_codex_lark_standard_demo", fake_demo)

    registry = MCPToolRegistry()
    result = await registry._invoke_impl(
        "mcp:windows_codex_lark_standard_demo",
        '{"project_name":"Jachin","project_path":"D:/Projects/jachi/jachin-system-main","recipients":["Vivian","Samuel"],"wait_seconds":60}',
        allow_l2_delegate=False,
    )

    assert calls == [("Jachin", "D:/Projects/jachi/jachin-system-main", "[\"Vivian\", \"Samuel\"]", 3, 60, True, True, "")]
    assert '"panel.html"' in result


async def test_windows_os_mission_execute_mcp_invokes_local_handler(monkeypatch) -> None:
    from l3_client.local_mcps.windows_uia_mcp import server as windows_uia_server
    from l3_node.primitives.mcp.registry import MCPToolRegistry

    calls: list[tuple[str, str, bool, bool, str]] = []

    def fake_mission(goal: str = "", steps_json: str = "", dry_run: bool = False, confirm_send: bool = False, out_dir: str = "") -> str:
        calls.append((goal, steps_json, dry_run, confirm_send, out_dir))
        return '{"task":"windows_os_mission_execute","ok":true,"detail":"mission_planned"}'

    monkeypatch.setattr(windows_uia_server, "windows_os_mission_execute", fake_mission)

    registry = MCPToolRegistry()
    result = await registry._invoke_impl(
        "mcp:windows_os_mission_execute",
        '{"goal":"demo","steps":[{"action":"window_list"}],"dry_run":true}',
        allow_l2_delegate=False,
    )

    assert calls == [("demo", "[{\"action\": \"window_list\"}]", True, False, "")]
    assert '"mission_planned"' in result


def test_windows_file_write_text_overwrite_requires_confirmation(tmp_path) -> None:
    from l3_client.local_mcps.windows_uia_mcp.os_tasks import WindowsOSAutomation

    target = tmp_path / "note.txt"
    target.write_text("old", encoding="utf-8")

    result = WindowsOSAutomation(out_dir=tmp_path / "vision").file_write_text(
        str(target),
        "new",
        overwrite=True,
        confirm=False,
    )

    assert result.ok is False
    assert result.detail == "confirmation_required"
    assert target.read_text(encoding="utf-8") == "old"


def test_windows_recent_files_classifies_files(tmp_path) -> None:
    import json

    from l3_client.local_mcps.windows_uia_mcp.os_tasks import WindowsOSAutomation

    (tmp_path / "a.png").write_bytes(b"x")
    (tmp_path / "b.py").write_text("print(1)", encoding="utf-8")

    result = WindowsOSAutomation(out_dir=tmp_path / "vision").recent_files(
        paths_json=json.dumps([str(tmp_path)]),
        since_days=1,
        max_results=10,
    )

    assert result.ok is True
    assert result.evidence["by_category"]["image"] == 1
    assert result.evidence["by_category"]["code"] == 1


def test_windows_project_briefing_remembers_path_and_falls_back_without_qwen(tmp_path, monkeypatch) -> None:
    from pathlib import Path

    from l3_client.local_mcps.windows_uia_mcp.os_tasks import WindowsOSAutomation

    monkeypatch.setenv("JACHIN_OS_PROJECT_MEMORY_PATH", str(tmp_path / "memory.json"))
    project = tmp_path / "Jachin"
    project.mkdir()
    (project / "feature.py").write_text("def hello():\n    return 'world'\n", encoding="utf-8")

    auto = WindowsOSAutomation(out_dir=tmp_path / "vision")
    remembered = auto.project_remember("Jachin", str(project))
    assert remembered.ok is True

    result = auto.project_latest_briefing(
        project_name="Jachin",
        project_path="",
        feature_query="hello",
        use_qwen=False,
        open_report=False,
        send_summary=False,
    )

    assert result.ok is True
    assert result.evidence["project_path"] == str(project.resolve())
    assert Path(result.evidence["report_path"]).exists()
    assert Path(result.evidence["evidence_path"]).exists()
    assert "项目 Jachin 最新简报" in result.evidence["summary"]


def test_windows_evidence_panel_renders_html_from_evidence_json(tmp_path) -> None:
    import json
    from pathlib import Path

    from l3_client.local_mcps.windows_uia_mcp.os_tasks import WindowsOSAutomation

    shot = tmp_path / "shot.png"
    shot.write_bytes(b"fake")
    evidence = {
        "task": "demo",
        "ok": True,
        "detail": "done",
        "recipients": ["Vivian"],
        "message_text": "Jachin demo output",
        "screenshots": {"typed": str(shot)},
        "validation": {"ok": True, "checks": {"non_empty": True}},
        "timeline": [
            {"ts": "2026-06-26 10:00:00", "stage": "open_codex", "status": "done", "detail": "Codex focused"},
            {"ts": "2026-06-26 10:00:10", "stage": "lark.verify_sent", "status": "done", "detail": "Vivian verified"},
        ],
    }
    evidence_path = tmp_path / "demo.evidence.json"
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False), encoding="utf-8")

    result = WindowsOSAutomation(out_dir=tmp_path / "vision").evidence_panel(str(evidence_path), title="Demo Panel")

    assert result.ok is True
    panel = Path(result.evidence["evidence_panel_path"])
    assert panel.exists()
    html = panel.read_text(encoding="utf-8")
    assert "Demo Panel" in html
    assert "Vivian" in html
    assert "Jachin demo output" in html
    assert "Execution Timeline" in html
    assert "open_codex" in html
    assert "lark.verify_sent" in html


def test_codex_lark_workflow_template_merges_directory_and_bug_query(tmp_path, monkeypatch) -> None:
    from pathlib import Path

    from l3_client.local_mcps.windows_uia_mcp.os_tasks import TaskResult, WindowsOSAutomation

    calls: list[dict] = []

    def fake_codex(self, **kwargs):
        calls.append(kwargs)
        return TaskResult(
            "windows_codex_project_briefing_to_lark",
            True,
            "codex_briefing_sent",
            {
                "report_path": str(tmp_path / "report.md"),
                "evidence_path": str(tmp_path / "run.evidence.json"),
                "message_text": "summary",
                "send_result": {"ok": True, "evidence": {"recipients": kwargs.get("recipients")}},
            },
        )

    monkeypatch.setattr(WindowsOSAutomation, "codex_project_briefing_to_lark", fake_codex)

    result = WindowsOSAutomation(out_dir=tmp_path / "vision").codex_lark_workflow_template(
        directory_path=str(tmp_path),
        bug_query="login fails",
        recipients=["Vivian", "测试备注冒烟草稿"],
        send_summary=True,
    )

    assert result.ok is True
    assert calls[0]["project_name"] == tmp_path.name
    assert calls[0]["project_path"] == str(tmp_path)
    assert "directory briefing" in calls[0]["feature_query"]
    assert "bug analysis: login fails" in calls[0]["feature_query"]
    assert calls[0]["recipients"] == ["Vivian", "测试备注冒烟草稿"]
    assert Path(result.evidence["evidence_panel_path"]).exists()


def test_codex_lark_standard_demo_uses_project_memory_defaults(tmp_path, monkeypatch) -> None:
    from l3_client.local_mcps.windows_uia_mcp.os_tasks import TaskResult, WindowsOSAutomation

    calls: list[dict] = []

    def fake_template(self, **kwargs):
        calls.append(kwargs)
        return TaskResult(
            "windows_codex_lark_workflow_template",
            True,
            "template_workflow_completed",
            {"evidence_panel_path": str(tmp_path / "panel.html")},
        )

    monkeypatch.setattr(WindowsOSAutomation, "codex_lark_workflow_template", fake_template)

    result = WindowsOSAutomation(out_dir=tmp_path / "vision").codex_lark_standard_demo(
        project_name="Jachin",
        project_path=str(tmp_path),
        recipients=["Vivian", "测试备注冒烟草稿"],
        send_summary=True,
    )

    assert result.ok is True
    assert calls[0]["project_name"] == "Jachin"
    assert calls[0]["project_path"] == str(tmp_path)
    assert calls[0]["feature_query"] == "OS assistant Codex Lark workflow"
    assert calls[0]["recipients"] == ["Vivian", "测试备注冒烟草稿"]
    assert calls[0]["since_days"] == 3
    assert calls[0]["send_summary"] is True


def test_codex_response_validation_requires_project_and_evidence_signals() -> None:
    from l3_client.local_mcps.windows_uia_mcp.os_tasks import _codex_response_valid

    good = (
        "Jachin 项目最新进展：完成 windows_codex_project_briefing_to_lark 工作流，涉及 "
        "l3_client/local_mcps/windows_uia_mcp/os_tasks.py 和 registry.py。风险是 Codex UI 复制需要视觉校验，"
        "下一步建议补充真实桌面烟测。"
    )
    bad = "好了。"

    assert _codex_response_valid(good, "Jachin")["ok"] is True
    assert _codex_response_valid(bad, "Jachin")["ok"] is False


def test_codex_response_validation_rejects_prompt_echo() -> None:
    from l3_client.local_mcps.windows_uia_mcp.os_tasks import _build_codex_project_prompt, _codex_response_valid

    prompt = _build_codex_project_prompt(
        "Jachin",
        r"D:\Projects\jachi\jachin-system-main",
        feature_query="OS assistant Codex Lark workflow",
        since_days=3,
    )

    validation = _codex_response_valid(prompt, "Jachin", "OS assistant Codex Lark workflow")

    assert validation["ok"] is False
    assert validation["checks"]["not_prompt_echo"] is False


def test_codex_prompt_can_be_polished_from_original_user_input(monkeypatch) -> None:
    from l3_client.local_mcps.windows_uia_mcp import os_tasks

    original = "总结Jachin最近开发了什么新功能，使用codex总结然后发给Neil，要一条一条列出来"
    project_path = r"D:\Projects\jachi\jachin-system-main"

    def fake_qwen(prompt: str, model: str = "", timeout: int = 45) -> dict:
        assert original in prompt
        return {
            "ok": True,
            "detail": "prompt_polished",
            "model": model,
            "content": (
                f"请作为 Codex 读取本机项目 Jachin。\n"
                f"项目路径：{project_path}\n"
                "请检查 git status、最近 commit、未提交 diff 和相关文件证据。\n"
                "重点回答用户关心的：最近开发了什么新功能，并且一条一条列出来。\n"
                "请优先阅读 OS Mission、Codex、Lark workflow、Evidence、确认策略相关文件，"
                "把新增能力、涉及模块、风险、下一步建议分别列清楚。"
                "最后生成一段适合发给 Neil 的 Lark 中文短消息。不要编造，必须说明依据来自文件、diff 或提交。"
            ),
        }

    monkeypatch.setattr(os_tasks, "_call_qwen_coder", fake_qwen)

    prompt, meta = os_tasks._build_codex_project_prompt_with_meta(
        "Jachin",
        project_path,
        feature_query="最近开发了什么新功能",
        since_days=3,
        original_user_input=original,
    )

    assert meta["strategy"] == "llm_polished_from_user_input"
    assert "最近开发了什么新功能" in prompt
    assert "一条一条列出来" in prompt
    assert project_path in prompt


def test_codex_copy_prefers_visible_copy_button(monkeypatch) -> None:
    import sys
    import types

    from l3_client.local_mcps.windows_uia_mcp import os_tasks
    from l3_client.local_mcps.windows_uia_mcp.os_tasks import WindowsOSAutomation

    clipboard = {"text": ""}
    copied_answer = (
        "Jachin 项目最新进展：已完成 Codex 到 Lark 的 OS workflow 优化，涉及 "
        "l3_client/local_mcps/windows_uia_mcp/os_tasks.py 和 clients/desktop/src/components/Chat/AssistantMessageContent.tsx。"
        "当前风险是需要继续做真实 UI 复制和发送的 evidence 验证，下一步建议跑 smoke matrix。"
    )

    fake_pyperclip = types.SimpleNamespace(
        copy=lambda text: clipboard.__setitem__("text", str(text)),
        paste=lambda: clipboard["text"],
    )
    monkeypatch.setitem(sys.modules, "pyperclip", fake_pyperclip)

    class Rect:
        left = 20
        top = 580
        right = 62
        bottom = 622

    class CopyButton:
        Name = "复制"
        ControlTypeName = "ButtonControl"
        BoundingRectangle = Rect()
        IsEnabled = True
        IsOffscreen = False

        def GetChildren(self):
            return []

        def SetFocus(self):
            return None

        def Click(self):
            clipboard["text"] = copied_answer

    class Root:
        def GetChildren(self):
            return [CopyButton()]

    fake_uia = types.SimpleNamespace(GetRootControl=lambda: Root())
    monkeypatch.setattr(os_tasks, "_import_uia", lambda: (fake_uia, ""))

    auto = object.__new__(WindowsOSAutomation)
    auto.win = types.SimpleNamespace(
        active_title=lambda: "Codex",
        active_rect=lambda: ("Codex", 0, 0, 900, 700),
        focus_by_keywords=lambda *args, **kwargs: True,
    )
    auto.io = types.SimpleNamespace(
        click=lambda *args, **kwargs: None,
        hotkey=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("hotkey fallback should not run")),
    )

    result = auto._codex_copy_latest_response("Jachin", "OS workflow")

    assert result["ok"] is True
    assert result["detail"] == "copied_by_codex_copy_button"
    assert result["text"] == copied_answer
    assert any(attempt["method"] == "uia_scan_copy_buttons" for attempt in result["attempts"])


def test_codex_copy_refuses_when_codex_not_foreground(monkeypatch) -> None:
    import sys
    import types

    from l3_client.local_mcps.windows_uia_mcp import os_tasks
    from l3_client.local_mcps.windows_uia_mcp.os_tasks import WindowsOSAutomation

    clipboard = {"text": ""}
    fake_pyperclip = types.SimpleNamespace(
        copy=lambda text: clipboard.__setitem__("text", str(text)),
        paste=lambda: clipboard["text"],
    )
    monkeypatch.setitem(sys.modules, "pyperclip", fake_pyperclip)
    monkeypatch.setattr(os_tasks, "_import_uia", lambda: (None, "uiautomation_not_available"))

    auto = object.__new__(WindowsOSAutomation)
    auto.win = types.SimpleNamespace(
        active_title=lambda: "新建 文本文档 (11).txt - Notepad",
        active_rect=lambda: ("新建 文本文档 (11).txt - Notepad", 100, 100, 900, 700),
        focus_by_keywords=lambda *args, **kwargs: False,
    )
    auto.io = types.SimpleNamespace(
        click=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not click outside Codex")),
        hotkey=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not hotkey outside Codex")),
    )

    result = auto._codex_copy_latest_response("Jachin", "OS workflow")

    assert result["ok"] is False
    assert result["detail"] == "codex_focus_lost_before_copy"
    assert result["text"] == ""
    assert result["attempts"][0]["method"] == "codex_focus_guard"


def test_codex_brief_message_prefers_ocr_when_clipboard_is_prompt_echo() -> None:
    from l3_client.local_mcps.windows_uia_mcp.os_tasks import _build_codex_project_prompt, _choose_codex_brief_message

    prompt = _build_codex_project_prompt(
        "Jachin",
        r"D:\Projects\jachi\jachin-system-main",
        feature_query="OS assistant Codex Lark workflow",
        since_days=3,
    )
    ocr = """
    Jachin 最近 3 天的最新进展集中在 OS assistant Codex Lark workflow。
    1. 最新完成/修改了什么
    已完成 windows_codex_project_briefing_to_lark 跨 App 工作流，支持打开 Codex、读取项目、生成简报，再发送到 Lark。
    2. 涉及模块和关键文件
    l3_client/local_mcps/windows_uia_mcp/os_tasks.py 负责 Codex 输入、OCR 回退、Lark 发送校验。
    tests/unit/test_os_assistant_capability.py 覆盖提示词回声拦截和 OCR 回退。
    3. 当前风险或未完成点
    真实 UI 复制仍可能拿到输入框内容，需要保留视觉/OCR 证据链。
    4. 下一步建议
    继续跑 live-ui smoke，并检查 evidence JSON 与 Lark 截图。
    """

    choice = _choose_codex_brief_message(prompt, ocr, "Jachin", feature_query="OS assistant Codex Lark workflow")

    assert choice["message_source"] == "ocr_fallback"
    assert choice["validation"]["ok"] is True
    assert choice["copied_validation"]["checks"]["not_prompt_echo"] is False
    assert "请总结 Windows 本机项目" not in choice["message_text"]
    assert "windows_codex_project_briefing_to_lark" in choice["message_text"]


def test_codex_brief_message_prefers_qwen_vision_over_clipboard() -> None:
    from l3_client.local_mcps.windows_uia_mcp.os_tasks import _choose_codex_brief_message

    wrong_clipboard = "这是一段旧剪贴板内容，和当前任务无关。"
    vision_text = (
        "Jachin 项目最新进展：已完成 Codex 到 Lark 的 OS assistant 工作流优化，新增视觉模型抽取 Codex "
        "最终回复的能力，涉及 l3_client/local_mcps/windows_uia_mcp/os_tasks.py 和 "
        "tests/unit/test_os_assistant_capability.py。当前风险是还需要继续做真实 UI 发送验证，下一步建议跑 "
        "smoke matrix 并检查 Evidence 截图、OCR 和视觉模型抽取结果。"
    )

    choice = _choose_codex_brief_message(
        wrong_clipboard,
        "",
        "Jachin",
        feature_query="OS assistant workflow",
        vision_text=vision_text,
    )

    assert choice["message_source"] == "qwen_vision"
    assert choice["validation"]["ok"] is True
    assert choice["message_text"] == vision_text


def test_codex_ocr_fallback_extracts_visible_brief() -> None:
    from l3_client.local_mcps.windows_uia_mcp.os_tasks import _codex_response_valid, _extract_codex_brief_from_ocr

    ocr = """
    口
    新对话
    Q搜索
    @插件
    自动化
    已处理22s>
    Codex移动版
    Jachin最近3天正式提交只有e8eb0f9c Release v0.9.93，当前主要进展集中在未提交工作区。
    1.最新完成/修改了什么
    已完成OSAssistant向多App调度方向的扩展：新增Windows UIA MCP能力，支持项目路径记忆、项目级简报、Codex作为代码分析App、Lark作为发送App的跨应用工作流。
    2.涉及模块/关键文件
    l3_client/local_mcps/windows_uia_mcp/os_tasks.py：核心OS自动化、项目记忆、Codex输入/等待/复制/校验、Lark发送编排。
    l3_node/primitives/mcp/registry.py：注册和分发新MCP工具。
    3.当前风险/未完成点
    真实UI复制和发送仍需要桌面截图证据。
    4.下一步建议
    运行live-ui烟测并保存evidence JSON。
    设置
    """

    text = _extract_codex_brief_from_ocr(ocr, "Jachin")

    assert "Jachin最近3天" in text
    assert "os_tasks.py" in text
    assert _codex_response_valid(text, "Jachin", "OS assistant Codex Lark workflow")["ok"] is True


def test_lark_ocr_lines_are_deduped_and_classified() -> None:
    from l3_client.local_mcps.windows_uia_mcp.os_tasks import (
        _classify_lark_lines,
        _dedupe_lines,
        _group_lark_lines_by_time,
    )

    lines = _dedupe_lines(["Vivian\n16:48\n今天请帮我确认\n今天请帮我确认", "昨天\n阻塞 风险"])
    classified = _classify_lark_lines(lines)
    groups = _group_lark_lines_by_time(lines)

    assert lines == ["Vivian", "16:48", "今天请帮我确认", "昨天", "阻塞 风险"]
    assert "今天请帮我确认" in classified["task_like_lines"]
    assert "阻塞 风险" in classified["urgent_lines"]
    assert groups[1] == {"time": "16:48", "lines": ["今天请帮我确认"]}


def test_open_app_profile_normalizes_lark_alias() -> None:
    from l3_client.local_mcps.windows_uia_mcp.os_tasks import normalize_app_name

    assert normalize_app_name("Lark") == "lark"
    assert normalize_app_name("飞书") == "lark"
    assert normalize_app_name("Feishu") == "lark"


def test_lark_long_message_preview_matches_visible_tail_anchors() -> None:
    from l3_client.local_mcps.windows_uia_mcp.os_tasks import _lark_message_visible_match

    message = (
        "基于本地Git状态、最近提交、未提交diff和相关文件读取，Jachin最近3天最新进展如下：\n"
        "当前主要进展在未提交工作区：OSAssistant能力明显扩展，新增WindowsUIAMCP、文件操作、窗口/系统感知、Lark消息、"
        "Lark历史读取、多维表格、项目简报，以及Codex->Lark跨App工作流。\n"
        "今天修复了Codex结果复制误拿原始prompt的问题，已加入OCRfallback和promptecho校验。"
        "下一步会做真实端到端验证并整理提交边界。"
    )
    visible_ocr_tail = """
    展：新增WindowsUIAMCP，支持窗山/文件/
    系统感知、Lark消息、
    多维表格、项目简报，以及Codex分析项目后
    自动发送到Lark的跨App工作流。今天修复了
    Codex结果复制误拿
    原始prompt的问题，已加入OCRfallback和
    promptecho校验。下一步会做真实端到端验
    证并整理提交边界。
    Shift + Enter 换行
    """

    match = _lark_message_visible_match(message, visible_ocr_tail)

    assert match["ok"] is True
    assert match["strategy"] == "anchor"
    assert len(match["hits"]) >= match["required"]


def test_lark_history_window_labels_include_iso_dates() -> None:
    from datetime import date

    from l3_client.local_mcps.windows_uia_mcp.os_tasks import _history_window_labels

    labels = _history_window_labels(3, today=date(2026, 6, 25))

    assert "2026-06-25" in labels
    assert "2026-06-24" in labels
    assert "2026-06-23" in labels


def test_lark_scroll_overlap_detects_too_small_scroll() -> None:
    from l3_client.local_mcps.windows_uia_mcp.os_tasks import _line_overlap_ratio, _ocr_content_keys

    page_a = _ocr_content_keys("Vivian\n16:48\n你好，我在\nSamuel\n图片\n测试备注冒烟草稿")
    page_b = _ocr_content_keys("Vivian\n16:48\n你好，我在\nSamuel\n图片\n测试备注冒烟草稿\n新的一行")
    page_c = _ocr_content_keys("昨天\n10:02\nAIRobot 战报\n工作流好用就行")

    assert _line_overlap_ratio(page_a, page_b) > 0.82
    assert _line_overlap_ratio(page_a, page_c) < 0.5


def test_windows_file_copy_and_folder_summarize(tmp_path) -> None:
    from l3_client.local_mcps.windows_uia_mcp.os_tasks import WindowsOSAutomation

    src = tmp_path / "source.txt"
    dst = tmp_path / "nested" / "copied.txt"
    src.write_text("hello", encoding="utf-8")

    auto = WindowsOSAutomation(out_dir=tmp_path / "vision")
    copied = auto.file_copy(str(src), str(dst))
    summary = auto.folder_summarize(str(tmp_path), max_depth=2)

    assert copied.ok is True
    assert dst.read_text(encoding="utf-8") == "hello"
    assert summary.ok is True
    assert summary.evidence["total_files"] >= 2


def test_windows_file_overwrite_requires_confirmation(tmp_path) -> None:
    from l3_client.local_mcps.windows_uia_mcp.os_tasks import WindowsOSAutomation

    src = tmp_path / "source.txt"
    dst = tmp_path / "dest.txt"
    src.write_text("new", encoding="utf-8")
    dst.write_text("old", encoding="utf-8")

    result = WindowsOSAutomation(out_dir=tmp_path / "vision").file_copy(str(src), str(dst))

    assert result.ok is False
    assert result.detail == "confirmation_required"
    assert result.evidence["confirmation_required"] is True
    assert dst.read_text(encoding="utf-8") == "old"


def test_windows_file_delete_respects_dangerous_setting(monkeypatch, tmp_path) -> None:
    from l3_client.local_mcps.windows_uia_mcp.os_tasks import WindowsOSAutomation

    target = tmp_path / "delete-me.txt"
    target.write_text("bye", encoding="utf-8")
    monkeypatch.setenv("JACHIN_OS_FILE_DANGEROUS_NO_CONFIRM", "1")

    result = WindowsOSAutomation(out_dir=tmp_path / "vision").file_delete_with_confirm(str(target))

    assert result.ok is True
    assert not target.exists()
    assert result.evidence["dangerous_bypassed"] is True


def test_open_app_finds_lark_start_menu_shortcut(monkeypatch, tmp_path) -> None:
    from l3_client.local_mcps.windows_uia_mcp.os_tasks import _find_app_executable

    shortcut = tmp_path / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Lark.lnk"
    shortcut.parent.mkdir(parents=True)
    shortcut.write_text("fake shortcut", encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(tmp_path))

    found, source = _find_app_executable(
        {
            "candidate_paths": (r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Lark.lnk",),
            "exe_names": ("Lark.exe",),
        }
    )

    assert found == str(shortcut)
    assert source == "candidate_path"


def test_calculator_visual_state_detects_wrong_display(monkeypatch, tmp_path) -> None:
    from l3_client.local_mcps.gameqa_mcp.core import ocr_engine
    from l3_client.local_mcps.windows_uia_mcp.os_tasks import (
        calculator_visual_state,
        normalize_calculator_expression,
    )

    def fake_ocr(_png: bytes) -> tuple[str, str, str]:
        return ("计算器\n标准\n0 + 9\n9\nMC\nMR\n7\n8\n9", "fake", "fake")

    img = tmp_path / "calc.png"
    img.write_bytes(b"not-a-real-png")
    monkeypatch.setattr(ocr_engine, "ocr_png_bytes", fake_ocr)

    state = calculator_visual_state(img)

    assert state["expression_norm"] == "0+9"
    assert state["result_norm"] == "9"
    assert state["expression_norm"] != normalize_calculator_expression("91+9")


def test_calculator_visual_state_prefers_expected_result(monkeypatch, tmp_path) -> None:
    from l3_client.local_mcps.gameqa_mcp.core import ocr_engine
    from l3_client.local_mcps.windows_uia_mcp.os_tasks import calculator_visual_state

    def fake_ocr(_png: bytes) -> tuple[str, str, str]:
        return ("计算器\n标准\n91 + 9 =\n00\n100\nMC\nMR", "fake", "fake")

    img = tmp_path / "calc.png"
    img.write_bytes(b"not-a-real-png")
    monkeypatch.setattr(ocr_engine, "ocr_png_bytes", fake_ocr)

    state = calculator_visual_state(img, "100")

    assert state["expression_norm"] == "91+9"
    assert state["result_norm"] == "100"
