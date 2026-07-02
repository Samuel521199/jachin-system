#!/usr/bin/env python3
"""Windows UI Automation MCP server.

This MCP is the structured Windows layer: it uses Microsoft UI Automation
through the Python ``uiautomation`` package. It should be preferred over
pixel clicking when applications expose accessibility controls.

Run:
  python -m l3_client.local_mcps.windows_uia_mcp.server
"""
from __future__ import annotations

import json
import logging
import re
import sys
import time
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("windows_uia_mcp")

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    logger.error("Please install mcp: pip install mcp")
    sys.exit(1)

try:
    mcp = FastMCP(
        "windows-uia",
        description="Windows UI Automation: inspect controls, click by name, set text.",
    )
except TypeError:
    mcp = FastMCP("windows-uia")


def _import_uia():
    try:
        import uiautomation as auto  # type: ignore

        return auto, ""
    except Exception as e:
        return None, (
            "uiautomation_not_available:"
            f"{e!r}; install with `.\\.venv-omniparser\\Scripts\\python.exe -m pip install uiautomation`"
        )


def _row(ctrl: Any, depth: int) -> dict[str, Any]:
    rect = getattr(ctrl, "BoundingRectangle", None)
    out = {
        "depth": depth,
        "name": getattr(ctrl, "Name", "") or "",
        "control_type": getattr(ctrl, "ControlTypeName", "") or "",
        "automation_id": getattr(ctrl, "AutomationId", "") or "",
        "class_name": getattr(ctrl, "ClassName", "") or "",
        "enabled": bool(getattr(ctrl, "IsEnabled", False)),
        "offscreen": bool(getattr(ctrl, "IsOffscreen", False)),
    }
    if rect:
        left = int(getattr(rect, "left", 0))
        top = int(getattr(rect, "top", 0))
        right = int(getattr(rect, "right", 0))
        bottom = int(getattr(rect, "bottom", 0))
        out["rect"] = [left, top, right, bottom]
        out["center_x"] = int((left + right) / 2)
        out["center_y"] = int((top + bottom) / 2)
    return out


def _matches(row: dict[str, Any], *, name: str, control_type: str = "", regex: bool = False) -> bool:
    hay_name = str(row.get("name") or "")
    hay_type = str(row.get("control_type") or "")
    if control_type and control_type.lower() not in hay_type.lower():
        return False
    if not name:
        return True
    if regex:
        return re.search(name, hay_name, flags=re.I) is not None
    return name.lower() in hay_name.lower()


def _iter_controls(root: Any, max_depth: int = 4):
    stack: list[tuple[Any, int]] = [(root, 0)]
    while stack:
        ctrl, depth = stack.pop()
        yield ctrl, depth
        if depth >= max_depth:
            continue
        try:
            children = list(ctrl.GetChildren())
        except Exception:
            children = []
        for child in reversed(children):
            stack.append((child, depth + 1))


def _find_control(
    *,
    name: str,
    control_type: str = "",
    regex: bool = False,
    timeout: float = 5.0,
    max_depth: int = 6,
):
    auto, err = _import_uia()
    if auto is None:
        return None, None, err
    deadline = time.time() + max(0.1, timeout)
    while time.time() < deadline:
        root = auto.GetRootControl()
        best = None
        best_row = None
        for ctrl, depth in _iter_controls(root, max_depth=max_depth):
            row = _row(ctrl, depth)
            if _matches(row, name=name, control_type=control_type, regex=regex):
                best = ctrl
                best_row = row
                break
        if best is not None:
            return best, best_row, ""
        time.sleep(0.2)
    return None, None, f"control_not_found name={name!r} control_type={control_type!r}"


@mcp.tool(name="uia_snapshot")
def uia_snapshot(max_depth: int = 3, name_contains: str = "", control_type: str = "", limit: int = 120) -> str:
    """Return a compact Windows UIA control tree snapshot."""
    auto, err = _import_uia()
    if auto is None:
        return json.dumps({"ok": False, "error": err}, ensure_ascii=False)
    rows: list[dict[str, Any]] = []
    root = auto.GetRootControl()
    for ctrl, depth in _iter_controls(root, max_depth=max(0, int(max_depth))):
        row = _row(ctrl, depth)
        if name_contains and name_contains.lower() not in str(row.get("name") or "").lower():
            continue
        if control_type and control_type.lower() not in str(row.get("control_type") or "").lower():
            continue
        rows.append(row)
        if len(rows) >= max(1, int(limit)):
            break
    return json.dumps({"ok": True, "count": len(rows), "controls": rows}, ensure_ascii=False)


