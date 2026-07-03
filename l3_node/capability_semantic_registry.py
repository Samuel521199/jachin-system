"""Semantic capability registry for MCP/Skill routing.

This module describes what a capability can *do*, independent from the code
that executes it.  The descriptors are intentionally small and serializable so
they can be indexed, shown in evidence, and extended by MCP/Skill metadata.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class CapabilityDescriptor:
    id: str
    domain: str
    actions: list[str]
    objects: list[str]
    inputs: list[str]
    risk: str
    description: str
    examples: list[str] = field(default_factory=list)
    workflow_id: str = ""
    task_type: str = ""
    tool_chain: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    source: str = "builtin"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def searchable_text(self) -> str:
        return " ".join(
            [
                self.id,
                self.domain,
                self.workflow_id,
                self.task_type,
                self.description,
                " ".join(self.actions),
                " ".join(self.objects),
                " ".join(self.inputs),
                " ".join(self.examples),
            ]
        )


BUILTIN_CAPABILITIES: tuple[CapabilityDescriptor, ...] = (
    CapabilityDescriptor(
        id="mcp:windows_codex_lark_workflow_template",
        domain="os_assistant.project_delivery",
        actions=["summarize", "analyze", "deliver", "send_message", "verify"],
        objects=["local_project", "directory", "codebase", "lark_contact", "lark_group"],
        inputs=["project_name", "project_path", "recipients", "since_days", "feature_query"],
        risk="external_effect",
        description="Use Codex to inspect a local project or directory, generate a briefing, send it through Lark, and verify visually.",
        examples=[
            "summarize recent Jachin work with Codex and send it to Neil",
            "inspect this project and send a short briefing to Vivian",
            "ask Codex to analyze the OS assistant workflow and send it to a test group",
        ],
        workflow_id="codex_project_briefing_to_lark",
        task_type="project_briefing_delivery",
        tool_chain=["project_memory", "windows_codex_lark_workflow_template", "windows_lark_send_message", "ocr_verify"],
        evidence=["router_evidence", "codex_output", "report_md", "lark_screenshot", "ocr_check"],
    ),
    CapabilityDescriptor(
        id="mcp:windows_codex_ask_lark_send",
        domain="os_assistant.agent_delivery",
        actions=["ask_agent", "capture_reply", "send_message", "verify"],
        objects=["codex_chat", "codex_reply", "lark_contact", "lark_group"],
        inputs=["feature_query", "recipients"],
        risk="external_effect",
        description="Ask Codex a user-provided question, validate the reply, send it through Lark, and verify visually.",
        examples=[
            "ask Codex what happened in AI this week and send the answer to Vivian",
            "let Codex answer this question and send the reply to Vivian via Lark",
        ],
        workflow_id="codex_ask_lark_send",
        task_type="codex_ask_lark_send",
        tool_chain=["windows_open_app", "codex_reply_capture", "windows_lark_send_message", "ocr_verify"],
        evidence=["codex_reply", "reply_validation", "lark_screenshot", "ocr_check"],
    ),
    CapabilityDescriptor(
        id="mcp:windows_lark_send_message",
        domain="communication.lark",
        actions=["send_message", "notify", "search_contact", "verify"],
        objects=["lark_contact", "lark_group", "message"],
        inputs=["recipients", "message"],
        risk="external_effect",
        description="Open Lark, find one or more contacts or groups, send a message, and verify the sent result.",
        examples=["send hello to Vivian", "notify Neil that testing is done", "send this message to a Lark group"],
        workflow_id="windows_lark_message_send",
        task_type="lark_message_send",
        evidence=["recipient_visible", "message_visible", "screenshot", "ocr_check"],
    ),
    CapabilityDescriptor(
        id="mcp:windows_calculator_calculate",
        domain="os_assistant.calculator",
        actions=["calculate", "open_app", "verify"],
        objects=["windows_calculator", "arithmetic_expression"],
        inputs=["expression", "expected"],
        risk="low",
        description="Open Windows Calculator, type an arithmetic expression, copy or read the result, and verify it visually.",
        examples=["calculate 20+70 in Calculator", "open Windows Calculator and compute 91+9", "use Calculator for 8*7"],
        workflow_id="windows_calculator_calculate",
        task_type="calculator_calculate",
        evidence=["active_window", "expression", "result", "screenshot", "ocr_check"],
    ),
    CapabilityDescriptor(
        id="mcp:windows_open_app",
        domain="os_assistant.app_control",
        actions=["open_app", "switch_window", "focus", "verify"],
        objects=["windows_app", "window"],
        inputs=["app_name"],
        risk="low",
        description="Open or focus a Windows app and verify the foreground window.",
        examples=["open Lark", "switch to browser", "open Calculator", "focus Codex"],
        workflow_id="windows_app_control",
        task_type="app_control",
        evidence=["active_window", "window_title", "screenshot"],
    ),
    CapabilityDescriptor(
        id="mcp:windows_system_status",
        domain="os_assistant.system_status",
        actions=["inspect", "report", "diagnose"],
        objects=["disk", "network", "battery", "process", "cpu", "memory"],
        inputs=[],
        risk="low",
        description="Inspect Windows disk, network, battery, process, CPU, and memory status.",
        examples=["check system status", "show disk and memory usage", "diagnose current computer health"],
        workflow_id="windows_system_status_report",
        task_type="system_status_report",
        evidence=["system_status", "process_snapshot", "evidence_json"],
    ),
    CapabilityDescriptor(
        id="mcp:windows_file_bridge_to_app",
        domain="os_assistant.file_to_app",
        actions=["find_file", "attach", "upload", "verify"],
        objects=["local_file", "folder", "app_upload_field"],
        inputs=["file_path", "app_name"],
        risk="medium",
        description="Find or resolve a local file, attach or upload it into a target app, then verify the filename.",
        examples=["send today's report to Lark", "upload this file in the browser", "attach the newest document to a message"],
        workflow_id="windows_file_to_app_bridge",
        task_type="file_to_app",
        evidence=["file_stat", "upload_target", "filename_visible", "ocr_check"],
    ),
    CapabilityDescriptor(
        id="mcp:windows_file_find",
        domain="os_assistant.file_ops",
        actions=["find_file", "search", "list_recent", "classify"],
        objects=["local_file", "folder", "desktop", "downloads", "documents"],
        inputs=["query", "directory_path", "since_days"],
        risk="low",
        description="Search Windows files and folders, including recent or project-related files.",
        examples=["find files added today", "search desktop logs", "search Jachin related docs"],
        workflow_id="windows_file_find",
        task_type="file_find",
        evidence=["file_paths", "file_stat", "evidence_json"],
    ),
    CapabilityDescriptor(
        id="mcp:windows_file_delete_with_confirm",
        domain="os_assistant.file_ops",
        actions=["delete_file", "confirm", "verify"],
        objects=["local_file", "folder"],
        inputs=["file_path", "confirm"],
        risk="high",
        description="Delete local files only with confirmation and evidence.",
        examples=["delete this temporary file", "clean old logs after confirmation"],
        workflow_id="windows_file_delete_with_confirm",
        task_type="file_delete",
        evidence=["confirmation", "file_path", "delete_result"],
    ),
    CapabilityDescriptor(
        id="mcp:windows_app_switch_matrix",
        domain="os_assistant.app_control",
        actions=["open_app", "switch_window", "verify_matrix"],
        objects=["windows_app", "window"],
        inputs=["apps"],
        risk="low",
        description="Open and switch across multiple Windows apps and verify each foreground window.",
        examples=["open multiple apps and verify focus", "test switching among Codex, Lark, browser, and Explorer"],
        workflow_id="windows_app_switch_matrix",
        task_type="app_switch_matrix",
        evidence=["active_window", "screenshot", "timing"],
    ),
    CapabilityDescriptor(
        id="mcp:create_presentation",
        domain="office.presentation",
        actions=["create_presentation", "generate_slides", "format"],
        objects=["ppt", "slides", "presentation"],
        inputs=["topic", "outline", "style"],
        risk="low",
        description="Create a PowerPoint or slide deck from a topic, outline, or document.",
        examples=["create a project report PPT", "turn this content into slides", "generate slides"],
        workflow_id="office_powerpoint_create",
        task_type="presentation_create",
        evidence=["presentation_id", "ppt_path"],
    ),
    CapabilityDescriptor(
        id="core:akshare_a_share_hist",
        domain="finance.a_share",
        actions=["analyze_market", "fetch_price", "report"],
        objects=["stock", "a_share", "market_data"],
        inputs=["symbol", "date_range"],
        risk="read_only",
        description="Fetch A-share market data for stock analysis.",
        examples=["analyze an A-share stock", "fetch recent market data for 600519"],
        workflow_id="a_share_analysis",
        task_type="finance_analysis",
        evidence=["market_data", "source_tool"],
    ),
)

def _canonical_tool_id(raw: str) -> str:
    s = str(raw or "").strip()
    if not s:
        return ""
    if s.startswith(("mcp:", "core:", "util:", "sys:", "jpp:")):
        return s
    return f"mcp:{s}"


def _first_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _extract_tool_id(tool: dict[str, Any]) -> str:
    fn = tool.get("function") if isinstance(tool.get("function"), dict) else {}
    return _canonical_tool_id(
        _first_text(
            tool.get("id"),
            tool.get("tool_id"),
            tool.get("name"),
            fn.get("name"),
            tool.get("_plugin_id"),
        )
    )


def _extract_description(tool: dict[str, Any]) -> str:
    fn = tool.get("function") if isinstance(tool.get("function"), dict) else {}
    return _first_text(
        tool.get("desc"),
        tool.get("description"),
        tool.get("label"),
        fn.get("description"),
        tool.get("_name"),
        tool.get("name"),
        tool.get("id"),
    )


def _extract_params_from_schema(schema: Any) -> list[str]:
    if not isinstance(schema, dict):
        return []
    props = schema.get("properties")
    if isinstance(props, dict):
        return [str(k) for k in props.keys() if str(k).strip()]
    required = schema.get("required")
    if isinstance(required, list):
        return [str(x) for x in required if str(x).strip()]
    return []


def _extract_inputs(tool: dict[str, Any]) -> list[str]:
    raw_params = tool.get("params")
    if isinstance(raw_params, list):
        return [str(x.get("name") if isinstance(x, dict) else x) for x in raw_params if str(x).strip()]
    if isinstance(raw_params, dict):
        return [str(k) for k in raw_params.keys() if str(k).strip()]

    for key in ("parameters", "input_schema", "inputSchema", "schema"):
        got = _extract_params_from_schema(tool.get(key))
        if got:
            return got
    fn = tool.get("function") if isinstance(tool.get("function"), dict) else {}
    got = _extract_params_from_schema(fn.get("parameters"))
    if got:
        return got
    schema = tool.get("schema") if isinstance(tool.get("schema"), dict) else {}
    got = _extract_params_from_schema(schema.get("input"))
    return got


def _split_words(text: str) -> list[str]:
    raw = str(text or "")
    words = re.findall(r"[A-Za-z][A-Za-z0-9]+|[\u4e00-\u9fff]{2,}", raw)
    out: list[str] = []
    seen: set[str] = set()
    for word in words:
        w = word.strip().lower()
        if len(w) < 2 or w in seen:
            continue
        seen.add(w)
        out.append(word)
    return out[:18]


def _infer_domain(tool_id: str, text: str) -> str:
    s = f"{tool_id} {text}".lower()
    if any(x in s for x in ("lark", "feishu", "flybook", "message", "chat", "notify")):
        return "communication.lark"
    if any(x in s for x in ("codex", "project", "briefing", "summary", "repo", "codebase")):
        return "os_assistant.project"
    if any(x in s for x in ("ppt", "powerpoint", "presentation", "slide", "slides")):
        return "office.presentation"
    if any(x in s for x in ("file", "folder", "fs_", "workspace", "read_file", "write_file", "path")):
        return "os_assistant.file_ops"
    if any(x in s for x in ("window", "app", "open", "switch", "uia", "desktop", "calculator", "notepad", "browser", "explorer")):
        return "os_assistant.app_control"
    if any(x in s for x in ("system", "disk", "network", "battery", "process", "cpu", "memory")):
        return "os_assistant.system_status"
    if any(x in s for x in ("sqlite", "sql", "database", "db_", "table", "record")):
        return "database"
    if any(x in s for x in ("stock", "akshare", "finance", "market")):
        return "finance"
    if tool_id.startswith("jpp:"):
        return "skill.plugin"
    if tool_id.startswith("core:"):
        return "core.native"
    return "dynamic.tool"


def _infer_actions(tool_id: str, text: str) -> list[str]:
    s = f"{tool_id} {text}".lower()
    mapping = (
        ("send_message", ("send", "message", "notify", "chat", "recipient")),
        ("summarize", ("summary", "briefing", "summarize", "report")),
        ("open_app", ("open", "switch", "focus", "launch", "activate")),
        ("find_file", ("find", "search", "list", "recent", "locate")),
        ("read", ("read", "get", "fetch", "query")),
        ("write", ("write", "create", "update", "append")),
        ("delete", ("delete", "remove")),
        ("verify", ("verify", "ocr", "screenshot", "validate")),
        ("create_presentation", ("presentation", "slide", "ppt")),
    )
    out = [action for action, keys in mapping if any(k in s for k in keys)]
    if out:
        return out[:8]
    name = tool_id.split(":", 1)[-1].replace("_", " ")
    return [name]


def _infer_objects(tool_id: str, text: str) -> list[str]:
    s = f"{tool_id} {text}".lower()
    mapping = (
        ("lark_contact", ("lark", "feishu", "flybook", "contact", "recipient", "chat")),
        ("local_project", ("project", "codebase", "repo")),
        ("windows_app", ("app", "window", "desktop", "calculator", "browser", "explorer")),
        ("local_file", ("file", "folder", "workspace", "path")),
        ("system_status", ("system", "disk", "network", "battery", "process", "cpu", "memory")),
        ("presentation", ("ppt", "presentation", "slide", "powerpoint")),
        ("database", ("sqlite", "sql", "database", "table", "record")),
    )
    return [obj for obj, keys in mapping if any(k in s for k in keys)][:8]


def _infer_risk(tool_id: str, text: str) -> str:
    s = f"{tool_id} {text}".lower()
    if any(x in s for x in ("delete", "remove", "overwrite", "drop", "truncate")):
        return "high"
    if any(x in s for x in ("send", "message", "notify", "post", "write", "create", "update", "move", "rename")):
        return "external_effect"
    if any(x in s for x in ("read", "list", "get", "status", "search", "query", "fetch")):
        return "read_only"
    return "unknown"

def _metadata_override(tool: dict[str, Any]) -> dict[str, Any]:
    for key in ("capability", "capabilities", "jachin_capability", "x_jachin_capability", "metadata"):
        value = tool.get(key)
        if isinstance(value, dict):
            return value
    return {}


def descriptor_for_tool(tool: dict[str, Any]) -> CapabilityDescriptor | None:
    tid = _extract_tool_id(tool)
    if not tid:
        return None
    for item in BUILTIN_CAPABILITIES:
        if item.id == tid or item.id.removeprefix("mcp:") == tid.removeprefix("mcp:"):
            inputs = _extract_inputs(tool)
            if not inputs:
                return item
            return CapabilityDescriptor(
                **{
                    **item.to_dict(),
                    "inputs": inputs or item.inputs,
                    "source": "builtin+tool_metadata",
                    "metadata": {
                        **item.metadata,
                        "label": tool.get("label"),
                        "name": tool.get("name"),
                        "plugin_id": tool.get("_plugin_id"),
                        "item_id": tool.get("_item_id"),
                        "input_count": len(inputs),
                    },
                }
            )
    desc = _extract_description(tool)
    if not desc:
        return None
    inputs = _extract_inputs(tool)
    override = _metadata_override(tool)
    text = " ".join([tid, desc, " ".join(inputs), json.dumps(override, ensure_ascii=False, default=str)])
    domain = str(override.get("domain") or _infer_domain(tid, text))
    actions = list(override.get("actions") or _infer_actions(tid, text))
    objects = list(override.get("objects") or _infer_objects(tid, text))
    risk = str(override.get("risk") or _infer_risk(tid, text))
    workflow_id = str(override.get("workflow_id") or tid.removeprefix("mcp:"))
    task_type = str(override.get("task_type") or "")
    examples = [str(x) for x in override.get("examples", [])] if isinstance(override.get("examples"), list) else []
    evidence = [str(x) for x in override.get("evidence", [])] if isinstance(override.get("evidence"), list) else []
    return CapabilityDescriptor(
        id=tid,
        domain=domain,
        actions=actions,
        objects=objects,
        inputs=inputs,
        risk=risk,
        description=desc,
        examples=examples,
        workflow_id=workflow_id,
        task_type=task_type,
        evidence=evidence,
        source="tool_metadata",
        metadata={
            "label": tool.get("label"),
            "name": tool.get("name"),
            "plugin_id": tool.get("_plugin_id"),
            "item_id": tool.get("_item_id"),
            "input_count": len(inputs),
            "has_capability_override": bool(override),
        },
    )


def build_capability_registry(tools: list[dict[str, Any]] | None = None) -> list[CapabilityDescriptor]:
    tools = tools or []
    out: list[CapabilityDescriptor] = []
    seen: set[str] = set()
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        item = descriptor_for_tool(tool)
        if not item:
            continue
        key = item.id.lower()
        if key not in seen:
            seen.add(key)
            out.append(item)
    if not tools:
        for item in BUILTIN_CAPABILITIES:
            key = item.id.lower()
            if key not in seen:
                seen.add(key)
                out.append(item)
    return out


def capability_registry_as_dicts(tools: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    return [item.to_dict() for item in build_capability_registry(tools)]



