"""Voice task replan patch builder.

Always-on voice lets the user correct an in-flight task with short utterances
such as "改成发给 Neil" or "内容换成你好".  This module converts that kind of
utterance into a structured patch before the normal planner sees it.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from l3_node.cognitive_kernel.ledger import append_event
from l3_node.cognitive_kernel.paths import state_dir


ReplanPatchType = Literal[
    "recipient_change",
    "content_change",
    "app_change",
    "mixed_change",
    "unknown",
]


@dataclass(slots=True)
class VoiceTaskReplanPatch:
    is_replan: bool
    patch_type: ReplanPatchType
    raw_text: str
    target_task_id: str = ""
    target_task_title: str = ""
    recipient_add: list[str] = field(default_factory=list)
    recipient_remove: list[str] = field(default_factory=list)
    recipient_replace: list[str] = field(default_factory=list)
    message_content: str = ""
    app: str = ""
    confidence: float = 0.0
    replanned_instruction: str = ""
    requires_confirmation: bool = False
    reasons: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_voice_task_replan_patch(
    text: str,
    *,
    voice_context: dict[str, Any] | None = None,
    interruption_decision: dict[str, Any] | None = None,
    run_id: str = "",
) -> VoiceTaskReplanPatch:
    ctx = voice_context or {}
    decision = interruption_decision or {}
    raw = str(text or "").strip()
    active = _active_task_context(ctx, decision)
    target = _target_task(active, decision)
    target_id = str(target.get("id") or decision.get("target_task_id") or "").strip()
    target_title = str(target.get("title") or decision.get("target_task_title") or "").strip()
    cn_patch = _extract_normal_chinese_patch(raw)
    contact_hint = _contact_replan_hint(raw, target_title)

    reasons: list[str] = []
    if str(decision.get("action") or "") != "modify_current_task" and not cn_patch and not contact_hint:
        reasons.append("not_modify_current_task")
        patch = VoiceTaskReplanPatch(
            is_replan=False,
            patch_type="unknown",
            raw_text=raw,
            target_task_id=target_id,
            target_task_title=target_title,
            confidence=0.0,
            reasons=reasons,
            evidence={"run_id": run_id},
        )
        _append_replan_event(patch)
        return patch
    if not target_id and not target_title:
        reasons.append("no_active_task_target")

    recipient_replace = _extract_replacement_recipients(raw)
    recipient_remove = _extract_removed_recipients(raw)
    recipient_add = _extract_added_recipients(raw)
    if cn_patch.get("recipient_replace"):
        recipient_replace = list(cn_patch["recipient_replace"])
    if cn_patch.get("recipient_remove"):
        recipient_remove = _dedupe([*recipient_remove, *list(cn_patch["recipient_remove"])])
    if cn_patch.get("recipient_add") and not recipient_replace:
        recipient_add = _dedupe([*recipient_add, *list(cn_patch["recipient_add"])])
    if contact_hint and not recipient_replace and not recipient_add:
        recipient_replace = list(contact_hint.get("recipient_replace") or [])
        recipient_remove = _dedupe([*recipient_remove, *list(contact_hint.get("recipient_remove") or [])])
        if recipient_replace or recipient_remove:
            reasons.append("degraded_contact_replan_hint")
    if recipient_replace:
        reasons.append("recipient_replace_detected")
        recipient_add = []
    elif recipient_add:
        reasons.append("recipient_add_detected")
    if recipient_remove:
        reasons.append("recipient_remove_detected")

    message_content = _extract_message_content(raw)
    if not message_content and cn_patch.get("message_content"):
        message_content = str(cn_patch["message_content"])
    if message_content:
        reasons.append("message_content_detected")

    app = _extract_app(raw)
    if not app and cn_patch.get("app"):
        app = str(cn_patch["app"])
    if app:
        reasons.append("app_detected")

    patch_type = _patch_type(
        recipient_replace=recipient_replace,
        recipient_add=recipient_add,
        recipient_remove=recipient_remove,
        message_content=message_content,
        app=app,
    )
    confidence = _confidence_for(
        patch_type=patch_type,
        target_id=target_id,
        target_title=target_title,
        recipient_replace=recipient_replace,
        recipient_add=recipient_add,
        recipient_remove=recipient_remove,
        message_content=message_content,
        app=app,
    )
    requires_confirmation = confidence < 0.62
    replanned_instruction = _build_replanned_instruction(
        raw=raw,
        target_id=target_id,
        target_title=target_title,
        recipient_replace=recipient_replace,
        recipient_add=recipient_add,
        recipient_remove=recipient_remove,
        message_content=message_content,
        app=app,
    )
    patch = VoiceTaskReplanPatch(
        is_replan=bool(patch_type != "unknown" and (target_id or target_title)),
        patch_type=patch_type,
        raw_text=raw,
        target_task_id=target_id,
        target_task_title=target_title,
        recipient_add=recipient_add,
        recipient_remove=recipient_remove,
        recipient_replace=recipient_replace,
        message_content=message_content,
        app=app,
        confidence=confidence,
        replanned_instruction=replanned_instruction,
        requires_confirmation=requires_confirmation,
        reasons=reasons or ["modify_current_task_without_slots"],
        evidence={
            "run_id": run_id,
            "active_task_context": active,
            "interruption_decision": decision,
        },
    )
    _append_replan_event(patch)
    return patch


def _contact_replan_hint(text: str, target_title: str) -> dict[str, list[str]]:
    """Recover a recipient-change patch when noisy STT kept only names.

    Always-on voice often loses short Chinese operation words but preserves
    English contact names. If an active task title is already a send-message
    task, a later fragment like "Neil Vivian" is a strong hint that the user is
    correcting recipients, not starting a new task.
    """

    names = _known_contact_names()
    if not names:
        return {}
    raw_hits = _names_in_text(text, names)
    if not raw_hits:
        return {}
    title_hits = _names_in_text(target_title, names)
    title_low = str(target_title or "").lower()
    send_task = bool(title_hits) and (
        "lark" in title_low
        or "message" in title_low
        or "send" in title_low
        or "\u53d1" in target_title
        or "\u6d88\u606f" in target_title
    )
    if not send_task:
        return {}
    replace = [name for name in raw_hits if name not in title_hits]
    remove = [name for name in raw_hits if name in title_hits and replace]
    if not replace:
        return {}
    return {"recipient_replace": replace[:4], "recipient_remove": remove[:4]}


def _known_contact_names() -> list[str]:
    names = ["Neil", "Vivian", "Samuel", "Ethan"]
    try:
        from l3_node.message_contacts import load_message_contacts

        for item in load_message_contacts():
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if name:
                names.append(name)
    except Exception:
        pass
    return _dedupe(names)


def _names_in_text(text: str, names: list[str]) -> list[str]:
    out: list[str] = []
    source = str(text or "")
    low = source.lower()
    for name in names:
        value = str(name or "").strip()
        if not value:
            continue
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(value)}(?![A-Za-z0-9_])", source, re.I):
            out.append(value)
            continue
        if value.lower() in low and len(value) >= 3:
            out.append(value)
    return _dedupe(out)


def apply_voice_task_replan_to_input(user_input: str, patch_payload: dict[str, Any] | VoiceTaskReplanPatch | None) -> str:
    if not patch_payload:
        return user_input or ""
    if isinstance(patch_payload, VoiceTaskReplanPatch):
        patch = patch_payload
    else:
        patch = VoiceTaskReplanPatch(
            is_replan=bool(patch_payload.get("is_replan")),
            patch_type=str(patch_payload.get("patch_type") or "unknown"),  # type: ignore[arg-type]
            raw_text=str(patch_payload.get("raw_text") or user_input or ""),
            target_task_id=str(patch_payload.get("target_task_id") or ""),
            target_task_title=str(patch_payload.get("target_task_title") or ""),
            recipient_add=list(patch_payload.get("recipient_add") or []),
            recipient_remove=list(patch_payload.get("recipient_remove") or []),
            recipient_replace=list(patch_payload.get("recipient_replace") or []),
            message_content=str(patch_payload.get("message_content") or ""),
            app=str(patch_payload.get("app") or ""),
            confidence=float(patch_payload.get("confidence") or 0.0),
            replanned_instruction=str(patch_payload.get("replanned_instruction") or ""),
            requires_confirmation=bool(patch_payload.get("requires_confirmation")),
            reasons=list(patch_payload.get("reasons") or []),
            evidence=dict(patch_payload.get("evidence") or {}),
        )
    if not patch.is_replan or patch.requires_confirmation:
        return user_input or ""
    return patch.replanned_instruction or user_input or ""


def _active_task_context(ctx: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    raw = ctx.get("voice_active_task_context")
    if isinstance(raw, dict):
        _remember_active_task_context(raw)
        return raw
    evidence = decision.get("evidence") if isinstance(decision.get("evidence"), dict) else {}
    raw = evidence.get("active_task_context")
    if isinstance(raw, dict) and isinstance(raw.get("active_tasks"), list) and raw.get("active_tasks"):
        _remember_active_task_context(raw)
        return raw
    return _load_recent_active_task_context()


def _target_task(active: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    tasks = active.get("active_tasks")
    if not isinstance(tasks, list):
        tasks = []
    focused = str(active.get("focused_task_id") or decision.get("target_task_id") or "").strip()
    for item in tasks:
        if isinstance(item, dict) and focused and str(item.get("id") or "") == focused:
            return item
    for item in tasks:
        if isinstance(item, dict):
            return item
    return {}


def _extract_replacement_recipients(text: str) -> list[str]:
    patterns = [
        r"(?:(?:改成|换成|改为)\s*)?(?:改发|转发给|发给|发送给)\s*([A-Za-z0-9_\-\u4e00-\u9fff、,，和与\s]{2,80})",
        r"(?:send\s+to|change\s+to|instead\s+send\s+to)\s+([A-Za-z0-9_\-\s,]{2,80})",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if not m:
            continue
        raw = _strip_recipient_tail(m.group(1))
        names = _split_names(raw)
        if names:
            return names
    return []


def _extract_added_recipients(text: str) -> list[str]:
    patterns = [
        r"(?:再加|加上|也发给|同时发给)\s*([A-Za-z0-9_\-\u4e00-\u9fff、,，和与\s]{2,80})",
        r"(?:also\s+send\s+to|add)\s+([A-Za-z0-9_\-\s,]{2,80})",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if m:
            names = _split_names(_strip_recipient_tail(m.group(1)))
            if names:
                return names
    return []


def _extract_removed_recipients(text: str) -> list[str]:
    patterns = [
        r"(?:不要发给|别发给|不发给|去掉|移除|取消给)\s*([A-Za-z0-9_\-\u4e00-\u9fff、,，和与\s]{2,80})",
        r"(?:do\s+not\s+send\s+to|remove)\s+([A-Za-z0-9_\-\s,]{2,80})",
    ]
    out: list[str] = []
    for pattern in patterns:
        for m in re.finditer(pattern, text, re.I):
            out.extend(_split_names(_strip_recipient_tail(m.group(1))))
    return _dedupe(out)


def _extract_message_content(text: str) -> str:
    patterns = [
        r"(?:内容|消息|正文)\s*(?:改成|换成|改为|是|为|:|：)\s*(.+)$",
        r"(?:message|content)\s*(?:to|is|=|:)\s*(.+)$",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if not m:
            continue
        value = (m.group(1) or "").strip(" ：:。.;；")
        if value:
            return value[:2000]
    quoted = re.findall(r"[`\"“”'‘’](.*?)[`\"“”'‘’]", text)
    if quoted:
        return quoted[-1].strip()[:2000]
    return ""


def _extract_app(text: str) -> str:
    low = text.lower()
    if any(token in low for token in ("lark", "feishu", "飞书")):
        return "Lark"
    if any(token in low for token in ("wechat", "weixin", "微信")):
        return "WeChat"
    if any(token in low for token in ("mail", "email", "outlook", "邮件", "邮箱")):
        return "Email"
    return ""


def _patch_type(
    *,
    recipient_replace: list[str],
    recipient_add: list[str],
    recipient_remove: list[str],
    message_content: str,
    app: str,
) -> ReplanPatchType:
    has_recipient = bool(recipient_replace or recipient_add or recipient_remove)
    has_content = bool(message_content)
    has_app = bool(app)
    count = sum(1 for item in (has_recipient, has_content, has_app) if item)
    if count > 1:
        return "mixed_change"
    if has_recipient:
        return "recipient_change"
    if has_content:
        return "content_change"
    if has_app:
        return "app_change"
    return "unknown"


def _confidence_for(
    *,
    patch_type: ReplanPatchType,
    target_id: str,
    target_title: str,
    recipient_replace: list[str],
    recipient_add: list[str],
    recipient_remove: list[str],
    message_content: str,
    app: str,
) -> float:
    if patch_type == "unknown":
        return 0.35
    score = 0.48
    if target_id or target_title:
        score += 0.18
    if recipient_replace or recipient_add:
        score += 0.16
    if recipient_remove:
        score += 0.08
    if message_content:
        score += 0.12
    if app:
        score += 0.08
    return min(0.94, score)


def _build_replanned_instruction(
    *,
    raw: str,
    target_id: str,
    target_title: str,
    recipient_replace: list[str],
    recipient_add: list[str],
    recipient_remove: list[str],
    message_content: str,
    app: str,
) -> str:
    parts = ["修正当前正在执行的任务。"]
    if target_title:
        parts.append(f"原任务：{target_title}。")
    if target_id:
        parts.append(f"原任务ID：{target_id}。")
    if app:
        parts.append(f"目标应用改为 {app}。")
    if recipient_replace:
        parts.append("收件人改为：" + "、".join(recipient_replace) + "。")
    elif recipient_add:
        parts.append("新增收件人：" + "、".join(recipient_add) + "。")
    if recipient_remove:
        parts.append("不要发送给：" + "、".join(recipient_remove) + "。")
    if message_content:
        parts.append(f"消息内容改为：{message_content}。")
    parts.append(f"用户原始修正话：{raw}")
    return "\n".join(parts)


def _strip_recipient_tail(raw: str) -> str:
    text = str(raw or "").strip()
    text = re.split(
        r"(?:，|,|。|；|;)?\s*(?:内容|消息|正文|不要发给|不要|别发给|别|不发给|不发|不发送|然后|同时|并且|message|content)",
        text,
        maxsplit=1,
        flags=re.I,
    )[0]
    text = re.sub(r"(?:发送|发|消息|信息|message)$", "", text, flags=re.I).strip()
    return text.strip(" ：:。.;；,，")


def _split_names(raw: str) -> list[str]:
    cleaned = str(raw or "").strip()
    if not cleaned:
        return []
    cleaned = re.sub(r"\s+(?:and|with)\s+", ",", cleaned, flags=re.I)
    cleaned = cleaned.replace("和", ",").replace("与", ",").replace("、", ",").replace("，", ",")
    parts = [p.strip(" ：:。.;；") for p in cleaned.split(",")]
    out: list[str] = []
    for part in parts:
        if not part:
            continue
        if len(part) > 40:
            continue
        if part.lower() in {"message", "content", "消息", "内容"}:
            continue
        out.append(part)
    return _dedupe(out)[:8]


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item.strip())
    return out


def _append_replan_event(patch: VoiceTaskReplanPatch) -> None:
    try:
        append_event("voice_task_replan_patch_built", patch.evidence.get("run_id") or "voice", patch.to_dict())
    except Exception:
        pass


def _extract_normal_chinese_patch(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    out: dict[str, Any] = {}
    recipient_replace = _first_names(
        raw,
        [
            r"(?:不是发给|不要发给|别发给)\s*[A-Za-z0-9_\-\u4e00-\u9fff、，,和与\s]{1,80}\s*(?:是|改成|换成|改为|而是)\s*([A-Za-z0-9_\-\u4e00-\u9fff、，,和与\s]{2,80})",
            r"(?:改成|换成|改为)?(?:改发|转发给|发送给|发给)\s*([A-Za-z0-9_\-\u4e00-\u9fff、，,和与\s]{2,80})",
        ],
    )
    if recipient_replace:
        out["recipient_replace"] = recipient_replace
    recipient_remove = _first_names(
        raw,
        [
            r"(?:不要发给|别发给|不发给|移除|取消给)\s*([A-Za-z0-9_\-\u4e00-\u9fff、，,和与\s]{2,80})",
        ],
    )
    if recipient_remove:
        out["recipient_remove"] = recipient_remove
    recipient_add = _first_names(
        raw,
        [
            r"(?:再加|加上|也发给|同时发给)\s*([A-Za-z0-9_\-\u4e00-\u9fff、，,和与\s]{2,80})",
        ],
    )
    if recipient_add:
        out["recipient_add"] = recipient_add
    m = re.search(r"(?:内容|消息|正文)\s*(?:改成|换成|改为|是|为|:|：)\s*(.+)$", raw, re.I)
    if m:
        content = m.group(1).strip(" ，,。.;；")
        if content:
            out["message_content"] = content[:2000]
    low = raw.lower()
    if "lark" in low or "飞书" in raw:
        out["app"] = "Lark"
    elif "微信" in raw or "wechat" in low:
        out["app"] = "WeChat"
    elif "邮箱" in raw or "邮件" in raw or "email" in low or "mail" in low:
        out["app"] = "Email"
    return out


def _first_names(text: str, patterns: list[str]) -> list[str]:
    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if not m:
            continue
        names = _split_normal_names(m.group(1))
        if names:
            return names
    return []


def _split_normal_names(raw: str) -> list[str]:
    text = str(raw or "").strip()
    text = re.split(r"(?:内容|消息|正文|不要|别|不发|然后|同时|并且|，|。)", text, maxsplit=1)[0]
    text = re.sub(r"\s+(?:and|with)\s+", ",", text, flags=re.I)
    for token in ("、", "，", "和", "与"):
        text = text.replace(token, ",")
    names = []
    for part in text.split(","):
        name = part.strip(" ，,。.;；")
        if not name or len(name) > 40:
            continue
        if name in {"内容", "消息", "正文"}:
            continue
        names.append(name)
    return _dedupe(names)[:8]


def _remember_active_task_context(active: dict[str, Any]) -> None:
    tasks = active.get("active_tasks")
    if not isinstance(tasks, list) or not tasks:
        return
    path = _recent_active_context_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(active)
    payload["saved_at_ms"] = int(time.time() * 1000)
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        append_event(
            "voice_active_task_context_remembered",
            "voice",
            {"task_count": len(tasks), "focused_task_id": active.get("focused_task_id") or ""},
        )
    except Exception:
        pass


def _load_recent_active_task_context() -> dict[str, Any]:
    path = _recent_active_context_path()
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    try:
        saved_at_ms = int(payload.get("saved_at_ms") or 0)
    except Exception:
        saved_at_ms = 0
    if saved_at_ms and int(time.time() * 1000) - saved_at_ms > 10 * 60 * 1000:
        return {}
    tasks = payload.get("active_tasks")
    return payload if isinstance(tasks, list) and tasks else {}


def _recent_active_context_path() -> Path:
    return state_dir() / "voice_task_context" / "latest_active_task.json"


# UTF-8-safe override: keep this definition after the legacy mojibake-tolerant
# helper so normal Chinese voice corrections work even when old source comments
# display poorly in Windows terminals.
def _extract_normal_chinese_patch(text: str) -> dict[str, Any]:  # type: ignore[no-redef]
    raw = str(text or "").strip()
    out: dict[str, Any] = {}
    name_class = r"A-Za-z0-9_\-\u4e00-\u9fff\u3001\uff0c,\u548c\u4e0e\s"
    replace_patterns = [
        rf"(?:\u4e0d\u662f\u53d1\u7ed9|\u4e0d\u8981\u53d1\u7ed9|\u522b\u53d1\u7ed9)\s*[{name_class}]{{1,80}}\s*(?:\u662f|\u6539\u6210|\u6362\u6210|\u6539\u4e3a|\u800c\u662f)\s*([{name_class}]{{2,80}})",
        rf"(?:\u6539\u6210|\u6362\u6210|\u6539\u4e3a)?(?:\u6539\u53d1|\u8f6c\u53d1\u7ed9|\u53d1\u9001\u7ed9|\u53d1\u7ed9)\s*([{name_class}]{{2,80}})",
    ]
    recipient_replace = _first_names(raw, replace_patterns)
    if recipient_replace:
        out["recipient_replace"] = recipient_replace
    recipient_remove = _first_names(
        raw,
        [
            rf"(?:\u4e0d\u8981\u53d1\u7ed9|\u522b\u53d1\u7ed9|\u4e0d\u53d1\u7ed9|\u79fb\u9664|\u53d6\u6d88\u7ed9)\s*([{name_class}]{{2,80}})",
        ],
    )
    if recipient_remove:
        out["recipient_remove"] = recipient_remove
    recipient_add = _first_names(
        raw,
        [
            rf"(?:\u518d\u52a0|\u52a0\u4e0a|\u4e5f\u53d1\u7ed9|\u540c\u65f6\u53d1\u7ed9)\s*([{name_class}]{{2,80}})",
        ],
    )
    if recipient_add:
        out["recipient_add"] = recipient_add
    m = re.search(
        r"(?:\u5185\u5bb9|\u6d88\u606f|\u6b63\u6587)\s*(?:\u6539\u6210|\u6362\u6210|\u6539\u4e3a|\u662f|\u4e3a|:|\uff1a)\s*(.+)$",
        raw,
        re.I,
    )
    if m:
        content = m.group(1).strip(" ，,。.;；")
        if content:
            out["message_content"] = content[:2000]
    low = raw.lower()
    if "lark" in low or "\u98de\u4e66" in raw:
        out["app"] = "Lark"
    elif "\u5fae\u4fe1" in raw or "wechat" in low:
        out["app"] = "WeChat"
    elif "\u90ae\u7bb1" in raw or "\u90ae\u4ef6" in raw or "email" in low or "mail" in low:
        out["app"] = "Email"
    return out


def _split_normal_names(raw: str) -> list[str]:  # type: ignore[no-redef]
    text = str(raw or "").strip()
    text = re.split(r"(?:\u5185\u5bb9|\u6d88\u606f|\u6b63\u6587|\u4e0d\u8981|\u522b|\u4e0d\u53d1|\u7136\u540e|\u540c\u65f6|\u5e76\u4e14|\uff0c|\u3002)", text, maxsplit=1)[0]
    text = re.sub(r"\s+(?:and|with)\s+", ",", text, flags=re.I)
    for token in ("\u3001", "\uff0c", "\u548c", "\u4e0e"):
        text = text.replace(token, ",")
    names: list[str] = []
    for part in text.split(","):
        name = part.strip(" ，,。.;；")
        if not name or len(name) > 40:
            continue
        if name in {"\u5185\u5bb9", "\u6d88\u606f", "\u6b63\u6587"}:
            continue
        names.append(name)
    return _dedupe(names)[:8]
