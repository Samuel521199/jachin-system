"""Voice interruption classifier for always-on voice mode.

The agent turns a noisy voice utterance into a small control decision before the
normal task planner sees it.  It is intentionally conservative: only obvious
cancel/pause/chat intents are intercepted.  Ambiguous utterances continue into
the normal Cognitive Kernel path with the decision attached as evidence.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal


VoiceInterruptAction = Literal[
    "none",
    "cancel",
    "pause",
    "resume",
    "modify_current_task",
    "side_chat",
    "confirm_required",
]


@dataclass(slots=True)
class VoiceInterruptionDecision:
    action: VoiceInterruptAction
    is_voice: bool
    active_task_present: bool
    target_task_id: str = ""
    target_task_title: str = ""
    confidence: float = 0.0
    should_intercept: bool = False
    should_cancel_run: bool = False
    user_visible_reply: str = ""
    reasons: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def classify_voice_interruption(
    text: str,
    *,
    voice_context: dict[str, Any] | None = None,
    run_id: str = "",
) -> VoiceInterruptionDecision:
    ctx = voice_context or {}
    normalized = str(text or "").strip()
    compact = _compact(normalized)
    is_voice = _is_voice_context(ctx)
    active = _active_task_context(ctx)
    active_present = bool(active.get("active_tasks"))
    target = _target_task(active)
    stt_confidence = _float_or_none(ctx.get("voice_stt_confidence") or ctx.get("voice_confidence"))

    reasons: list[str] = []
    action: VoiceInterruptAction = "none"
    intercept = False
    cancel_run = False
    confidence = 0.0
    reply = ""

    if not is_voice:
        reasons.append("not_voice_turn")
    elif not active_present:
        reasons.append("no_active_task")
    elif stt_confidence is not None and stt_confidence < 0.42 and _looks_like_control(compact):
        action = "confirm_required"
        intercept = True
        confidence = 0.55
        reasons.append("low_confidence_control_phrase")
        reply = "我听到你可能想打断当前任务，但不够确定。你可以说“确认取消”或重新说一遍。"
    elif _looks_like_cancel(compact):
        action = "cancel"
        intercept = True
        cancel_run = True
        confidence = 0.92
        reasons.append("explicit_cancel_or_stop")
        reply = _cancel_reply(target)
    elif _looks_like_pause(compact):
        action = "pause"
        intercept = True
        cancel_run = True
        confidence = 0.86
        reasons.append("explicit_pause")
        reply = _pause_reply(target)
    elif _looks_like_resume(compact):
        action = "resume"
        intercept = True
        confidence = 0.72
        reasons.append("explicit_resume")
        reply = "收到，你想继续当前任务。我会把它作为续跑请求交给任务链路处理。"
    elif _looks_like_normal_chinese_modify(normalized, compact) or _looks_like_modify(normalized, compact):
        action = "modify_current_task"
        intercept = False
        confidence = 0.78
        reasons.append("modify_current_task_reference")
    elif _looks_like_side_chat(normalized, compact):
        action = "side_chat"
        intercept = True
        confidence = 0.76
        reasons.append("side_chat_during_active_task")
        reply = ""
    elif active_present:
        reasons.append("active_task_but_not_interruption")

    decision = VoiceInterruptionDecision(
        action=action,
        is_voice=is_voice,
        active_task_present=active_present,
        target_task_id=str(target.get("id") or ""),
        target_task_title=str(target.get("title") or ""),
        confidence=confidence,
        should_intercept=intercept,
        should_cancel_run=cancel_run,
        user_visible_reply=reply,
        reasons=reasons,
        evidence={
            "run_id": run_id,
            "input_preview": normalized[:160],
            "compact": compact[:160],
            "stt_confidence": stt_confidence,
            "voice_interaction_mode": ctx.get("voice_interaction_mode") or "",
            "active_task_context": active,
        },
    )
    _append_interruption_event(decision)
    return decision


def _is_voice_context(ctx: dict[str, Any]) -> bool:
    if not ctx:
        return False
    if any(ctx.get(k) for k in ("voice_raw_stt_text", "voice_asr_raw_text", "voice_final_text", "voice_routed_text")):
        return True
    mode = str(ctx.get("voice_interaction_mode") or "").lower()
    if mode in {"continuous_listen", "wake_conversation", "push_to_talk"}:
        return True
    source = str(ctx.get("source") or ctx.get("voice_stt_source") or "").lower()
    return "voice" in source or "stt" in source or source in {"continuous", "wake", "ptt"}


def _active_task_context(ctx: dict[str, Any]) -> dict[str, Any]:
    raw = ctx.get("voice_active_task_context")
    if isinstance(raw, dict):
        return raw
    return {}


def _target_task(active: dict[str, Any]) -> dict[str, Any]:
    tasks = active.get("active_tasks")
    if not isinstance(tasks, list):
        tasks = []
    focused = str(active.get("focused_task_id") or "").strip()
    for item in tasks:
        if isinstance(item, dict) and focused and str(item.get("id") or "") == focused:
            return item
    for item in tasks:
        if isinstance(item, dict):
            return item
    return {}


def _compact(text: str) -> str:
    t = str(text or "").strip().lower()
    return re.sub(r"[\s,，。.!！?？:：;；、…\"'“”‘’]+", "", t)


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _looks_like_control(compact: str) -> bool:
    return _looks_like_cancel(compact) or _looks_like_pause(compact) or _looks_like_resume(compact)


def _looks_like_cancel(compact: str) -> bool:
    if compact in {"取消", "停", "停下", "停止", "停一下", "别执行", "不要执行", "不用了", "算了", "取消任务", "停掉", "stop", "cancel", "abort"}:
        return True
    return bool(re.search(r"(取消|停止|停下|停一下|别继续|别执行|不要执行|不用执行|不用了|算了|先停|停掉)", compact))


def _looks_like_pause(compact: str) -> bool:
    if compact in {"暂停", "暂停一下", "等一下", "先等等", "稍等", "先别动"}:
        return True
    return bool(re.search(r"(暂停|等一下|先等等|稍等|先别动|先别继续)", compact))


def _looks_like_resume(compact: str) -> bool:
    if compact in {"继续", "继续执行", "接着来", "接着做", "恢复", "resume", "continue"}:
        return True
    return bool(re.search(r"(继续|接着|恢复).{0,6}(执行|做|来)?", compact))


def _looks_like_normal_chinese_modify(text: str, compact: str) -> bool:
    pattern = r"(改成|换成|改为|不是.*是|不要发给|别发给|不发给|改发|收件人|内容改|消息改|正文改|重新)"
    return bool(re.search(pattern, text) or re.search(pattern, compact))


def _looks_like_modify(text: str, compact: str) -> bool:
    if re.search(r"(改成|换成|改为|不是.*是|发给|不要发给|别发给|不发给|改发|收件人|内容改|消息改|重新)", text):
        return True
    return bool(re.search(r"(改成|换成|改为|改发|不要发给|别发给|不发给|重新|不是.*是)", compact))


def _looks_like_side_chat(text: str, compact: str) -> bool:
    if len(compact) <= 2:
        return False
    if re.search(r"(问一下|聊一下|你觉得|为什么|是什么|什么意思|怎么样|现在在干嘛|进展如何|解释一下)", text):
        return True
    if text.strip().endswith(("?", "？")) and not _looks_like_control(compact):
        return True
    return False


def _cancel_reply(target: dict[str, Any]) -> str:
    title = str(target.get("title") or "").strip()
    if title:
        return f"收到，我先停止当前任务：{title}。"
    return "收到，我先停止当前任务。"


def _pause_reply(target: dict[str, Any]) -> str:
    title = str(target.get("title") or "").strip()
    if title:
        return f"收到，我先暂停当前任务：{title}。"
    return "收到，我先暂停当前任务。"


def _append_interruption_event(decision: VoiceInterruptionDecision) -> None:
    try:
        from l3_node.cognitive_kernel.ledger import append_event

        append_event(
            "voice_interruption_classified",
            str(decision.evidence.get("run_id") or "voice"),
            decision.to_dict(),
        )
    except Exception:
        pass


def _looks_like_normal_chinese_modify(text: str, compact: str) -> bool:  # type: ignore[no-redef]
    pattern = (
        r"(\u6539\u6210|\u6362\u6210|\u6539\u4e3a|\u4e0d\u662f.*\u662f|"
        r"\u4e0d\u8981\u53d1\u7ed9|\u522b\u53d1\u7ed9|\u4e0d\u53d1\u7ed9|"
        r"\u6539\u53d1|\u6536\u4ef6\u4eba|\u5185\u5bb9\u6539|"
        r"\u6d88\u606f\u6539|\u6b63\u6587\u6539|\u91cd\u65b0)"
    )
    return bool(re.search(pattern, text) or re.search(pattern, compact))
