"""Natural-language adapter for desktop companion replies.

Routing, slot filling, and safety layers should keep returning structured or
template-safe messages.  This adapter is the last small presentation layer for
voice/companion surfaces so those messages do not sound like internal state.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any


_TECH_MARKERS_RE = re.compile(
    r"Task Preview|Router Evidence|pending_required_slots|slot_filling|"
    r"当前有一个没补全的任务|缺少的内容|Abort_Intent|workflow|task_type",
    re.I,
)


def reply_needs_companion_adaptation(text: str) -> bool:
    return bool(_TECH_MARKERS_RE.search(str(text or "")))


def deterministic_companion_reply(base_msg: str, *, user_input: str = "") -> str:
    msg = str(base_msg or "").strip()
    user = str(user_input or "").strip()
    if re.search(r"当前有一个没补全的任务|缺少的内容|pending_required_slots|slot_filling", msg, re.I):
        if user:
            return f"我听到你刚才说的是“{user}”。前面还有个没补完的任务，我先不乱接着执行；你可以说“取消”，或者直接把这次的新指令再说完整一点。"
        return "前面还有个没补完的任务，我先不乱接着执行。你可以说“取消”，或者直接把新的完整指令告诉我。"
    if re.search(r"Abort_Intent|多次|未补齐|缺少", msg, re.I):
        return "这个任务缺的信息太多，我先替你停住了。你可以换成一句完整的新指令再来。"
    cleaned = re.sub(r"Router Evidence:.*", "", msg, flags=re.I | re.S).strip()
    cleaned = re.sub(r"Task Preview:.*?(?=\n\S|$)", "", cleaned, flags=re.I | re.S).strip()
    return cleaned or msg


async def adapt_companion_reply_async(
    *,
    base_msg: str,
    user_input: str = "",
    engine: Any = None,
    reason: str = "",
) -> str:
    """Polish a deterministic router/gate reply for companion voice.

    The model is only allowed to rewrite the wording.  It must not add claims
    that a task was executed.
    """
    base = str(base_msg or "").strip()
    if not base:
        return base
    fallback = deterministic_companion_reply(base, user_input=user_input)
    if engine is None:
        return fallback
    messages = [
        {
            "role": "system",
            "content": (
                "你是 Jachin 的陪伴语音回复润色器。把系统/路由层说明改写成一两句自然中文。"
                "不要输出 Markdown，不要出现 task_type、workflow、pending、Router Evidence 等内部词。"
                "不要声称已经执行任何操作；只说明当前需要确认、取消或重新说完整指令。"
                "语气自然、短一点，像实时语音助手。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"用户刚才说：{user_input[:500]}\n"
                f"触发原因：{reason[:120]}\n"
                f"系统原文：{base[:1000]}"
            ),
        },
    ]
    async def _call() -> str:
        raw = await engine.generate_response(
            messages,
            tools=None,
            temperature=0.45,
            max_tokens=180,
            l3_call_purpose="desktop_companion_reply_adapter",
        )
        return (raw.get("content", raw) if isinstance(raw, dict) else str(raw or "")).strip()

    try:
        text = await asyncio.wait_for(_call(), timeout=2.5)
    except Exception:
        return fallback
    if not text or reply_needs_companion_adaptation(text):
        return fallback
    return text[:500].strip() or fallback
