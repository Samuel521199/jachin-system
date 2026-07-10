"""User-facing mission result normalization.

Tools return technical evidence. This module turns that evidence into a
stable task result contract and a concise answer that can be shown or spoken
to the user. It intentionally lives above concrete MCP tools so every mission
path gets the same completion contract.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from l3_node.mission_intent_schema import CapabilityRoute, MissionIntent, MissionTaskType


@dataclass
class MissionUserResult:
    executed: bool
    success: bool
    status: str
    user_summary: str
    speakable_summary: str = ""
    user_result: str = ""
    technical_detail: str = ""
    warnings: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _norm_number(value: Any) -> str:
    return re.sub(r"[,\s]", "", str(value or "")).strip()


def _friendly_detail(detail: Any) -> str:
    raw = str(detail or "unknown").strip()
    if "NameError" in raw or "nameerror" in raw or "workflow_code_defect" in raw:
        return "\u5de5\u4f5c\u6d41\u5185\u90e8\u4f9d\u8d56\u7f3a\u5931\uff0c\u6211\u5df2\u505c\u6b62\u540e\u7eed\u64cd\u4f5c\u4ee5\u907f\u514d\u8bef\u53d1"
    mapping = {
        "visual_or_result_mismatch": "\u7ed3\u679c\u6821\u9a8c\u6ca1\u6709\u5b8c\u5168\u901a\u8fc7",
        "result_verified_expression_ocr_incomplete": "\u7ed3\u679c\u5df2\u6821\u9a8c\u901a\u8fc7\uff0c\u4f46\u8868\u8fbe\u5f0f OCR \u8bc6\u522b\u4e0d\u5b8c\u6574",
        "workflow_failed": "\u5de5\u4f5c\u6d41\u6ca1\u6709\u5b8c\u6210",
        "mouse_failsafe_triggered": "\u9f20\u6807\u5728\u5c4f\u5e55\u89d2\u843d\u89e6\u53d1\u4e86\u81ea\u52a8\u5316\u5b89\u5168\u6025\u505c",
        "unsupported_route": "\u5f53\u524d\u6ca1\u6709\u53ef\u7528\u7684\u6267\u884c\u8def\u7531",
        "project_path_required": "\u9700\u8981\u5148\u786e\u8ba4\u9879\u76ee\u8def\u5f84",
        "app_executable_not_found": "没有找到目标应用的启动程序，请确认应用已安装，或在设置里配置正确的程序路径",
        "app_launch_failed": "目标应用启动失败，请确认应用路径和 Windows 权限正常",
        "app_focus_failed": "\u6ca1\u6709\u53ef\u9760\u5207\u6362\u5230\u76ee\u6807\u5e94\u7528",
        "wrong_recipient": "\u5f53\u524d\u6253\u5f00\u7684 Lark \u4f1a\u8bdd\u4e0d\u662f\u76ee\u6807\u8054\u7cfb\u4eba\uff0c\u6211\u5df2\u505c\u6b62\u53d1\u9001\u4ee5\u907f\u514d\u53d1\u9519\u4eba",
        "wrong_foreground_app": "\u76ee\u6807\u5e94\u7528\u7a97\u53e3\u88ab\u5176\u4ed6\u7a97\u53e3\u62a2\u5230\u524d\u53f0\uff0c\u6211\u5df2\u5c1d\u8bd5\u91cd\u65b0\u5207\u56de\uff1b\u5982\u679c\u4ecd\u65e0\u6cd5\u53ef\u9760\u786e\u8ba4\u76ee\u6807\u7a97\u53e3\uff0c\u5c31\u4f1a\u505c\u6b62\u8f93\u5165\u4ee5\u907f\u514d\u8bef\u64cd\u4f5c",
        "foreground_app_unknown": "\u65e0\u6cd5\u53ef\u9760\u786e\u8ba4\u5f53\u524d\u524d\u53f0\u7a97\u53e3",
        "codex_reply_validation_failed": "\u6ca1\u6709\u53ef\u9760\u62ff\u5230 Codex \u7684\u5b8c\u6574\u56de\u590d",
    }
    for key, text in mapping.items():
        if key in raw:
            return text
    return raw


def _evidence_dict(result_data: dict[str, Any]) -> dict[str, Any]:
    evidence = result_data.get("evidence")
    return evidence if isinstance(evidence, dict) else result_data


def _last_timeline_stage(evidence: dict[str, Any]) -> str:
    timeline = evidence.get("timeline")
    if not isinstance(timeline, list):
        return ""
    for row in reversed(timeline):
        if isinstance(row, dict) and str(row.get("stage") or "").strip():
            return str(row.get("stage") or "").strip()
    return ""


def _is_mouse_failsafe(detail: str, evidence: dict[str, Any], runtime: dict[str, Any] | None = None) -> bool:
    failure_class = str((((runtime or {}).get("metrics") or {}).get("failure_class")) or "")
    raw = f"{detail} {failure_class} {evidence}".lower()
    return "mouse_failsafe_triggered" in raw or "failsafeexception" in raw or "fail-safe" in raw


def _is_workflow_code_defect(detail: str, evidence: dict[str, Any], runtime: dict[str, Any] | None = None) -> bool:
    failure_class = str((((runtime or {}).get("metrics") or {}).get("failure_class")) or "")
    raw = f"{detail} {failure_class} {evidence}".lower()
    return "workflow_code_defect" in raw or ("nameerror" in raw and "not defined" in raw)


def _humanize_expression(expr: str) -> str:
    text = str(expr or "").strip()
    if not text:
        return ""
    replacements = [
        ("**", " \u7684 "),
        ("*", " \u4e58\u4ee5 "),
        ("x", " \u4e58\u4ee5 "),
        ("X", " \u4e58\u4ee5 "),
        ("\u00d7", " \u4e58\u4ee5 "),
        ("/", " \u9664\u4ee5 "),
        ("\u00f7", " \u9664\u4ee5 "),
        ("+", " \u52a0 "),
        ("-", " \u51cf "),
    ]
    out = text
    for old, new in replacements:
        out = out.replace(old, new)
    return re.sub(r"\s+", " ", out).strip()


def _calculator_user_result(intent: MissionIntent, result_data: dict[str, Any]) -> MissionUserResult | None:
    evidence = _evidence_dict(result_data)
    expect = _norm_number(evidence.get("expect"))
    clipboard = _norm_number(evidence.get("clipboard_norm") or evidence.get("clipboard_raw"))
    visual = evidence.get("visual") if isinstance(evidence.get("visual"), dict) else {}
    visual_result = _norm_number(visual.get("result_norm") or visual.get("result"))
    direct_result = _norm_number(evidence.get("result_norm") or evidence.get("result"))
    detail = str(result_data.get("detail") or "")
    raw_ok = bool(result_data.get("ok"))
    expr = intent.slots.expression or "\u8fd9\u4e2a\u8868\u8fbe\u5f0f"
    human_expr = _humanize_expression(expr) or expr

    if "wrong_foreground_app" in detail or "app_focus_failed" in detail:
        guard = evidence.get("environment_guard") if isinstance(evidence.get("environment_guard"), dict) else {}
        focus_result = evidence.get("focus_result") if isinstance(evidence.get("focus_result"), dict) else {}
        focus_evidence = focus_result.get("evidence") if isinstance(focus_result.get("evidence"), dict) else {}
        if not guard and isinstance(focus_evidence.get("environment_guard"), dict):
            guard = focus_evidence.get("environment_guard")
        active = guard.get("active") if isinstance(guard.get("active"), dict) else {}
        active_name = str(active.get("process") or active.get("title") or "\u5176\u4ed6\u7a97\u53e3").strip()
        if "wrong_foreground_app" in detail:
            summary = (
                f"\u6211\u8bc6\u522b\u5230\u8981\u7528 Windows \u8ba1\u7b97\u5668\u8ba1\u7b97 {human_expr}\uff0c"
                f"\u4f46\u8f93\u5165\u524d\u53d1\u73b0\u524d\u53f0\u4ecd\u662f {active_name}\uff0c\u4e0d\u662f\u8ba1\u7b97\u5668\uff0c\u6240\u4ee5\u6ca1\u6709\u7ee7\u7eed\u8f93\u5165\u3002"
            )
        else:
            summary = f"\u6211\u8bc6\u522b\u5230\u8981\u7528 Windows \u8ba1\u7b97\u5668\u8ba1\u7b97 {human_expr}\uff0c\u4f46\u6ca1\u6709\u53ef\u9760\u628a\u8ba1\u7b97\u5668\u5207\u5230\u524d\u53f0\u3002"
        return MissionUserResult(
            executed=True,
            success=False,
            status="failed",
            user_summary=summary,
            speakable_summary=summary,
            user_result="",
            technical_detail=detail,
            evidence={
                "expected": expect,
                "clipboard": clipboard,
                "visual_result": visual_result,
                "raw_ok": raw_ok,
                "active_window": active,
            },
        )

    result = expect or clipboard or visual_result or direct_result
    if not result:
        return None

    strong_result_match = bool(expect and (clipboard == expect or visual_result == expect))
    success = raw_ok or strong_result_match
    warnings: list[str] = []
    if success and "result_verified_expression_ocr_incomplete" in detail:
        warnings.append(
            "\u8ba1\u7b97\u7ed3\u679c\u5df2\u53ef\u9760\u5339\u914d\uff0c\u4f46\u8ba1\u7b97\u5668\u4e0a\u7684\u8868\u8fbe\u5f0f OCR \u8bc6\u522b\u4e0d\u5b8c\u6574\u3002"
        )
    elif success and not raw_ok:
        warnings.append(
            "\u5de5\u5177\u8fd4\u56de\u4e86\u6280\u672f\u6821\u9a8c\u8b66\u544a\uff0c\u4f46\u526a\u8d34\u677f\u6216\u5c4f\u5e55\u7ed3\u679c\u5df2\u5339\u914d\u9884\u671f\u503c\u3002"
        )
    if success:
        summary = f"\u6211\u7b97\u597d\u4e86\uff0c{human_expr} \u7b49\u4e8e {result}\u3002"
        status = "completed_with_warning" if warnings else "completed"
    else:
        summary = (
            "\u6211\u5c1d\u8bd5\u7528 Windows \u8ba1\u7b97\u5668\u8ba1\u7b97"
            f" {human_expr}\uff0c\u4f46\u6ca1\u6709\u53ef\u9760\u62ff\u5230\u6700\u7ec8\u7ed3\u679c\u3002"
        )
        status = "failed"
    return MissionUserResult(
        executed=True,
        success=success,
        status=status,
        user_summary=summary,
        speakable_summary=summary,
        user_result=result,
        technical_detail=detail,
        warnings=warnings,
        evidence={
            "expected": expect,
            "clipboard": clipboard,
            "visual_result": visual_result,
            "raw_ok": raw_ok,
        },
    )


def build_mission_user_result(
    *,
    intent: MissionIntent,
    route: CapabilityRoute,
    result_data: dict[str, Any],
    runtime: dict[str, Any] | None = None,
) -> MissionUserResult:
    raw_ok = bool(result_data.get("ok"))
    detail = str(result_data.get("detail") or result_data.get("task") or "")
    evidence = _evidence_dict(result_data)

    if _is_workflow_code_defect(detail, evidence, runtime):
        recipients = "\u3001".join(intent.slots.recipients) or "\u6307\u5b9a\u5bf9\u8c61"
        if intent.task_type == MissionTaskType.CODEX_ASK_LARK_SEND:
            summary = f"\u6211\u5df2\u7ecf\u5c1d\u8bd5\u8ba9 Codex \u56de\u7b54\u95ee\u9898\u5e76\u53d1\u9001\u7ed9 {recipients}\uff0c\u4f46\u5728\u6574\u7406\u5f85\u53d1\u9001\u7684 Codex \u56de\u590d\u65f6\u9047\u5230\u4e86\u5de5\u4f5c\u6d41\u5185\u90e8\u4f9d\u8d56\u7f3a\u5931\uff0c\u6240\u4ee5\u6ca1\u6709\u7ee7\u7eed\u53d1\u9001 Lark \u6d88\u606f\u3002"
        else:
            summary = "\u6267\u884c\u8fd9\u4e2a\u672c\u5730\u4efb\u52a1\u65f6\u9047\u5230\u4e86\u5de5\u4f5c\u6d41\u5185\u90e8\u4f9d\u8d56\u7f3a\u5931\uff0c\u6211\u5df2\u505c\u6b62\u540e\u7eed\u64cd\u4f5c\u4ee5\u907f\u514d\u8bef\u64cd\u4f5c\u3002"
        return MissionUserResult(
            executed=True,
            success=False,
            status="failed",
            user_summary=summary,
            speakable_summary=summary,
            technical_detail=detail,
            evidence={
                "raw_ok": raw_ok,
                "tool_id": route.tool_id,
                "workflow_id": route.workflow_id,
                "attempt_count": ((runtime or {}).get("metrics") or {}).get("attempt_count"),
                "failure_class": "workflow_code_defect",
                "tool_evidence_path": evidence.get("evidence_path"),
            },
        )

    if _is_mouse_failsafe(detail, evidence, runtime):
        recipients = "\u3001".join(intent.slots.recipients) or "\u6307\u5b9a\u5bf9\u8c61"
        stage = _last_timeline_stage(evidence)
        stage_text = f"\u505c\u5728 {stage} \u8fd9\u4e00\u6b65" if stage else "\u5df2\u505c\u6b62\u540e\u7eed\u64cd\u4f5c"
        if intent.task_type == MissionTaskType.CODEX_ASK_LARK_SEND:
            summary = (
                f"\u6211\u51c6\u5907\u8ba9 Codex \u56de\u7b54\u95ee\u9898\u5e76\u53d1\u9001\u7ed9 {recipients}\uff0c"
                f"\u4f46\u68c0\u6d4b\u5230\u9f20\u6807\u5728\u5c4f\u5e55\u89d2\u843d\u89e6\u53d1\u4e86\u5b89\u5168\u6025\u505c\uff0c{stage_text}\u3002\u6211\u6ca1\u6709\u7ee7\u7eed\u53d1\u9001 Lark \u6d88\u606f\u3002"
            )
        elif intent.task_type == MissionTaskType.LARK_MESSAGE_SEND:
            summary = f"\u6211\u51c6\u5907\u5411 {recipients} \u53d1\u9001\u6d88\u606f\uff0c\u4f46\u9f20\u6807\u5728\u5c4f\u5e55\u89d2\u843d\u89e6\u53d1\u4e86\u5b89\u5168\u6025\u505c\uff0c{stage_text}\u3002\u6211\u6ca1\u6709\u7ee7\u7eed\u53d1\u9001\u3002"
        else:
            summary = f"\u6267\u884c\u8fd9\u4e2a\u672c\u5730\u4efb\u52a1\u65f6\uff0c\u9f20\u6807\u5728\u5c4f\u5e55\u89d2\u843d\u89e6\u53d1\u4e86\u5b89\u5168\u6025\u505c\uff0c{stage_text}\u3002\u4e3a\u4e86\u907f\u514d\u8bef\u64cd\u4f5c\uff0c\u6211\u5df2\u7ecf\u505c\u6b62\u3002"
        return MissionUserResult(
            executed=True,
            success=False,
            status="interrupted",
            user_summary=summary,
            speakable_summary=summary,
            technical_detail=detail,
            evidence={
                "raw_ok": raw_ok,
                "tool_id": route.tool_id,
                "workflow_id": route.workflow_id,
                "attempt_count": ((runtime or {}).get("metrics") or {}).get("attempt_count"),
                "failure_class": "mouse_failsafe_triggered",
                "last_stage": stage,
                "tool_evidence_path": evidence.get("evidence_path"),
            },
        )

    if intent.task_type == MissionTaskType.CALCULATOR_CALCULATE:
        calc = _calculator_user_result(intent, result_data)
        if calc is not None:
            return calc

    status = "completed" if raw_ok else "failed"
    friendly = _friendly_detail(detail)
    slots = intent.slots

    if intent.task_type == MissionTaskType.PROJECT_BRIEFING_DELIVERY:
        target = slots.project_name or slots.project_path or "\u76ee\u6807\u9879\u76ee"
        recipients = "\u3001".join(slots.recipients) or "\u6307\u5b9a\u5bf9\u8c61"
        summary = (
            f"\u6211\u5df2\u7ecf\u5b8c\u6210 {target} \u7684\u9879\u76ee\u603b\u7ed3\uff0c\u5e76\u53d1\u9001\u7ed9 {recipients}\u3002"
            if raw_ok
            else f"\u6211\u5c1d\u8bd5\u603b\u7ed3 {target} \u5e76\u53d1\u9001\u7ed9 {recipients}\uff0c\u4f46\u6ca1\u6709\u5b8c\u6210\uff1a{friendly}\u3002"
        )
    elif intent.task_type == MissionTaskType.LARK_MESSAGE_SEND:
        recipients = "\u3001".join(slots.recipients) or "\u6307\u5b9a\u5bf9\u8c61"
        summary = (
            f"\u6211\u5df2\u7ecf\u628a\u6d88\u606f\u53d1\u9001\u7ed9 {recipients}\u3002"
            if raw_ok
            else f"\u6211\u5c1d\u8bd5\u5411 {recipients} \u53d1\u9001\u6d88\u606f\uff0c\u4f46\u6ca1\u6709\u5b8c\u6210\uff1a{friendly}\u3002"
        )
    elif intent.task_type == MissionTaskType.CODEX_ASK_LARK_SEND:
        recipients = "、".join(slots.recipients) or "指定对象"
        summary = (
            f"我已经让 Codex 回答了问题，并把回复发送给 {recipients}。"
            if raw_ok
            else f"我尝试让 Codex 回答问题并发送给 {recipients}，但没有完成：{friendly}。"
        )
    elif intent.task_type == MissionTaskType.APP_CONTROL:
        app = slots.app_name or "\u76ee\u6807\u5e94\u7528"
        summary = (
            f"\u6211\u5df2\u7ecf\u6253\u5f00\u6216\u5207\u6362\u5230 {app}\u3002"
            if raw_ok
            else f"\u6211\u5c1d\u8bd5\u6253\u5f00\u6216\u5207\u6362\u5230 {app}\uff0c\u4f46\u6ca1\u6709\u5b8c\u6210\uff1a{friendly}\u3002"
        )
    elif intent.task_type == MissionTaskType.PROJECT_MEMORY_UPDATE:
        target = slots.project_name or slots.project_path or "\u8fd9\u4e2a\u9879\u76ee"
        summary = (
            f"\u6211\u5df2\u7ecf\u8bb0\u4f4f {target} \u7684\u672c\u5730\u8def\u5f84\u3002"
            if raw_ok
            else f"\u6211\u5c1d\u8bd5\u8bb0\u5f55 {target} \u7684\u672c\u5730\u8def\u5f84\uff0c\u4f46\u6ca1\u6709\u5b8c\u6210\uff1a{friendly}\u3002"
        )
    elif intent.task_type == MissionTaskType.SYSTEM_STATUS_REPORT:
        summary = (
            "\u6211\u5df2\u7ecf\u5b8c\u6210 Windows \u7cfb\u7edf\u72b6\u6001\u68c0\u67e5\u3002"
            if raw_ok
            else f"\u6211\u5c1d\u8bd5\u68c0\u67e5 Windows \u7cfb\u7edf\u72b6\u6001\uff0c\u4f46\u6ca1\u6709\u5b8c\u6210\uff1a{friendly}\u3002"
        )
    elif intent.task_type == MissionTaskType.FILE_TO_APP:
        app = slots.app_name or "\u76ee\u6807\u5e94\u7528"
        file_path = slots.file_path or "\u76ee\u6807\u6587\u4ef6"
        summary = (
            f"\u6211\u5df2\u7ecf\u628a {file_path} \u4ea4\u7ed9 {app} \u5904\u7406\u3002"
            if raw_ok
            else f"\u6211\u5c1d\u8bd5\u628a {file_path} \u4ea4\u7ed9 {app}\uff0c\u4f46\u6ca1\u6709\u5b8c\u6210\uff1a{friendly}\u3002"
        )
    else:
        summary = (
            "\u6211\u5df2\u7ecf\u5b8c\u6210\u8fd9\u4e2a\u672c\u5730\u4efb\u52a1\u3002"
            if raw_ok
            else f"\u6211\u5c1d\u8bd5\u6267\u884c\u8fd9\u4e2a\u672c\u5730\u4efb\u52a1\uff0c\u4f46\u6ca1\u6709\u5b8c\u6210\uff1a{friendly}\u3002"
        )

    return MissionUserResult(
        executed=True,
        success=raw_ok,
        status=status,
        user_summary=summary,
        speakable_summary=summary,
        technical_detail=detail,
        evidence={
            "raw_ok": raw_ok,
            "tool_id": route.tool_id,
            "workflow_id": route.workflow_id,
            "attempt_count": ((runtime or {}).get("metrics") or {}).get("attempt_count"),
            "tool_evidence_path": evidence.get("evidence_path"),
        },
    )


def format_mission_user_reply(
    *,
    task_result: MissionUserResult,
    intent: MissionIntent,
    route: CapabilityRoute,
    router_evidence: str,
    result_data: dict[str, Any],
) -> str:
    """Return only the user-facing answer.

    Debug fields such as task type, workflow id, and evidence paths are stored
    in Router Evidence. They must not leak into the main companion/chat reply.
    """
    lines = [task_result.user_summary]
    for warning in task_result.warnings:
        lines.append(f"\u8bf4\u660e\uff1a{warning}")
    return "\n".join(lines)
