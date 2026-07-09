"""Role-agent review board for the Memory-first Cognitive Kernel.

The ReviewBoard is a coordinator, not the final decision maker. It fans out the
same envelope/state/memory evidence to review roles, collects their structured
reviews, and emits a ReviewSummary for the Arbiter.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any

from .contracts import (
    AgentInputEnvelope,
    InputSource,
    RelevantMemoryBundle,
    ReviewSummary,
    RiskLevel,
    RoleAgentReview,
    RoleAgentReviewInput,
    StateSnapshot,
)
from .ledger import append_event


_APP_ALIASES: dict[str, tuple[str, ...]] = {
    "Calculator": ("计算器", "calculator", "calc"),
    "Lark": ("飞书", "lark", "feishu"),
    "WeChat": ("微信", "wechat", "weixin"),
    "Chrome": ("chrome", "google chrome", "谷歌"),
    "Edge": ("edge", "microsoft edge"),
    "Browser": ("browser", "web", "浏览器"),
    "DingTalk": ("钉钉", "dingtalk", "dingding"),
    "WeCom": ("企业微信", "wecom", "wxwork", "enterprise wechat"),
    "QQ": ("qq", "腾讯QQ"),
    "TIM": ("tim", "qq tim"),
    "TencentMeeting": ("腾讯会议", "tencent meeting", "voov", "wemeet"),
    "Zoom": ("zoom", "zoom meeting"),
    "Teams": ("teams", "microsoft teams"),
    "Slack": ("slack",),
    "Discord": ("discord",),
    "Firefox": ("firefox", "mozilla firefox", "火狐"),
    "VSCode": ("vscode", "vs code", "visual studio code", "code"),
    "Cursor": ("cursor",),
    "PyCharm": ("pycharm", "jetbrains pycharm"),
    "Notion": ("notion", "知识库"),
    "Obsidian": ("obsidian", "双链笔记"),
    "WPS": ("wps", "office", "金山办公"),
    "Word": ("word", "microsoft word", "winword", "文档"),
    "Excel": ("excel", "microsoft excel", "表格"),
    "PowerPoint": ("powerpoint", "ppt", "microsoft powerpoint"),
    "Outlook": ("outlook", "microsoft outlook", "邮箱"),
    "OneNote": ("onenote", "one note", "microsoft onenote", "笔记"),
    "Notepad": ("记事本", "notepad"),
    "Terminal": ("终端", "terminal", "powershell", "cmd"),
    "Explorer": ("资源管理器", "explorer", "文件管理器"),
}

_BASE_REVIEW_ROLES = (
    "IntentAnalystAgent",
    "EntityResolverAgent",
    "AmbiguityResolverAgent",
    "MemoryRecallAgent",
    "PreferenceAgent",
    "CorrectionLearningAgent",
    "DesktopStateReadAgent",
    "SafetyAgent",
    "PermissionAgent",
    "PrivacyAgent",
    "ConsistencyCheckAgent",
)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time())}_{uuid.uuid4().hex[:10]}"


def _norm_text(envelope: AgentInputEnvelope) -> str:
    return str(envelope.normalized_text or envelope.raw_text or "").strip()


def _lower(text: str) -> str:
    return text.lower()


def _iter_app_aliases_by_specificity() -> list[tuple[str, tuple[str, ...]]]:
    pairs = []
    for app_name, aliases in _APP_ALIASES.items():
        app_aliases = (app_name.lower(), *aliases)
        pairs.append((app_name, tuple(sorted(app_aliases, key=len, reverse=True))))
    return sorted(pairs, key=lambda item: max((len(alias) for alias in item[1]), default=0), reverse=True)


def _detect_intent(text: str) -> str:
    low = _lower(text)
    stripped = low.strip()
    if stripped in {"你好", "hello", "hi", "在吗", "你在吗"}:
        return "conversation"
    if any(k in low for k in ("file", "folder", "read ", "open file", "reveal", "show in explorer", "delete file", "rename", "copy", "move")):
        return "file_operation"
    if any(k in low for k in ("关闭", "关掉", "关了", "退出", "close", "quit")):
        return "close_app"
    if any(k in low for k in ("打开", "启动", "运行", "open ", "launch", "start ")):
        return "open_app"
    if any(k in low for k in ("切到", "切换到", "回到", "switch to")):
        return "switch_app"
    if any(k in low for k in ("发给", "发送", "发消息", "send to", "message")):
        return "message_send"
    if any(k in low for k in ("文件", "目录", "folder", "file", "rename", "copy", "move")):
        return "file_operation"
    return "conversation"


def _explicit_app_from_text(text: str) -> str:
    low = _lower(text)
    for app_name, aliases in _iter_app_aliases_by_specificity():
        if any(alias in low for alias in aliases):
            return app_name
    return ""


def _active_window_app(state_snapshot: StateSnapshot) -> str:
    active = state_snapshot.active_window or {}
    for key in ("app_name", "app", "process_name", "name"):
        value = str(active.get(key) or "").strip()
        if value:
            return _normalize_app_name(value)
    title = str(active.get("title") or active.get("window_title") or "").strip()
    return _normalize_app_name(title)


def _normalize_app_name(value: str) -> str:
    low = _lower(value)
    for app_name, aliases in _iter_app_aliases_by_specificity():
        if any(alias in low for alias in aliases):
            return app_name
    return value.strip()


def _recent_action_target(memory_bundle: RelevantMemoryBundle) -> str:
    for evidence in memory_bundle.recent_actions:
        content = evidence.content or ""
        target = _target_from_jsonish(content)
        if target:
            return _normalize_app_name(target)
        target = _explicit_app_from_text(content)
        if target:
            return target
    for ref in memory_bundle.resolved_references:
        target = str(ref.get("target_name") or ref.get("target") or ref.get("app_name") or "").strip()
        if target:
            return _normalize_app_name(target)
    return ""


def _target_from_jsonish(content: str) -> str:
    try:
        obj = json.loads(content)
    except Exception:
        obj = None
    if isinstance(obj, dict):
        for key in ("target_name", "target_app", "app_name", "target", "last_opened_app"):
            value = str(obj.get(key) or "").strip()
            if value:
                return value
        for value in obj.values():
            if isinstance(value, (dict, list)):
                nested = _target_from_jsonish(json.dumps(value, ensure_ascii=False))
                if nested:
                    return nested
    if isinstance(obj, list):
        for item in obj:
            nested = _target_from_jsonish(json.dumps(item, ensure_ascii=False))
            if nested:
                return nested
    return ""


def _extract_message_recipients(text: str) -> list[str]:
    text = str(text or "").strip()
    if not text:
        return []
    patterns = [
        r"(?:发给|发送给|给)\s*([A-Za-z0-9_\-\u4e00-\u9fff、,，和与\s]+?)\s*(?:[:：]|说|发送|发|$)",
        r"(?:send\s+to|message)\s+([A-Za-z0-9_\-\s,]+?)(?:[:：]|$)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if not m:
            continue
        raw = (m.group(1) or "").strip()
        raw = re.sub(r"(消息|信息|内容|同事|用户|群聊)$", "", raw).strip()
        parts = [x.strip() for x in re.split(r"[、,，]|和|与|\band\b", raw) if x.strip()]
        return parts[:8]
    return []


def _extract_message_body(text: str, recipients: list[str] | None = None) -> str:
    text = str(text or "").strip()
    if not text:
        return ""
    quoted = re.findall(r"[`\"“”'‘’](.*?)[`\"“”'‘’]", text)
    if quoted:
        return quoted[-1].strip()
    if ":" in text or "：" in text:
        tail = re.split(r"[:：]", text, maxsplit=1)[-1].strip()
        if tail:
            return tail
    m = re.search(r"(?:内容|消息|说|发送|发)\s*(?:是|为|:|：)?\s*(.+)$", text, re.I)
    if m:
        body = (m.group(1) or "").strip()
        if body and not any(body == r for r in recipients or []):
            return body
    m = re.search(r"(?:发给|发送给|给).+?(?:[:：]|说)\s*(.+)$", text, re.I)
    if m:
        return (m.group(1) or "").strip()
    return ""


def _extract_file_path(text: str) -> str:
    text = str(text or "").strip()
    if not text:
        return ""
    quoted = re.findall(r"[`\"“”'‘’](.*?)[`\"“”'‘’]", text)
    for value in quoted:
        value = value.strip()
        if value and (("\\" in value) or ("/" in value) or ("." in value)):
            return value
    m = re.search(r"([A-Za-z]:\\[^\s，。；;]+)", text)
    if m:
        return m.group(1).strip()
    m = re.search(r"((?:[\w\-.]+[\\/])+[\w\-.]+)", text)
    if m:
        return m.group(1).strip()
    m = re.search(r"(?:读取|打开|查看|定位|删除|重命名|复制|移动|写入|reveal|open|read|delete|rename|copy|move|write)\s+(?:文件|file)?\s*([^\s，。；;]+)", text, re.I)
    if m:
        return m.group(1).strip()
    return ""


def _extract_file_operation(text: str) -> str:
    low = _lower(text)
    if any(k in low for k in ("write", "save", "create", "new file", "写入", "保存", "创建", "新建")):
        return "write"
    if any(k in low for k in ("reveal", "show in explorer", "open location", "瀹氫綅", "鎵€鍦ㄤ綅缃", "璧勬簮绠＄悊鍣")):
        return "reveal"
    if any(k in low for k in ("open", "鎵撳紑", "鍚姩")):
        return "open"
    if any(k in low for k in ("write", "save", "copy", "move", "rename", "delete", "鍐欏叆", "淇濆瓨", "澶嶅埗", "绉诲姩", "閲嶅懡鍚", "鍒犻櫎")):
        return "mutating"
    return "read"


def _extract_file_content(text: str) -> str:
    text = str(text or "").strip()
    if not text:
        return ""
    quoted = re.findall(r"[`\"'“”‘’](.*?)[`\"'“”‘’]", text)
    if quoted:
        return quoted[-1].strip()
    patterns = [
        r"(?:content|text)\s*(?:is|=|:)\s*(.+)$",
        r"(?:内容|写入内容|文本)\s*(?:是|为|=|：|:)?\s*(.+)$",
        r"(?:write|save)\s+(.+?)\s+(?:to|into)\s+",
        r"(?:写入|保存)\s+(.+?)\s+(?:到|进)\s+",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return (match.group(1) or "").strip()
    return ""


def _extract_target(text: str, intent: str, state_snapshot: StateSnapshot, memory_bundle: RelevantMemoryBundle) -> dict[str, Any]:
    if intent == "message_send":
        recipients = _extract_message_recipients(text)
        message = _extract_message_body(text, recipients)
        if recipients or message:
            return {"type": "lark_message", "recipients": recipients, "message": message, "source": "input_text"}
    if intent == "file_operation":
        path = _extract_file_path(text)
        if path:
            return {
                "type": "file",
                "path": path,
                "name": path,
                "operation": _extract_file_operation(text),
                "content": _extract_file_content(text),
                "source": "input_text",
            }
    explicit_app = _explicit_app_from_text(text)
    if explicit_app:
        return {"type": "app", "name": explicit_app, "source": "input_text"}
    if intent in {"close_app", "switch_app"}:
        active = _active_window_app(state_snapshot)
        if active:
            return {"type": "app", "name": active, "source": "active_window"}
        recent = _recent_action_target(memory_bundle)
        if recent:
            return {"type": "app", "name": recent, "source": "recent_action_memory"}
    return {}


def _task_type_for_intent(intent: str) -> str:
    if intent in {"open_app", "close_app", "switch_app"}:
        return "app_control"
    if intent == "message_send":
        return "message_delivery"
    if intent == "file_operation":
        return "file_operation"
    return "conversation"


def _tool_for_intent(intent: str, target: dict[str, Any] | None = None) -> str:
    target = target or {}
    if intent == "file_operation":
        operation = str(target.get("operation") or "read").strip().lower()
        if operation == "open":
            return "mcp:windows_file_open"
        if operation == "reveal":
            return "mcp:windows_file_reveal_in_explorer"
        if operation == "write":
            return "core:fs_write"
        return "core:fs_read"
    return {
        "open_app": "mcp:windows_open_app",
        "close_app": "mcp:windows_window_close",
        "switch_app": "mcp:windows_window_switch",
        "message_send": "mcp:windows_lark_send_message",
    }.get(intent, "")


def _risk_for(intent: str, target: dict[str, Any], state_snapshot: StateSnapshot) -> tuple[RiskLevel, bool, str]:
    if intent == "conversation":
        return RiskLevel.LOW, False, ""
    risk_state = state_snapshot.risk_state or {}
    active = state_snapshot.active_window or {}
    unsaved = (
        _truthy_risk(risk_state.get("unsaved_documents"))
        or _truthy_risk(risk_state.get("unsaved_document"))
        or _truthy_risk(active.get("unsaved"))
        or "unsaved" in [str(x).lower() for x in active.get("risk_flags", [])]
    )
    if intent == "close_app" and unsaved:
        name = target.get("name") or "当前应用"
        return RiskLevel.HIGH, True, f"要关闭 {name} 吗？当前状态提示可能有未保存内容。"
    if intent == "message_send":
        return RiskLevel.HIGH, False, ""
    if intent == "file_operation":
        if str(target.get("operation") or "").lower() in {"mutating", "write"}:
            return RiskLevel.HIGH, True, "文件写入、移动、重命名或删除需要先确认后执行。"
        return RiskLevel.LOW, False, ""
    return RiskLevel.LOW, False, ""


def _voice_needs_clarification(envelope: AgentInputEnvelope, intent: str) -> tuple[bool, str]:
    if envelope.source != InputSource.VOICE or intent == "conversation":
        return False, ""
    confidence = envelope.confidence
    voice = (envelope.modality_evidence or {}).get("voice") or {}
    if confidence is None:
        raw_conf = voice.get("confidence") or voice.get("stt_confidence")
        try:
            confidence = float(raw_conf) if raw_conf is not None else None
        except Exception:
            confidence = None
    if confidence is not None and confidence < 0.72:
        return True, "语音识别置信度偏低，请确认你要执行的操作。"
    return False, ""


def run_review_board(
    *,
    envelope: AgentInputEnvelope,
    state_snapshot: StateSnapshot,
    memory_bundle: RelevantMemoryBundle,
    review_roles: tuple[str, ...] | None = None,
) -> ReviewSummary:
    text = _norm_text(envelope)
    intent = _detect_intent(text)
    task_type = _task_type_for_intent(intent)
    target = _extract_target(text, intent, state_snapshot, memory_bundle)
    tool = _tool_for_intent(intent, target)
    risk, risk_needs_clarification, risk_question = _risk_for(intent, target, state_snapshot)
    voice_needs_clarification, voice_question = _voice_needs_clarification(envelope, intent)
    missing_target = intent in {"open_app", "close_app", "switch_app"} and not target
    needs_clarification = bool(risk_needs_clarification or voice_needs_clarification or missing_target)
    clarification_question = risk_question or voice_question
    if missing_target and not clarification_question:
        clarification_question = "你想操作哪个应用？"

    review_session_id = _new_id("review")
    candidate_intents = [intent]
    candidate_entities = [target] if target else []
    active_review_roles = review_roles or _review_roles_for(
        envelope=envelope,
        intent=intent,
        task_type=task_type,
        target=target,
        risk=risk,
        missing_target=missing_target,
        needs_clarification=needs_clarification,
        memory_bundle=memory_bundle,
    )
    reviews: list[RoleAgentReview] = []
    for role_id in active_review_roles:
        review_input = RoleAgentReviewInput(
            review_session_id=review_session_id,
            turn_id=envelope.turn_id,
            role_id=role_id,
            input_envelope=envelope,
            state_snapshot=state_snapshot,
            memory_bundle=memory_bundle,
            candidate_intents=candidate_intents,
            candidate_entities=candidate_entities,
        )
        reviews.append(
            _review_as_role(
                review_input=review_input,
                intent=intent,
                task_type=task_type,
                target=target,
                tool=tool,
                risk=risk,
                needs_clarification=needs_clarification,
                clarification_question=clarification_question,
            )
        )

    selected_roles = _selected_roles_for(intent)
    summary = ReviewSummary(
        review_session_id=review_session_id,
        turn_id=envelope.turn_id,
        reviews=reviews,
        top_intent=intent,
        task_type=task_type,
        target=target,
        selected_roles=selected_roles,
        candidate_tools=[tool] if tool else [],
        risk_level=risk,
        confidence=_summary_confidence(intent, target, needs_clarification),
        needs_clarification=needs_clarification,
        clarification_question=clarification_question,
        conflicts=_collect_conflicts(memory_bundle, state_snapshot, intent, target),
        rationale=_summary_rationale(intent, target, risk, needs_clarification),
    )
    append_event("review_board_summary", envelope.turn_id, summary.to_dict())
    return summary


def _review_roles_for(
    *,
    envelope: AgentInputEnvelope,
    intent: str,
    task_type: str,
    target: dict[str, Any],
    risk: RiskLevel,
    missing_target: bool,
    needs_clarification: bool,
    memory_bundle: RelevantMemoryBundle,
) -> tuple[str, ...]:
    roles = list(_BASE_REVIEW_ROLES)
    if envelope.source == InputSource.VOICE:
        roles.append("VoiceEvidenceAgent")
    if task_type == "app_control":
        roles.extend(["AppAliasResolverAgent", "WindowContextAgent", "AppStateAgent", "AppControlPlannerAgent"])
        if intent == "open_app":
            roles.append("AppLaunchPlannerAgent")
        if intent == "close_app":
            roles.extend(["AppClosePlannerAgent", "ConfirmationAgent"])
    elif task_type == "message_delivery":
        roles.extend(["CommunicationPlannerAgent", "CommunicationWorker", "ConfirmationAgent"])
    elif task_type == "file_operation":
        roles.extend(["FileContextAgent", "FileWorker", "ConfirmationAgent"])
    else:
        roles.extend(["ConversationAgent", "UserFacingReplyAgent"])
    if risk in {RiskLevel.HIGH, RiskLevel.CRITICAL} or missing_target or needs_clarification or memory_bundle.conflicts:
        roles.extend(["ConfirmationAgent", "RecoveryAgent", "RetryPlannerAgent"])
    return tuple(dict.fromkeys(roles))


def _truthy_risk(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "on", "detected", "present", "unsaved"}


def _review_as_role(
    *,
    review_input: RoleAgentReviewInput,
    intent: str,
    task_type: str,
    target: dict[str, Any],
    tool: str,
    risk: RiskLevel,
    needs_clarification: bool,
    clarification_question: str,
) -> RoleAgentReview:
    role_id = review_input.role_id
    rationale: list[str] = []
    evidence: list[dict[str, Any]] = []
    recommended_roles: list[str] = []
    can_execute = False
    confidence = 0.7

    if role_id == "IntentAnalystAgent":
        rationale.append(f"Input maps to intent {intent}.")
        evidence.append({"type": "input_text", "text": _norm_text(review_input.input_envelope)})
        confidence = 0.86 if intent != "conversation" else 0.78
    elif role_id == "EntityResolverAgent":
        if target:
            rationale.append(f"Resolved target {target.get('name')} from {target.get('source')}.")
            evidence.append({"type": "target", **target})
            confidence = 0.9 if target.get("source") == "input_text" else 0.78
        else:
            rationale.append("No concrete target resolved.")
            confidence = 0.42
    elif role_id == "AmbiguityResolverAgent":
        if target and target.get("source") in {"active_window", "recent_action_memory"}:
            rationale.append("Short reference resolved from state or recent memory.")
        elif intent in {"close_app", "switch_app"}:
            rationale.append("Short reference still ambiguous.")
        else:
            rationale.append("No ambiguous reference found.")
        confidence = 0.8 if target else 0.5
    elif role_id == "VoiceEvidenceAgent":
        voice = review_input.input_envelope.modality_evidence.get("voice") if review_input.input_envelope.modality_evidence else None
        rationale.append("Voice evidence reviewed." if voice else "No voice evidence attached.")
        evidence.append({"type": "voice_evidence", "source": review_input.input_envelope.source.value, "voice": voice or {}})
        confidence = 0.7
    elif role_id == "MemoryRecallAgent":
        rationale.append("RelevantMemoryBundle was reviewed as the only memory source for this turn.")
        evidence.append(
            {
                "type": "memory_bundle",
                "recent_actions": len(review_input.memory_bundle.recent_actions),
                "active_tasks": len(review_input.memory_bundle.active_tasks),
                "preferences": len(review_input.memory_bundle.user_preferences),
                "corrections": len(review_input.memory_bundle.corrections),
                "conflicts": len(review_input.memory_bundle.conflicts),
                "confidence": review_input.memory_bundle.confidence,
            }
        )
        confidence = max(0.45, min(0.92, review_input.memory_bundle.confidence or 0.62))
    elif role_id == "PreferenceAgent":
        rationale.append("User preferences and safety preferences were reviewed for default choices.")
        evidence.append(
            {
                "type": "preference_memory",
                "user_preferences": len(review_input.memory_bundle.user_preferences),
                "safety_preferences": len(review_input.memory_bundle.safety_preferences),
                "tool_habits": len(review_input.memory_bundle.tool_habits),
            }
        )
        confidence = 0.72 if review_input.memory_bundle.user_preferences or review_input.memory_bundle.safety_preferences else 0.5
    elif role_id == "CorrectionLearningAgent":
        rationale.append("Correction memory was reviewed; any new correction must be written only through MemoryWriteAgent.")
        evidence.append(
            {
                "type": "correction_memory",
                "corrections": len(review_input.memory_bundle.corrections),
                "aliases": len(review_input.memory_bundle.aliases),
            }
        )
        confidence = 0.75 if review_input.memory_bundle.corrections else 0.5
    elif role_id == "DesktopStateReadAgent":
        rationale.append("Desktop state snapshot was reviewed; no live desktop action is performed in review.")
        evidence.append(
            {
                "type": "state_snapshot",
                "active_window": review_input.state_snapshot.active_window,
                "running_apps_count": len(review_input.state_snapshot.running_apps),
                "recent_app_events_count": len(review_input.state_snapshot.recent_app_events),
            }
        )
        confidence = 0.84 if review_input.state_snapshot.active_window else 0.58
    elif role_id == "WindowContextAgent":
        rationale.append("Window context was reviewed for active-window and unsaved-content signals.")
        evidence.append({"type": "window_context", "active_window": review_input.state_snapshot.active_window})
        confidence = 0.82 if review_input.state_snapshot.active_window else 0.5
    elif role_id == "AppStateAgent":
        rationale.append("App state was reviewed from running apps and recent app events.")
        evidence.append(
            {
                "type": "app_state",
                "running_apps_count": len(review_input.state_snapshot.running_apps),
                "recent_app_events_count": len(review_input.state_snapshot.recent_app_events),
            }
        )
        confidence = 0.78
    elif role_id == "FileContextAgent":
        rationale.append("File context was reviewed without mutating files.")
        if target:
            evidence.append({"type": "file_context", **target})
        confidence = 0.78 if target else 0.5
    elif role_id == "SafetyAgent":
        rationale.append(f"Risk classified as {risk.value}.")
        evidence.append(
            {
                "type": "risk_state",
                "active_window": review_input.state_snapshot.active_window,
                "risk_state": review_input.state_snapshot.risk_state,
            }
        )
        confidence = 0.83
    elif role_id == "PermissionAgent":
        rationale.append("Permission boundary reviewed; final authorization remains with the Cognitive Kernel.")
        evidence.append({"type": "permission_review", "intent": intent, "task_type": task_type, "risk": risk.value})
        confidence = 0.8
    elif role_id == "PrivacyAgent":
        rationale.append("Privacy exposure reviewed for files, contacts, messages, and outbound side effects.")
        evidence.append({"type": "privacy_review", "task_type": task_type, "target_type": target.get("type") if target else ""})
        confidence = 0.82 if task_type in {"message_delivery", "file_operation"} else 0.68
    elif role_id == "ConfirmationAgent":
        rationale.append("Confirmation need reviewed for ambiguity or elevated risk.")
        evidence.append({"type": "confirmation_review", "needs_clarification": needs_clarification, "question": clarification_question})
        confidence = 0.84 if needs_clarification else 0.62
    elif role_id == "AppAliasResolverAgent":
        if target:
            rationale.append(f"App alias resolution produced target {target.get('name')} from {target.get('source')}.")
            evidence.append({"type": "app_alias", **target})
            confidence = 0.88
        else:
            rationale.append("No app alias was resolved.")
            confidence = 0.44
    elif role_id == "AppControlPlannerAgent":
        if task_type == "app_control":
            recommended_roles.append("AppControlExecutorAgent")
            rationale.append(f"App control intent can use {tool}.")
            confidence = 0.84 if target else 0.45
        else:
            rationale.append("Not an app-control task.")
            confidence = 0.5
    elif role_id == "AppLaunchPlannerAgent":
        if intent == "open_app":
            recommended_roles.append("AppControlExecutorAgent")
            rationale.append(f"App launch can be authorized through {tool}.")
            confidence = 0.86 if target else 0.45
        else:
            rationale.append("Not an app-launch task.")
            confidence = 0.45
    elif role_id == "AppClosePlannerAgent":
        if intent == "close_app":
            recommended_roles.append("AppControlExecutorAgent")
            rationale.append(f"App close can be authorized through {tool} after risk review.")
            confidence = 0.84 if target else 0.45
        else:
            rationale.append("Not an app-close task.")
            confidence = 0.45
    elif role_id in {"CommunicationPlannerAgent", "CommunicationWorker"}:
        if task_type == "message_delivery":
            recommended_roles.append("MessageExecutorAgent")
            rationale.append("Communication task can be planned for MessageExecutorAgent after safety and privacy review.")
            evidence.append({"type": "communication_target", **target} if target else {"type": "communication_target"})
            confidence = 0.82 if target.get("recipients") and target.get("message") else 0.55
        else:
            rationale.append("Not a communication task.")
            confidence = 0.45
    elif role_id == "FileWorker":
        if task_type == "file_operation":
            recommended_roles.append("FileExecutorAgent")
            rationale.append("File task can be planned for FileExecutorAgent within WorkOrder scope.")
            evidence.append({"type": "file_target", **target} if target else {"type": "file_target"})
            confidence = 0.82 if target else 0.5
        else:
            rationale.append("Not a file task.")
            confidence = 0.45
    elif role_id == "ConversationAgent":
        if intent == "conversation":
            recommended_roles.append("UserFacingReplyAgent")
            rationale.append("Input should be answered conversationally.")
            confidence = 0.88
        else:
            rationale.append("Input is not primarily conversational.")
            confidence = 0.4
    elif role_id == "UserFacingReplyAgent":
        rationale.append("Final wording should be generated only after TurnClosure or kernel decision boundaries are known.")
        recommended_roles.append("UserFacingReplyAgent")
        confidence = 0.82 if intent == "conversation" else 0.62
    elif role_id == "ConsistencyCheckAgent":
        rationale.append("Consistency between memory, state, target, and intent was reviewed.")
        evidence.append({"type": "consistency", "conflicts": review_input.memory_bundle.conflicts})
        confidence = 0.8 if not review_input.memory_bundle.conflicts else 0.6
    elif role_id in {"RecoveryAgent", "RetryPlannerAgent"}:
        rationale.append("Recovery options are available if execution verification fails or ambiguity remains.")
        evidence.append({"type": "recovery_readiness", "risk": risk.value, "needs_clarification": needs_clarification})
        confidence = 0.74
    else:
        rationale.append(f"{role_id} is registered for this stage but has no specialized review adapter yet.")
        confidence = 0.5

    return RoleAgentReview(
        review_id=_new_id("role_review"),
        review_session_id=review_input.review_session_id,
        turn_id=review_input.turn_id,
        role_id=role_id,
        candidate_intents=[intent],
        candidate_entities=[target] if target else [],
        confidence=confidence,
        risk_level=risk,
        recommended_roles=recommended_roles,
        proposed_task_type=task_type,
        proposed_tool=tool,
        rationale=rationale,
        evidence=evidence,
        needs_clarification=needs_clarification,
        clarification_question=clarification_question,
        can_execute=can_execute,
    )


def _selected_roles_for(intent: str) -> list[str]:
    if intent == "open_app":
        return _dedupe_roles(
            [
                "MemoryRecallAgent",
                "DesktopStateReadAgent",
                "AppAliasResolverAgent",
                "AppLaunchPlannerAgent",
                "AppControlPlannerAgent",
                "SafetyAgent",
                "PermissionAgent",
                "AppControlExecutorAgent",
                "VerificationAgent",
                "AuditAgent",
                "RecoveryAgent",
                "RetryPlannerAgent",
                "MemoryWriteAgent",
                "UserFacingReplyAgent",
            ]
        )
    if intent == "close_app":
        return _dedupe_roles(
            [
                "MemoryRecallAgent",
                "DesktopStateReadAgent",
                "WindowContextAgent",
                "AppStateAgent",
                "AppClosePlannerAgent",
                "SafetyAgent",
                "PermissionAgent",
                "ConfirmationAgent",
                "AppControlExecutorAgent",
                "VerificationAgent",
                "AuditAgent",
                "RecoveryAgent",
                "RetryPlannerAgent",
                "MemoryWriteAgent",
                "UserFacingReplyAgent",
            ]
        )
    if intent == "switch_app":
        return _dedupe_roles(
            [
                "MemoryRecallAgent",
                "DesktopStateReadAgent",
                "WindowContextAgent",
                "AppControlPlannerAgent",
                "SafetyAgent",
                "PermissionAgent",
                "AppControlExecutorAgent",
                "VerificationAgent",
                "AuditAgent",
                "RecoveryAgent",
                "MemoryWriteAgent",
                "UserFacingReplyAgent",
            ]
        )
    if intent == "message_send":
        return _dedupe_roles(
            [
                "MemoryRecallAgent",
                "EntityResolverAgent",
                "CommunicationPlannerAgent",
                "CommunicationWorker",
                "SafetyAgent",
                "PermissionAgent",
                "PrivacyAgent",
                "ConfirmationAgent",
                "MessageExecutorAgent",
                "VerificationAgent",
                "AuditAgent",
                "RecoveryAgent",
                "RetryPlannerAgent",
                "MemoryWriteAgent",
                "UserFacingReplyAgent",
            ]
        )
    if intent == "file_operation":
        return _dedupe_roles(
            [
                "MemoryRecallAgent",
                "EntityResolverAgent",
                "FileContextAgent",
                "FileWorker",
                "SafetyAgent",
                "PermissionAgent",
                "PrivacyAgent",
                "ConfirmationAgent",
                "FileExecutorAgent",
                "VerificationAgent",
                "AuditAgent",
                "RecoveryAgent",
                "RetryPlannerAgent",
                "MemoryWriteAgent",
                "UserFacingReplyAgent",
            ]
        )
    return _dedupe_roles(["MemoryRecallAgent", "ConversationAgent", "UserFacingReplyAgent", "MemoryWriteAgent"])


def _dedupe_roles(roles: list[str]) -> list[str]:
    return list(dict.fromkeys(roles))


def _summary_confidence(intent: str, target: dict[str, Any], needs_clarification: bool) -> float:
    if needs_clarification:
        return 0.52
    if intent in {"open_app", "close_app", "switch_app"} and target:
        return 0.84
    if intent == "conversation":
        return 0.78
    return 0.68


def _collect_conflicts(
    memory_bundle: RelevantMemoryBundle,
    state_snapshot: StateSnapshot,
    intent: str,
    target: dict[str, Any],
) -> list[dict[str, Any]]:
    conflicts = list(memory_bundle.conflicts or [])
    if intent == "close_app" and target.get("source") == "recent_action_memory":
        active = _active_window_app(state_snapshot)
        if active and _normalize_app_name(active) != _normalize_app_name(str(target.get("name") or "")):
            conflicts.append(
                {
                    "conflict_type": "memory_vs_state",
                    "memory_claim": target,
                    "state_claim": {"active_window": active},
                    "severity": "medium",
                    "suggested_resolution": "prefer active_window unless user explicitly says recently opened app",
                }
            )
    return conflicts


def _summary_rationale(intent: str, target: dict[str, Any], risk: RiskLevel, needs_clarification: bool) -> list[str]:
    rationale = [f"ReviewBoard top intent: {intent}.", f"Risk: {risk.value}."]
    if target:
        rationale.append(f"Target resolved: {target.get('name')} via {target.get('source')}.")
    if needs_clarification:
        rationale.append("Execution blocked until ambiguity or safety concern is clarified.")
    return rationale