@mcp.tool(name="uia_click")
def uia_click(name: str, control_type: str = "", regex: bool = False, timeout: float = 5.0) -> str:
    """Click a Windows UIA control by accessible name."""
    ctrl, row, err = _find_control(name=name, control_type=control_type, regex=regex, timeout=timeout)
    if ctrl is None:
        return json.dumps({"ok": False, "error": err}, ensure_ascii=False)
    try:
        try:
            ctrl.SetFocus()
        except Exception:
            pass
        ctrl.Click()
        return json.dumps({"ok": True, "control": row}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": f"click_failed:{e!r}", "control": row}, ensure_ascii=False)


@mcp.tool(name="uia_set_text")
def uia_set_text(name: str, text: str, control_type: str = "", regex: bool = False, timeout: float = 5.0, press_enter: bool = False) -> str:
    """Focus a UIA text control and set/paste text."""
    ctrl, row, err = _find_control(name=name, control_type=control_type, regex=regex, timeout=timeout)
    if ctrl is None:
        return json.dumps({"ok": False, "error": err}, ensure_ascii=False)
    try:
        ctrl.SetFocus()
        try:
            value = ctrl.GetValuePattern()
            value.SetValue(str(text))
            method = "value_pattern"
        except Exception:
            import pyperclip

            auto, _ = _import_uia()
            pyperclip.copy(str(text))
            auto.SendKeys("{Ctrl}v")
            method = "clipboard"
        if press_enter:
            auto, _ = _import_uia()
            auto.SendKeys("{Enter}")
        return json.dumps({"ok": True, "method": method, "control": row, "typed_len": len(str(text))}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": f"set_text_failed:{e!r}", "control": row}, ensure_ascii=False)


@mcp.tool(name="uia_focused")
def uia_focused() -> str:
    """Return the currently focused Windows UIA control."""
    auto, err = _import_uia()
    if auto is None:
        return json.dumps({"ok": False, "error": err}, ensure_ascii=False)
    try:
        ctrl = auto.GetFocusedControl()
        return json.dumps({"ok": True, "control": _row(ctrl, 0)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": f"focused_failed:{e!r}"}, ensure_ascii=False)


def _os_auto(out_dir: str = ""):
    from .os_tasks import WindowsOSAutomation

    return WindowsOSAutomation(out_dir=out_dir or None)


def _tool_exception_json(task: str, exc: Exception) -> str:
    detail = f"failed:{exc!r}"
    evidence: dict[str, Any] = {"exception_type": type(exc).__name__}
    if type(exc).__name__ == "MouseFailSafeInterrupt" or "fail-safe" in str(exc).lower() or "mouse_failsafe_triggered" in str(exc).lower():
        detail = "mouse_failsafe_triggered"
        to_evidence = getattr(exc, "to_evidence", None)
        if callable(to_evidence):
            try:
                evidence["mouse_failsafe"] = to_evidence()
            except Exception:
                evidence["mouse_failsafe"] = {"detail": "mouse_failsafe_triggered"}
        else:
            evidence["mouse_failsafe"] = {"detail": "mouse_failsafe_triggered"}
        evidence["side_effect_status"] = "interrupted_by_user_safety_corner"
    return json.dumps({"task": task, "ok": False, "detail": detail, "evidence": evidence}, ensure_ascii=False)


@mcp.tool(name="windows_notepad_save_text")
def windows_notepad_save_text(text: str, target_path: str, out_dir: str = "") -> str:
    """Open Notepad, write text, save it, and verify file contents."""
    try:
        return _os_auto(out_dir).notepad_edit_save(text=text, target_path=target_path).to_json()
    except Exception as e:
        return _tool_exception_json("notepad", e)


@mcp.tool(name="windows_calculator_calculate")
def windows_calculator_calculate(expression: str, expected: str = "", out_dir: str = "") -> str:
    """Open Calculator, type an expression, copy/read the result, and verify it."""
    try:
        return _os_auto(out_dir).calculator_calculate(expression=expression, expected=expected).to_json()
    except Exception as e:
        return _tool_exception_json("calculator", e)


@mcp.tool(name="windows_open_app")
def windows_open_app(app_name: str, args_json: str = "[]", out_dir: str = "") -> str:
    """Open a Windows app by profile/name, focus its window, and return verification evidence."""
    try:
        try:
            raw_args = json.loads(args_json or "[]")
            args = [str(x) for x in raw_args] if isinstance(raw_args, list) else []
        except Exception:
            args = []
        return _os_auto(out_dir).open_app(app_name=app_name, args=args).to_json()
    except Exception as e:
        return _tool_exception_json("open_app", e)


@mcp.tool(name="windows_lark_send_message")
def windows_lark_send_message(recipients_json: str, message: str, out_dir: str = "", max_attempts: int = 2) -> str:
    """Open Lark, send a message to one or more recipients, and verify with screenshot/OCR."""
    try:
        try:
            raw = json.loads(recipients_json or "[]")
            if isinstance(raw, str):
                recipients = [raw]
            elif isinstance(raw, list):
                recipients = [str(x) for x in raw]
            else:
                recipients = []
        except Exception:
            recipients = [x.strip() for x in str(recipients_json or "").split(",") if x.strip()]
        return _os_auto(out_dir).lark_send_message(recipients=recipients, message=message, max_attempts=max_attempts).to_json()
    except Exception as e:
        return _tool_exception_json("lark_send_message", e)


@mcp.tool(name="windows_codex_ask_lark_send")
def windows_codex_ask_lark_send(
    question: str,
    recipients_json: str = "[]",
    original_user_input: str = "",
    wait_seconds: int = 90,
    out_dir: str = "",
) -> str:
    """Ask Codex a question, validate the reply, then send the reply to Lark recipients."""
    try:
        try:
            raw = json.loads(recipients_json or "[]")
            if isinstance(raw, str):
                recipients = [raw]
            elif isinstance(raw, list):
                recipients = [str(x) for x in raw]
            else:
                recipients = []
        except Exception:
            recipients = [x.strip() for x in str(recipients_json or "").split(",") if x.strip()]
        return _os_auto(out_dir).codex_ask_lark_send(
            question=question,
            recipients=recipients,
            original_user_input=original_user_input,
            wait_seconds=wait_seconds,
        ).to_json()
    except Exception as e:
        return _tool_exception_json("windows_codex_ask_lark_send", e)


@mcp.tool(name="windows_lark_read_recent_messages")
def windows_lark_read_recent_messages(target: str, pages: int = 3, scroll_clicks: int = 5, out_dir: str = "") -> str:
    """Open a Lark chat/group, OCR recent message pages, dedupe lines, and classify likely tasks/mentions/urgent lines."""
    try:
        return _os_auto(out_dir).lark_read_recent_messages(target=target, pages=pages, scroll_clicks=scroll_clicks).to_json()
    except Exception as e:
        return _tool_exception_json("lark_read_recent_messages", e)


@mcp.tool(name="windows_lark_read_history")
def windows_lark_read_history(target: str, days: int = 7, max_pages: int = 18, scroll_clicks: int = 6, out_dir: str = "") -> str:
    """Open a Lark chat/group and OCR-scroll history for a requested day window."""
    try:
        return _os_auto(out_dir).lark_read_history(target=target, days=days, max_pages=max_pages, scroll_clicks=scroll_clicks).to_json()
    except Exception as e:
        return _tool_exception_json("lark_read_history", e)


@mcp.tool(name="windows_lark_open_bitable")
def windows_lark_open_bitable(table_name: str, out_dir: str = "", max_attempts: int = 2) -> str:
    """Open a Lark/Feishu Bitable by title through the desktop app and verify the browser/table view."""
    try:
        return _os_auto(out_dir).lark_open_bitable(table_name=table_name, max_attempts=max_attempts).to_json()
    except Exception as e:
        return _tool_exception_json("lark_open_bitable", e)


@mcp.tool(name="windows_lark_bitable_add_record")
def windows_lark_bitable_add_record(
    table_name: str,
    fields_json: str,
    confirm: bool = False,
    allow_dangerous: bool = False,
    out_dir: str = "",
    max_attempts: int = 2,
) -> str:
    """Add a record to a Lark/Feishu Bitable after confirmation, then verify with screenshot/OCR."""
    try:
        return _os_auto(out_dir).lark_bitable_add_record(
            table_name=table_name,
            fields_json=fields_json,
            confirm=confirm,
            allow_dangerous=allow_dangerous,
            max_attempts=max_attempts,
        ).to_json()
    except Exception as e:
        return _tool_exception_json("lark_bitable_add_record", e)


@mcp.tool(name="windows_lark_bitable_ai_paste_records")
def windows_lark_bitable_ai_paste_records(
    table_name: str,
    records_text: str,
    target_group: str = "2026/6/22",
    confirm: bool = False,
    allow_dangerous: bool = False,
    out_dir: str = "",
    max_attempts: int = 2,
) -> str:
    """Use Lark/Feishu Bitable AI paste import for multiple records after confirmation."""
    try:
        return _os_auto(out_dir).lark_bitable_ai_paste_records(
            table_name=table_name,
            records_text=records_text,
            target_group=target_group,
            confirm=confirm,
            allow_dangerous=allow_dangerous,
            max_attempts=max_attempts,
        ).to_json()
    except Exception as e:
        return _tool_exception_json("lark_bitable_ai_paste_records", e)


@mcp.tool(name="windows_lark_bitable_cdp_ai_paste_records")
def windows_lark_bitable_cdp_ai_paste_records(
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
    """Use browser CDP/DOM to open AI paste import in a logged-in Lark Bitable page."""
    try:
        from .bitable_cdp import windows_lark_bitable_cdp_ai_paste_records as _impl

        return _impl(
            table_name=table_name,
            bitable_url=bitable_url,
            records_text=records_text,
            target_group=target_group,
            cdp_url=cdp_url,
            launch_if_missing=launch_if_missing,
            submit=submit,
            confirm=confirm,
            allow_dangerous=allow_dangerous,
            out_dir=out_dir,
        )
    except Exception as e:
        return _tool_exception_json("lark_bitable_cdp_ai_paste_records", e)


@mcp.tool(name="windows_active_window")
def windows_active_window(out_dir: str = "") -> str:
    """Capture the current foreground window title, bounds, and screenshot evidence."""
    try:
        return _os_auto(out_dir).active_window().to_json()
    except Exception as e:
        return _tool_exception_json("windows_active_window", e)


@mcp.tool(name="windows_window_list")
def windows_window_list(limit: int = 80, out_dir: str = "") -> str:
    """List visible top-level Windows windows and classify office-related windows."""
    try:
        return _os_auto(out_dir).window_list(limit=limit).to_json()
    except Exception as e:
        return _tool_exception_json("windows_window_list", e)


@mcp.tool(name="windows_window_switch")
def windows_window_switch(keywords: str, exclude_keywords: str = "", timeout: float = 5.0, out_dir: str = "") -> str:
    """Focus a visible window by title/process keywords and return screenshot evidence."""
    try:
        return _os_auto(out_dir).window_switch(keywords=keywords, exclude_keywords=exclude_keywords, timeout=timeout).to_json()
    except Exception as e:
        return _tool_exception_json("windows_window_switch", e)


@mcp.tool(name="windows_disk_snapshot")
def windows_disk_snapshot(out_dir: str = "") -> str:
    """Collect drive free/used space."""
    try:
        return _os_auto(out_dir).disk_snapshot().to_json()
    except Exception as e:
        return _tool_exception_json("windows_disk_snapshot", e)


@mcp.tool(name="windows_network_check")
def windows_network_check(host: str = "www.baidu.com", port: int = 443, timeout: float = 3.0, out_dir: str = "") -> str:
    """Check basic outbound TCP network connectivity."""
    try:
        return _os_auto(out_dir).network_check(host=host, port=port, timeout=timeout).to_json()
    except Exception as e:
        return _tool_exception_json("windows_network_check", e)


@mcp.tool(name="windows_power_status")
def windows_power_status(out_dir: str = "") -> str:
    """Collect battery/power status when available."""
    try:
        return _os_auto(out_dir).power_status().to_json()
    except Exception as e:
        return _tool_exception_json("windows_power_status", e)


@mcp.tool(name="windows_process_snapshot")
def windows_process_snapshot(top: int = 10, out_dir: str = "") -> str:
    """Collect top processes by CPU usage."""
    try:
        return _os_auto(out_dir).process_snapshot(top=top).to_json()
    except Exception as e:
        return _tool_exception_json("windows_process_snapshot", e)


@mcp.tool(name="windows_system_status")
def windows_system_status(network_host: str = "www.baidu.com", out_dir: str = "") -> str:
    """Collect disk, network, power, and process status."""
    try:
        return _os_auto(out_dir).system_status(network_host=network_host).to_json()
    except Exception as e:
        return _tool_exception_json("windows_system_status", e)


@mcp.tool(name="windows_recent_files")
def windows_recent_files(paths_json: str = "", since_days: int = 1, max_results: int = 200, out_dir: str = "") -> str:
    """List recently changed files under Desktop/Downloads/Documents/project or provided paths_json."""
    try:
        return _os_auto(out_dir).recent_files(paths_json=paths_json, since_days=since_days, max_results=max_results).to_json()
    except Exception as e:
        return _tool_exception_json("windows_recent_files", e)


@mcp.tool(name="windows_folder_create")
def windows_folder_create(path: str, out_dir: str = "") -> str:
    """Create a folder and return filesystem evidence."""
    try:
        return _os_auto(out_dir).folder_create(path=path).to_json()
    except Exception as e:
        return _tool_exception_json("windows_folder_create", e)


@mcp.tool(name="windows_file_write_text")
def windows_file_write_text(path: str, text: str, overwrite: bool = False, confirm: bool = False, allow_dangerous: bool = False, out_dir: str = "") -> str:
    """Write a text file. Existing files require confirmation or dangerous bypass."""
    try:
        return _os_auto(out_dir).file_write_text(path=path, text=text, overwrite=overwrite, confirm=confirm, allow_dangerous=allow_dangerous).to_json()
    except Exception as e:
        return _tool_exception_json("windows_file_write_text", e)


@mcp.tool(name="windows_workspace_report")
def windows_workspace_report(output_path: str = "", since_days: int = 1, open_folder: bool = False, out_dir: str = "") -> str:
    """Generate a Windows OS workspace report with windows, files, system status, and evidence JSON."""
    try:
        return _os_auto(out_dir).workspace_report(output_path=output_path, since_days=since_days, open_folder=open_folder).to_json()
    except Exception as e:
        return _tool_exception_json("windows_workspace_report", e)


@mcp.tool(name="windows_evidence_panel")
def windows_evidence_panel(evidence_path: str, title: str = "", open_panel: bool = False, out_dir: str = "") -> str:
    """Render an evidence JSON file into a leadership-friendly HTML evidence panel."""
    try:
        return _os_auto(out_dir).evidence_panel(evidence_path=evidence_path, title=title, open_panel=open_panel).to_json()
    except Exception as e:
        return _tool_exception_json("windows_evidence_panel", e)


@mcp.tool(name="windows_project_remember")
def windows_project_remember(project_name: str, project_path: str, out_dir: str = "") -> str:
    """Remember a project name to local path mapping for future project briefings."""
    try:
        return _os_auto(out_dir).project_remember(project_name=project_name, project_path=project_path).to_json()
    except Exception as e:
        return _tool_exception_json("windows_project_remember", e)


@mcp.tool(name="windows_project_latest_briefing")
def windows_project_latest_briefing(
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
    """Create a local project briefing preview. If Lark sending is requested, delegate to the Codex -> Lark OS workflow."""
    try:
        recipients = json.loads(recipients_json or "[]")
        if isinstance(recipients, str):
            recipients = [recipients]
        if not isinstance(recipients, list):
            recipients = []
        clean_recipients = [str(x) for x in recipients if str(x).strip()]
        if clean_recipients:
            return _os_auto(out_dir).codex_project_briefing_to_lark(
                project_name=project_name,
                project_path=project_path,
                feature_query=feature_query,
                recipients=clean_recipients,
                since_days=since_days,
                wait_seconds=120,
                send_summary=True,
                remember=remember,
            ).to_json()
        return _os_auto(out_dir).project_latest_briefing(
            project_name=project_name,
            project_path=project_path,
            feature_query=feature_query,
            recipients=clean_recipients,
            since_days=since_days,
            send_summary=send_summary,
            open_report=open_report,
            use_qwen=use_qwen,
            remember=remember,
            max_files=max_files,
        ).to_json()
    except Exception as e:
        return _tool_exception_json("windows_project_latest_briefing", e)


@mcp.tool(name="windows_codex_project_briefing_to_lark")
def windows_codex_project_briefing_to_lark(
    project_name: str,
    project_path: str = "",
    feature_query: str = "",
    original_user_input: str = "",
    recipients_json: str = "[]",
    since_days: int = 3,
    wait_seconds: int = 90,
    send_summary: bool = False,
    remember: bool = True,
    out_dir: str = "",
) -> str:
    """Use the Codex desktop app to summarize a project, copy the result, and optionally send it to Lark recipients."""
    try:
        recipients = json.loads(recipients_json or "[]")
        if isinstance(recipients, str):
            recipients = [recipients]
        if not isinstance(recipients, list):
            recipients = []
        return _os_auto(out_dir).codex_project_briefing_to_lark(
            project_name=project_name,
            project_path=project_path,
            feature_query=feature_query,
            original_user_input=original_user_input,
            recipients=[str(x) for x in recipients],
            since_days=since_days,
            wait_seconds=wait_seconds,
            send_summary=send_summary,
            remember=remember,
        ).to_json()
    except Exception as e:
        return _tool_exception_json("windows_codex_project_briefing_to_lark", e)


@mcp.tool(name="windows_codex_lark_workflow_template")
def windows_codex_lark_workflow_template(
    project_name: str = "",
    project_path: str = "",
    directory_path: str = "",
    feature_query: str = "",
    bug_query: str = "",
    original_user_input: str = "",
    recipients_json: str = "[]",
    since_days: int = 3,
    wait_seconds: int = 90,
    send_summary: bool = False,
    remember: bool = True,
    out_dir: str = "",
) -> str:
    """Generic Codex -> Lark workflow template for project, directory, or bug-analysis briefings."""
    try:
        recipients = json.loads(recipients_json or "[]")
        if isinstance(recipients, str):
            recipients = [recipients]
        if not isinstance(recipients, list):
            recipients = []
        return _os_auto(out_dir).codex_lark_workflow_template(
            project_name=project_name,
            project_path=project_path,
            directory_path=directory_path,
            feature_query=feature_query,
            bug_query=bug_query,
            original_user_input=original_user_input,
            recipients=[str(x) for x in recipients],
            since_days=since_days,
            wait_seconds=wait_seconds,
            send_summary=send_summary,
            remember=remember,
        ).to_json()
    except Exception as e:
        return _tool_exception_json("windows_codex_lark_workflow_template", e)


@mcp.tool(name="windows_codex_lark_standard_demo")
def windows_codex_lark_standard_demo(
    project_name: str = "Jachin",
    project_path: str = "",
    recipients_json: str = "[]",
    since_days: int = 3,
    wait_seconds: int = 120,
    send_summary: bool = True,
    remember: bool = True,
    out_dir: str = "",
) -> str:
    """Standard leadership demo: Codex summarizes Jachin/project progress and sends it to Lark recipients with timeline evidence."""
    try:
        recipients = json.loads(recipients_json or "[]")
        if isinstance(recipients, str):
            recipients = [recipients]
        if not isinstance(recipients, list):
            recipients = []
        return _os_auto(out_dir).codex_lark_standard_demo(
            project_name=project_name,
            project_path=project_path,
            recipients=[str(x) for x in recipients],
            since_days=since_days,
            wait_seconds=wait_seconds,
            send_summary=send_summary,
            remember=remember,
        ).to_json()
    except Exception as e:
        return _tool_exception_json("windows_codex_lark_standard_demo", e)


@mcp.tool(name="windows_app_switch_matrix")
def windows_app_switch_matrix(apps_json: str = "", timeout: float = 4.0, out_dir: str = "") -> str:
    """Open or focus a set of common Windows apps and verify each focused window."""
    try:
        return _os_auto(out_dir).app_switch_matrix(apps_json=apps_json, timeout=timeout).to_json()
    except Exception as e:
        return _tool_exception_json("windows_app_switch_matrix", e)


@mcp.tool(name="windows_daily_office_briefing")
def windows_daily_office_briefing(
    recipients_json: str = "[]",
    paths_json: str = "",
    since_days: int = 1,
    send_summary: bool = False,
    open_report: bool = True,
    reveal_key_file: bool = True,
    max_files: int = 60,
    out_dir: str = "",
) -> str:
    """Create a cross-app daily office briefing from windows, files, and system status; optionally send it in Lark."""
    try:
        recipients = json.loads(recipients_json or "[]")
        if isinstance(recipients, str):
            recipients = [recipients]
        if not isinstance(recipients, list):
            recipients = []
        return _os_auto(out_dir).daily_office_briefing(
            recipients=[str(x) for x in recipients],
            paths_json=paths_json,
            since_days=since_days,
            send_summary=send_summary,
            open_report=open_report,
            reveal_key_file=reveal_key_file,
            max_files=max_files,
        ).to_json()
    except Exception as e:
        return _tool_exception_json("windows_daily_office_briefing", e)


@mcp.tool(name="windows_file_bridge_to_app")
def windows_file_bridge_to_app(
    file_path: str = "",
    app_name: str = "",
    paths_json: str = "",
    since_days: int = 1,
    open_dialog_hotkey: str = "ctrl+o",
    out_dir: str = "",
) -> str:
    """Find/reveal a file, open the target app, and submit the file path to its file dialog."""
    try:
        return _os_auto(out_dir).file_bridge_to_app(
            file_path=file_path,
            app_name=app_name,
            paths_json=paths_json,
            since_days=since_days,
            open_dialog_hotkey=open_dialog_hotkey,
        ).to_json()
    except Exception as e:
        return _tool_exception_json("windows_file_bridge_to_app", e)


@mcp.tool(name="windows_os_mission_execute")
def windows_os_mission_execute(goal: str = "", steps_json: str = "", dry_run: bool = False, confirm_send: bool = False, out_dir: str = "") -> str:
    """Execute a declarative cross-app OS mission with step-by-step evidence."""
    try:
        return _os_auto(out_dir).os_mission_execute(goal=goal, steps_json=steps_json, dry_run=dry_run, confirm_send=confirm_send).to_json()
    except Exception as e:
        return _tool_exception_json("windows_os_mission_execute", e)


@mcp.tool(name="windows_file_find")
def windows_file_find(root: str, pattern: str = "*", max_results: int = 100, include_dirs: bool = True, out_dir: str = "") -> str:
    """Find files/folders under a root path."""
    try:
        return _os_auto(out_dir).file_find(root=root, pattern=pattern, max_results=max_results, include_dirs=include_dirs).to_json()
    except Exception as e:
        return _tool_exception_json("file_find", e)


@mcp.tool(name="windows_file_copy")
def windows_file_copy(source: str, destination: str, overwrite: bool = False, confirm: bool = False, allow_dangerous: bool = False, out_dir: str = "") -> str:
    """Copy a file. Existing destination requires confirmation or dangerous bypass."""
    try:
        return _os_auto(out_dir).file_copy(source=source, destination=destination, overwrite=overwrite, confirm=confirm, allow_dangerous=allow_dangerous).to_json()
    except Exception as e:
        return _tool_exception_json("file_copy", e)


@mcp.tool(name="windows_file_move")
def windows_file_move(source: str, destination: str, overwrite: bool = False, confirm: bool = False, allow_dangerous: bool = False, out_dir: str = "") -> str:
    """Move a file/folder. Existing destination requires confirmation or dangerous bypass."""
    try:
        return _os_auto(out_dir).file_move(source=source, destination=destination, overwrite=overwrite, confirm=confirm, allow_dangerous=allow_dangerous).to_json()
    except Exception as e:
        return _tool_exception_json("file_move", e)


@mcp.tool(name="windows_file_rename")
def windows_file_rename(path: str, new_name: str, overwrite: bool = False, confirm: bool = False, allow_dangerous: bool = False, out_dir: str = "") -> str:
    """Rename a file/folder within its parent directory."""
    try:
        return _os_auto(out_dir).file_rename(path=path, new_name=new_name, overwrite=overwrite, confirm=confirm, allow_dangerous=allow_dangerous).to_json()
    except Exception as e:
        return _tool_exception_json("file_rename", e)


@mcp.tool(name="windows_file_delete_with_confirm")
def windows_file_delete_with_confirm(path: str, confirm: bool = False, allow_dangerous: bool = False, out_dir: str = "") -> str:
    """Delete a file/folder only after explicit confirm or configured dangerous bypass."""
    try:
        return _os_auto(out_dir).file_delete_with_confirm(path=path, confirm=confirm, allow_dangerous=allow_dangerous).to_json()
    except Exception as e:
        return _tool_exception_json("file_delete_with_confirm", e)


@mcp.tool(name="windows_file_open")
def windows_file_open(path: str, out_dir: str = "") -> str:
    """Open a file/folder with the Windows default app."""
    try:
        return _os_auto(out_dir).file_open(path=path).to_json()
    except Exception as e:
        return _tool_exception_json("file_open", e)


@mcp.tool(name="windows_file_reveal_in_explorer")
def windows_file_reveal_in_explorer(path: str, out_dir: str = "") -> str:
    """Reveal and select a file/folder in Windows Explorer."""
    try:
        return _os_auto(out_dir).file_reveal_in_explorer(path=path).to_json()
    except Exception as e:
        return _tool_exception_json("file_reveal_in_explorer", e)


@mcp.tool(name="windows_file_attach_to_app")
def windows_file_attach_to_app(file_path: str, app_name: str = "", open_dialog_hotkey: str = "ctrl+o", out_dir: str = "") -> str:
    """Submit a file path to the active/opened app's file dialog."""
    try:
        return _os_auto(out_dir).file_attach_to_app(file_path=file_path, app_name=app_name, open_dialog_hotkey=open_dialog_hotkey).to_json()
    except Exception as e:
        return _tool_exception_json("file_attach_to_app", e)


@mcp.tool(name="windows_folder_summarize")
def windows_folder_summarize(folder: str, max_depth: int = 2, max_entries: int = 300, out_dir: str = "") -> str:
    """Summarize a folder tree with counts, sizes, extensions, and sample entries."""
    try:
        return _os_auto(out_dir).folder_summarize(folder=folder, max_depth=max_depth, max_entries=max_entries).to_json()
    except Exception as e:
        return _tool_exception_json("folder_summarize", e)


@mcp.tool(name="windows_file_dialogs_smoke")
def windows_file_dialogs_smoke(out_dir: str = "") -> str:
    """Exercise Windows open-file and save-file dialogs with UIA/keyboard fallback."""
    try:
        return _os_auto(out_dir).file_open_save_dialogs().to_json()
    except Exception as e:
        return _tool_exception_json("file_dialogs", e)


@mcp.tool(name="windows_browser_address_download_prompt")
def windows_browser_address_download_prompt(url: str = "", out_dir: str = "") -> str:
    """Open a browser, use the address bar, attempt a download, and dismiss prompts."""
    try:
        return _os_auto(out_dir).browser_address_download_prompt(url=url).to_json()
    except Exception as e:
        return _tool_exception_json("browser", e)


@mcp.tool(name="windows_popup_action")
def windows_popup_action(action: str = "confirm", out_dir: str = "") -> str:
    """Open a native popup and confirm/cancel/close it."""
    try:
        return _os_auto(out_dir).popup_action(action=action).to_json()
    except Exception as e:
        return _tool_exception_json("popup", e)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

