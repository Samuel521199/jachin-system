"""Deterministic OS mission routing for high-value local desktop workflows.

This router is intentionally thin: it parses a small set of natural-language
mission shapes, then delegates execution to the Windows UIA MCP skill layer.
It must not know how Codex or Lark are operated internally.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from l3_node.capability_router import choose_capability_route
from l3_node.capability_matcher import match_task_to_capability
from l3_node.clarification_policy import decide_clarification
from l3_node.mission_intent_schema import CapabilityRoute, ClarificationDecision, MissionIntent, MissionTaskType
from l3_node.mission_control_center import (
    capability_route_from_dict,
    clear_pending_mission,
    is_cancel_command,
    is_confirmation_command,
    load_pending_mission,
    mission_intent_from_dict,
    patch_intent_from_text,
    save_pending_mission,
    should_hold_for_confirmation,
)
from l3_node.mission_memory_center import apply_memory_to_intent, record_successful_mission
from l3_node.mission_preview import build_mission_preview, format_preview_for_chat
from l3_node.mission_runtime import build_plan_preview, execute_with_retry
from l3_node.mission_template_library import MissionTemplate, select_mission_template
from l3_node.project_memory import remember_project, resolve_project
from l3_node.semantic_intent_engine import parse_semantic_intent
from l3_node.workflow_composer import compose_workflow


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CodexLarkMission:
    project_name: str = ""
    project_path: str = ""
    directory_path: str = ""
    feature_query: str = ""
    bug_query: str = ""
    recipients: tuple[str, ...] = ()
    since_days: int = 3
    wait_seconds: int = 120


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def _has_codex_lark_tool(tools: list[dict[str, Any]]) -> bool:
    ids = {str(t.get("id") or t.get("name") or "").strip() for t in tools if isinstance(t, dict)}
    return bool(
        {
            "mcp:windows_codex_lark_workflow_template",
            "mcp:windows_codex_project_briefing_to_lark",
            "windows_codex_lark_workflow_template",
            "windows_codex_project_briefing_to_lark",
        }
        & ids
    )


def _strip_recipient_prefix(text: str) -> str:
    s = str(text or "").strip()
    s = re.sub(r"^(?:群聊|群|单聊|联系人|同事)\s*[:：]\s*", "", s)
    return s.strip(" \t\r\n。.!！?？")


def _split_recipients(text: str) -> tuple[str, ...]:
    raw = _strip_recipient_prefix(text)
    raw = re.sub(r"(?:这次|本次)?(?:不要|不)\s*(?:发给|发送给|发到|发送到).*$", "", raw).strip()
    parts = re.split(r"\s*(?:、|，|,|；|;|和|与|及|以及|and)\s*", raw)
    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        name = _strip_recipient_prefix(part)
        name = re.sub(r"(?:都)?(?:发送|发)(?:同样的)?(?:消息)?$", "", name).strip()
        if not name:
            continue
        key = name.lower()
        if key not in seen:
            seen.add(key)
            out.append(name)
    return tuple(out)


def _extract_recipients(text: str) -> tuple[str, ...]:
    matches = list(re.finditer(r"(?:发给|发送给|发到|发送到|发往|转给)\s*(.+)$", text, re.I))
    if not matches:
        return ()
    tail = matches[-1].group(1)
    tail = re.split(r"(?:，然后|, then|然后再|再\s|并\s*$)", tail, maxsplit=1)[0]
    return _split_recipients(tail)


def _extract_since_days(text: str) -> int:
    m = re.search(r"(?:最近|近|过去)\s*([0-9一二三四五六七八九十两]+)\s*天", text)
    if m:
        raw = m.group(1)
        zh = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
        try:
            return max(1, min(30, int(raw)))
        except ValueError:
            return max(1, min(30, zh.get(raw, 3)))
    if re.search(r"(?:最近|近|过去)\s*(?:一)?周", text):
        return 7
    return 3


def _extract_windows_path(text: str) -> str:
    quoted = re.search(r"[`\"“']([A-Za-z]:[\\/][^`\"”']+)[`\"”']", text)
    if quoted:
        return quoted.group(1).strip()
    m = re.search(r"([A-Za-z]:[\\/][^\s，。；;,]+(?:[\\/][^\s，。；;,]+)*)", text)
    return m.group(1).strip() if m else ""


def _extract_project_name(text: str, project_path: str) -> str:
    if project_path:
        return Path(project_path).name or "project"
    patterns = (
        r"(?:让|请)?\s*Codex\s*(?:分析|总结|查看|搜索)?\s*([A-Za-z][A-Za-z0-9_.-]{1,80}|[\u4e00-\u9fffA-Za-z0-9_.-]{2,80})\s*的",
        r"总结\s*([A-Za-z][A-Za-z0-9_.-]{1,80}|[\u4e00-\u9fffA-Za-z0-9_.-]{2,80})\s*(?:最近|近|过去|项目|最新|的)",
        r"([A-Za-z][A-Za-z0-9_.-]{1,80})\s*(?:项目)?(?:最近|最新).*(?:发给|发送给|发到|发送到)",
    )
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if not m:
            continue
        name = str(m.group(1) or "").strip()
        if name and name.lower() not in {"codex", "windows", "lark"}:
            return name
    return ""


def _extract_feature_query(text: str, project_name: str, project_path: str) -> str:
    head = re.split(r"(?:发给|发送给|发到|发送到|发往|转给)", text, maxsplit=1)[0]
    feature = head
    if project_path:
        feature = feature.replace(project_path, "项目")
    if project_name:
        feature = re.sub(re.escape(project_name), "项目", feature, flags=re.I)
    feature = re.sub(r"^(?:请|帮我|麻烦)?\s*(?:让\s*)?Codex\s*", "", feature, flags=re.I)
    feature = re.sub(r"^(?:请|帮我|麻烦)?\s*(?:总结|分析|查看|搜索)\s*", "", feature)
    feature = re.sub(r"(?:最近|近|过去)\s*[0-9一二三四五六七八九十两]+\s*天", "", feature)
    feature = feature.strip(" ，,。；;")
    if re.search(r"一条一条|按条|条列|bullet|list", text, re.I):
        feature = (feature + "；请按条列输出").strip("；")
    return feature or "latest project progress"


def parse_codex_lark_mission(user_input: str) -> CodexLarkMission | None:
    text = str(user_input or "").strip()
    if not text:
        return None
    compact = _compact(text)
    if not re.search(r"(发给|发送给|发到|发送到|发往|转给)", text):
        return None
    if "lark" not in compact and "飞书" not in text and not re.search(r"(发给|发送给|发到|发送到)", text):
        return None
    if not re.search(r"(总结|分析|搜索|查看|最新进展|做了什么|bug|workflow|代码|项目|目录|文件夹|codex)", text, re.I):
        return None

    recipients = _extract_recipients(text)
    if not recipients:
        return None
    project_path = _extract_windows_path(text)
    project_name = _extract_project_name(text, project_path)
    if not project_name and not project_path:
        return None
    bug_query = ""
    if re.search(r"\bbug\b|问题|报错|异常", text, re.I):
        bug_query = _extract_feature_query(text, project_name, project_path)
    return CodexLarkMission(
        project_name=project_name,
        project_path=project_path,
        feature_query=_extract_feature_query(text, project_name, project_path),
        bug_query=bug_query,
        recipients=recipients,
        since_days=_extract_since_days(text),
    )


def _format_codex_lark_result(result_text: str, mission: CodexLarkMission) -> str:
    try:
        data = json.loads(result_text)
    except Exception:
        return result_text
    ok = bool(data.get("ok"))
    detail = str(data.get("detail") or "")
    evidence = data.get("evidence") if isinstance(data.get("evidence"), dict) else data
    evidence_path = str(evidence.get("evidence_path") or data.get("evidence_path") or "").strip()
    panel_path = str(evidence.get("evidence_panel_path") or data.get("evidence_panel_path") or "").strip()
    report_path = str(evidence.get("report_path") or data.get("report_path") or "").strip()
    recipients = "、".join(mission.recipients)
    target = mission.project_name or mission.project_path or "目标项目"
    if ok:
        lines = [f"已通过 Codex 总结 {target}，并发送给 {recipients}。"]
    else:
        lines = [f"Codex -> Lark 工作流未完成：{detail or 'unknown'}。"]
    if evidence_path:
        lines.append(f"Evidence: {evidence_path}")
    if panel_path:
        lines.append(f"Evidence Panel: {panel_path}")
    if report_path:
        lines.append(f"Report: {report_path}")
    if not ok:
        lines.append("我没有把未通过校验的内容当作成功发送。")
    return "\n".join(lines)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _router_evidence_path() -> Path:
    root = Path(os.environ.get("JACHIN_OS_MISSION_EVIDENCE_DIR") or (_repo_root() / "output" / "os_mission_router"))
    root.mkdir(parents=True, exist_ok=True)
    return root / f"os_mission_router_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.evidence.json"


def _parse_tool_result(result_text: str) -> dict[str, Any]:
    try:
        data = json.loads(result_text)
        return data if isinstance(data, dict) else {"raw": data}
    except Exception:
        return {"raw_text": result_text}


def _write_router_evidence(
    *,
    intent: MissionIntent,
    route: CapabilityRoute,
    clarification: ClarificationDecision,
    status: str,
    parser_meta: dict[str, Any] | None = None,
    memory_evidence: dict[str, Any] | None = None,
    template: MissionTemplate | None = None,
    mission_preview: dict[str, Any] | None = None,
    capability_semantic: dict[str, Any] | None = None,
    workflow_composition: dict[str, Any] | None = None,
    control: dict[str, Any] | None = None,
    plan_preview: dict[str, Any] | None = None,
    tool_result: dict[str, Any] | None = None,
    attempts: list[dict[str, Any]] | None = None,
    retry: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
) -> str:
    path = _router_evidence_path()
    payload = {
        "task": "os_mission_router",
        "ok": status == "done",
        "detail": status,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "evidence_path": str(path),
        "intent": intent.to_dict(),
        "parser": parser_meta or {},
        "memory": memory_evidence or {},
        "template": template.to_dict() if template else {},
        "mission_preview": mission_preview or {},
        "capability_semantic": capability_semantic or {},
        "workflow_composition": workflow_composition or {},
        "control": control or {},
        "plan_preview": plan_preview or {},
        "route": route.to_dict(),
        "clarification": clarification.to_dict(),
        "tool_result": tool_result or {},
        "attempts": attempts or [],
        "retry": retry or {},
        "metrics": metrics or {},
        "timeline": [
            {
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "stage": "parse_intent",
                "status": "done",
                "detail": intent.task_type.value,
                "evidence": {
                    "confidence": intent.confidence,
                    "slots": intent.slots.to_dict(),
                    "missing_slots": intent.missing_slots,
                    "parser": parser_meta or {},
                },
            },
            {
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "stage": "route_capability",
                "status": "done" if route.ok else "failed",
                "detail": route.reason,
                "evidence": route.to_dict(),
            },
            {
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "stage": "semantic_capability_match",
                "status": "done" if (capability_semantic or {}).get("selected") else "check",
                "detail": str((capability_semantic or {}).get("reason") or ""),
                "evidence": capability_semantic or {},
            },
            {
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "stage": "workflow_composer",
                "status": "done" if workflow_composition else "check",
                "detail": str((workflow_composition or {}).get("workflow_id") or ""),
                "evidence": workflow_composition or {},
            },
            {
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "stage": "memory_center",
                "status": "done",
                "detail": "memory_applied",
                "evidence": memory_evidence or {},
            },
            {
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "stage": "select_template",
                "status": "done" if template else "check",
                "detail": template.id if template else "no_template",
                "evidence": template.to_dict() if template else {},
            },
            {
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "stage": "clarification_policy",
                "status": "check" if clarification.should_ask else "done",
                "detail": clarification.reason,
                "evidence": clarification.to_dict(),
            },
        ],
    }
    if mission_preview is not None:
        payload["timeline"].append(
            {
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "stage": "task_preview",
                "status": "check" if mission_preview.get("requires_confirmation") else "done",
                "detail": str(mission_preview.get("title") or mission_preview.get("summary") or ""),
                "evidence": mission_preview,
            }
        )
    if control is not None:
        payload["timeline"].append(
            {
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "stage": "user_confirmation",
                "status": str(control.get("status") or "check"),
                "detail": str(control.get("decision") or control.get("mode") or ""),
                "evidence": control,
            }
        )
    if plan_preview is not None:
        payload["timeline"].append(
            {
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "stage": "plan_preview",
                "status": "check" if plan_preview.get("requires_confirmation") else "done",
                "detail": str(plan_preview.get("summary") or ""),
                "evidence": plan_preview,
            }
        )
    if tool_result is not None:
        payload["timeline"].append(
            {
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "stage": "execute_workflow",
                "status": "done" if bool(tool_result.get("ok")) else "failed",
                "detail": str(tool_result.get("detail") or tool_result.get("task") or ""),
                "evidence": {"tool_result": tool_result, "attempts": attempts or [], "retry": retry or {}, "metrics": metrics or {}},
            }
        )
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def _hydrate_project_memory(intent: MissionIntent) -> None:
    if intent.task_type != MissionTaskType.PROJECT_BRIEFING_DELIVERY:
        return
    if intent.slots.project_path:
        return
    root, evidence = resolve_project(intent.slots.project_name)
    if not root:
        intent.reasoning.append(f"project_memory_miss:{evidence.get('error') or 'not_found'}")
        return
    intent.slots.project_path = str(root)
    if not intent.slots.project_name:
        entry = evidence.get("memory_entry") if isinstance(evidence.get("memory_entry"), dict) else {}
        intent.slots.project_name = str(entry.get("name") or root.name)
    intent.missing_slots = [slot for slot in intent.missing_slots if slot != "project"]
    intent.confidence = min(0.98, intent.confidence + 0.08)
    intent.reasoning.append(f"project_memory_hit:{evidence.get('memory_key') or intent.slots.project_name}")


def _format_mission_result(result_text: str, intent: MissionIntent, route: CapabilityRoute, router_evidence: str) -> str:
    data = _parse_tool_result(result_text)
    ok = bool(data.get("ok"))
    evidence = data.get("evidence") if isinstance(data.get("evidence"), dict) else data
    evidence_path = str(evidence.get("evidence_path") or data.get("evidence_path") or "").strip()
    panel_path = str(evidence.get("evidence_panel_path") or data.get("evidence_panel_path") or "").strip()
    report_path = str(evidence.get("report_path") or data.get("report_path") or "").strip()

    if intent.task_type == MissionTaskType.PROJECT_BRIEFING_DELIVERY:
        target = intent.slots.project_name or intent.slots.project_path or "目标项目"
        recipients = "、".join(intent.slots.recipients)
        head = f"已通过 Codex 总结 {target}，并发送给 {recipients}。" if ok else f"Codex -> Lark 工作流未完成：{data.get('detail') or 'unknown'}。"
    elif intent.task_type == MissionTaskType.LARK_MESSAGE_SEND:
        head = "Lark 消息已发送并完成校验。" if ok else f"Lark 消息发送未完成：{data.get('detail') or 'unknown'}。"
    elif intent.task_type == MissionTaskType.PROJECT_MEMORY_UPDATE:
        target = intent.slots.project_name or intent.slots.project_path or "项目"
        head = f"已记住项目路径：{target}。" if ok else f"项目路径记忆未完成：{data.get('detail') or 'unknown'}。"
    elif intent.task_type == MissionTaskType.APP_CONTROL:
        head = f"已执行 App 操作：{intent.slots.app_name}。" if ok else f"App 操作未完成：{data.get('detail') or 'unknown'}。"
    elif intent.task_type == MissionTaskType.SYSTEM_STATUS_REPORT:
        head = "已完成 Windows 系统状态检查。" if ok else f"系统状态检查未完成：{data.get('detail') or 'unknown'}。"
    elif intent.task_type == MissionTaskType.FILE_TO_APP:
        head = "已执行文件到 App 的桥接流程。" if ok else f"文件到 App 桥接未完成：{data.get('detail') or 'unknown'}。"
    else:
        head = "OS Mission 已执行。" if ok else f"OS Mission 未完成：{data.get('detail') or 'unknown'}。"

    lines = [
        head,
        f"识别意图: {intent.task_type.value}",
        f"使用 workflow: {route.workflow_id or route.tool_id}",
        f"Router Evidence: {router_evidence}",
    ]
    if evidence_path:
        lines.append(f"Tool Evidence: {evidence_path}")
    if panel_path:
        lines.append(f"Evidence Panel: {panel_path}")
    if report_path:
        lines.append(f"Report: {report_path}")
    if not ok:
        lines.append("我没有把未通过校验的内容当作成功。")
    return "\n".join(lines)


def _execute_mission_sync(intent: MissionIntent, route: CapabilityRoute) -> str:
    from l3_client.local_mcps.windows_uia_mcp import server as windows_uia_server

    slots = intent.slots
    if route.tool_id == "mcp:windows_codex_lark_workflow_template":
        return windows_uia_server.windows_codex_lark_workflow_template(
            project_name=slots.project_name,
            project_path=slots.project_path,
            directory_path=slots.directory_path,
            feature_query=slots.feature_query,
            bug_query=slots.bug_query,
            original_user_input=intent.raw_text,
            recipients_json=json.dumps(slots.recipients, ensure_ascii=False),
            since_days=slots.since_days,
            wait_seconds=120,
            send_summary=True,
            remember=True,
            out_dir="",
        )
    if route.tool_id == "mcp:windows_lark_send_message":
        return windows_uia_server.windows_lark_send_message(
            json.dumps(slots.recipients, ensure_ascii=False),
            slots.message,
            "",
            2,
        )
    if route.tool_id == "mcp:windows_project_remember":
        return windows_uia_server.windows_project_remember(slots.project_name, slots.project_path, "")
    if route.tool_id == "local:project_memory":
        try:
            row = remember_project(slots.project_name, slots.project_path)
            return json.dumps({"task": "project_memory_update", "ok": True, "detail": "project_remembered", "evidence": row}, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"task": "project_memory_update", "ok": False, "detail": f"failed:{exc!r}"}, ensure_ascii=False)
    if route.tool_id == "mcp:windows_open_app":
        return windows_uia_server.windows_open_app(slots.app_name, "[]", "")
    if route.tool_id == "mcp:windows_system_status":
        return windows_uia_server.windows_system_status("www.baidu.com", "")
    if route.tool_id == "mcp:windows_workspace_report":
        return windows_uia_server.windows_workspace_report("", slots.since_days, False, "")
    if route.tool_id == "mcp:windows_file_bridge_to_app":
        return windows_uia_server.windows_file_bridge_to_app(slots.file_path, slots.app_name, "", 1, "ctrl+o", "")
    return json.dumps({"ok": False, "detail": f"unsupported_route:{route.tool_id}"}, ensure_ascii=False)


async def _execute_prepared_mission(
    *,
    intent: MissionIntent,
    route: CapabilityRoute,
    clarification: ClarificationDecision,
    semantic_meta: dict[str, Any],
    memory_evidence: dict[str, Any],
    template: MissionTemplate | None,
    plan: Any,
    preview: Any,
    capability_semantic: dict[str, Any] | None = None,
    workflow_composition: dict[str, Any] | None = None,
    control: dict[str, Any] | None = None,
) -> str:
    control_payload = dict(control or {})
    control_payload.setdefault("executed_at", time.strftime("%Y-%m-%d %H:%M:%S"))

    def _run() -> dict[str, Any]:
        return execute_with_retry(
            intent=intent,
            route=route,
            execute_once=lambda: _execute_mission_sync(intent, route),
            parse_result=_parse_tool_result,
        )

    runtime = await asyncio.to_thread(_run)
    result_text = str(runtime.get("result_text") or "")
    result_data = runtime.get("result_data") if isinstance(runtime.get("result_data"), dict) else _parse_tool_result(result_text)
    memory_update: dict[str, Any] = {}
    if bool(result_data.get("ok")):
        memory_update = record_successful_mission(intent, template.id if template else route.workflow_id)
    control_payload["result_ok"] = bool(result_data.get("ok"))
    control_payload["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    router_evidence = _write_router_evidence(
        intent=intent,
        route=route,
        clarification=clarification,
        status="done" if bool(result_data.get("ok")) else "failed",
        parser_meta=semantic_meta,
        memory_evidence={**memory_evidence, "post_run_update": memory_update},
        template=template,
        mission_preview=preview.to_dict(),
        capability_semantic=capability_semantic,
        workflow_composition=workflow_composition,
        control=control_payload,
        plan_preview=plan.to_dict(),
        tool_result=result_data,
        attempts=runtime.get("attempts") if isinstance(runtime.get("attempts"), list) else [],
        retry=runtime.get("retry") if isinstance(runtime.get("retry"), dict) else {},
        metrics=runtime.get("metrics") if isinstance(runtime.get("metrics"), dict) else {},
    )
    return "\n".join([*format_preview_for_chat(preview, executed=True), _format_mission_result(result_text, intent, route, router_evidence)])


async def maybe_run_codex_lark_mission(
    *,
    user_input: str,
    tools: list[dict[str, Any]],
    allowed: list[str] | None = None,
) -> str | None:
    if os.environ.get("JACHIN_DISABLE_OS_MISSION_ROUTER", "").strip().lower() in {"1", "true", "yes", "on"}:
        return None

    pending = load_pending_mission()
    if pending:
        if is_cancel_command(user_input):
            clear_pending_mission()
            intent = mission_intent_from_dict(pending.get("intent") or {})
            route = capability_route_from_dict(pending.get("route") or {})
            template = select_mission_template(intent, route)
            plan = build_plan_preview(intent, route)
            clarification = ClarificationDecision(False)
            preview = build_mission_preview(
                intent=intent,
                route=route,
                plan=plan,
                template=template,
                clarification=clarification,
                memory_evidence=pending.get("memory") if isinstance(pending.get("memory"), dict) else {},
            )
            evidence_path = _write_router_evidence(
                intent=intent,
                route=route,
                clarification=clarification,
                status="cancelled",
                parser_meta=pending.get("parser") if isinstance(pending.get("parser"), dict) else {},
                memory_evidence=pending.get("memory") if isinstance(pending.get("memory"), dict) else {},
                template=template,
                mission_preview=preview.to_dict(),
                capability_semantic=pending.get("capability_semantic") if isinstance(pending.get("capability_semantic"), dict) else {},
                workflow_composition=pending.get("workflow_composition") if isinstance(pending.get("workflow_composition"), dict) else {},
                control={
                    "status": "cancelled",
                    "decision": "cancel",
                    "pending_id": pending.get("pending_id"),
                    "initial_user_input": pending.get("initial_user_input"),
                    "cancelled_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "history": pending.get("history") or [],
                },
                plan_preview=plan.to_dict(),
            )
            return f"已取消这次 OS 任务。\nRouter Evidence: {evidence_path}"

        if is_confirmation_command(user_input):
            intent = mission_intent_from_dict(pending.get("intent") or {})
            route = capability_route_from_dict(pending.get("route") or {})
            template = select_mission_template(intent, route)
            clarification = ClarificationDecision(False)
            plan = build_plan_preview(intent, route)
            preview = build_mission_preview(
                intent=intent,
                route=route,
                plan=plan,
                template=template,
                clarification=clarification,
                memory_evidence=pending.get("memory") if isinstance(pending.get("memory"), dict) else {},
            )
            clear_pending_mission()
            return await _execute_prepared_mission(
                intent=intent,
                route=route,
                clarification=clarification,
                semantic_meta=pending.get("parser") if isinstance(pending.get("parser"), dict) else {},
                memory_evidence=pending.get("memory") if isinstance(pending.get("memory"), dict) else {},
                template=template,
                plan=plan,
                preview=preview,
                capability_semantic=pending.get("capability_semantic") if isinstance(pending.get("capability_semantic"), dict) else {},
                workflow_composition=pending.get("workflow_composition") if isinstance(pending.get("workflow_composition"), dict) else {},
                control={
                    "status": "confirmed",
                    "decision": "confirm_execute",
                    "pending_id": pending.get("pending_id"),
                    "initial_user_input": pending.get("initial_user_input"),
                    "confirmed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "history": pending.get("history") or [],
                },
            )

        # A pending mission exists and the user did not confirm/cancel. Treat
        # slot-like language as a patch to the preview, then keep waiting.
        intent = mission_intent_from_dict(pending.get("intent") or {})
        route = capability_route_from_dict(pending.get("route") or {})
        patched_intent, changes = patch_intent_from_text(intent, user_input)
        if changes:
            memory_evidence = apply_memory_to_intent(patched_intent)
            match_result = match_task_to_capability(str(pending.get("initial_user_input") or user_input), tools, allowed)
            match_result.understanding.intent = patched_intent
            route = choose_capability_route(patched_intent, tools, allowed)
            match_result.route = route
            composition = compose_workflow(match_result)
            template = select_mission_template(patched_intent, route)
            clarification = decide_clarification(patched_intent, route)
            plan = build_plan_preview(patched_intent, route)
            preview = build_mission_preview(
                intent=patched_intent,
                route=route,
                plan=plan,
                template=template,
                clarification=clarification,
                memory_evidence=memory_evidence,
            )
            history = [*(pending.get("history") or []), {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "user_input": user_input, "changes": changes}]
            saved = save_pending_mission(
                {
                    **pending,
                    "intent": patched_intent.to_dict(),
                    "route": route.to_dict(),
                    "memory": memory_evidence,
                    "template": template.to_dict() if template else {},
                    "mission_preview": preview.to_dict(),
                    "capability_semantic": match_result.to_dict(),
                    "workflow_composition": composition.to_dict(),
                    "plan_preview": plan.to_dict(),
                    "history": history,
                }
            )
            evidence_path = _write_router_evidence(
                intent=patched_intent,
                route=route,
                clarification=clarification,
                status="preview_updated",
                parser_meta=pending.get("parser") if isinstance(pending.get("parser"), dict) else {},
                memory_evidence=memory_evidence,
                template=template,
                mission_preview=preview.to_dict(),
                capability_semantic=match_result.to_dict(),
                workflow_composition=composition.to_dict(),
                control={
                    "status": "pending_confirmation",
                    "decision": "patch_preview",
                    "pending_id": saved.get("pending_id"),
                    "initial_user_input": saved.get("initial_user_input"),
                    "changes": changes,
                    "history": history,
                },
                plan_preview=plan.to_dict(),
            )
            return "\n".join(
                [
                    *format_preview_for_chat(preview),
                    "已更新任务预览。确认后我再执行；也可以继续修改或取消。",
                    f"Router Evidence: {evidence_path}",
                ]
            )

        return "当前有一个待确认的 OS 任务。你可以说：确认执行、取消，或者直接说要修改什么，例如“改发给 Vivian”“时间范围改成 7 天”。"

    semantic = parse_semantic_intent(user_input)
    intent = semantic.intent
    if intent.task_type == MissionTaskType.UNKNOWN:
        return None
    memory_evidence = apply_memory_to_intent(intent)
    match_result = match_task_to_capability(user_input, tools, allowed)
    match_result.understanding.intent = intent
    schema_route = choose_capability_route(intent, tools, allowed)
    route = schema_route if schema_route.tool_id else match_result.route
    match_result.route = route
    composition = compose_workflow(match_result)
    template = select_mission_template(intent, route)
    clarification = decide_clarification(intent, route)
    plan = build_plan_preview(intent, route)
    preview = build_mission_preview(
        intent=intent,
        route=route,
        plan=plan,
        template=template,
        clarification=clarification,
        memory_evidence=memory_evidence,
    )
    if clarification.should_ask:
        evidence_path = _write_router_evidence(
            intent=intent,
            route=route,
            clarification=clarification,
            status="clarification_required",
            parser_meta=semantic.meta,
            memory_evidence=memory_evidence,
            template=template,
            mission_preview=preview.to_dict(),
            capability_semantic=match_result.to_dict(),
            workflow_composition=composition.to_dict(),
            plan_preview=plan.to_dict(),
        )
        logger.info(
            "[OSMissionRouter] clarification intent=%s reason=%s evidence=%s",
            intent.task_type.value,
            clarification.reason,
            evidence_path,
        )
        return "\n".join([*format_preview_for_chat(preview), clarification.question, f"Router Evidence: {evidence_path}"])
    if should_hold_for_confirmation(intent, plan):
        saved = save_pending_mission(
            {
                "initial_user_input": user_input,
                "intent": intent.to_dict(),
                "parser": semantic.meta,
                "memory": memory_evidence,
                "route": route.to_dict(),
                "template": template.to_dict() if template else {},
                "mission_preview": preview.to_dict(),
                "capability_semantic": match_result.to_dict(),
                "workflow_composition": composition.to_dict(),
                "plan_preview": plan.to_dict(),
                "history": [{"at": time.strftime("%Y-%m-%d %H:%M:%S"), "user_input": user_input, "decision": "preview_created"}],
            }
        )
        evidence_path = _write_router_evidence(
            intent=intent,
            route=route,
            clarification=ClarificationDecision(True, "请确认是否执行，或者继续修改这个任务。", "mission_preview_confirmation_required"),
            status="preview_waiting_confirmation",
            parser_meta=semantic.meta,
            memory_evidence=memory_evidence,
            template=template,
            mission_preview=preview.to_dict(),
            capability_semantic=match_result.to_dict(),
            workflow_composition=composition.to_dict(),
            control={
                "status": "pending_confirmation",
                "decision": "preview_created",
                "pending_id": saved.get("pending_id"),
                "pending_path": saved.get("pending_path"),
                "initial_user_input": user_input,
                "mode": "preview_then_confirm",
                "history": saved.get("history") or [],
            },
            plan_preview=plan.to_dict(),
        )
        return "\n".join(
            [
                *format_preview_for_chat(preview),
                "我已经生成任务预览，暂不执行。你可以回复：确认执行、取消，或直接修改，例如“改发给 Vivian”“时间范围改成 7 天”。",
                f"Router Evidence: {evidence_path}",
            ]
        )

    logger.info(
        "[OSMissionRouter] route=%s tool=%s confidence=%.2f slots=%s",
        intent.task_type.value,
        route.tool_id,
        intent.confidence,
        intent.slots.to_dict(),
    )

    return await _execute_prepared_mission(
        intent=intent,
        route=route,
        clarification=clarification,
        semantic_meta=semantic.meta,
        memory_evidence=memory_evidence,
        template=template,
        plan=plan,
        preview=preview,
        capability_semantic=match_result.to_dict(),
        workflow_composition=composition.to_dict(),
        control={"status": "auto_executed", "decision": "auto_execute", "initial_user_input": user_input},
    )


maybe_run_os_mission = maybe_run_codex_lark_mission
