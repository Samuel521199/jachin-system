"""
§6.1 轻量路由文本：澄清态挂助理问句摘要；短句指代时拼最近一轮上下文（非深度 coref）。
"""
from __future__ import annotations

import re
from typing import Any

from l3_node.intent_gateway.bundle import SystemState

_PRONOUN_RE = re.compile(r"[这那它其此彼上面刚才之前那个这个该各此条本条]", re.UNICODE)


def _last_assistant_snippet(prior_messages: list[dict[str, Any]], max_chars: int = 360) -> str:
    for m in reversed(prior_messages or []):
        if (m.get("role") or "").strip().lower() != "assistant":
            continue
        raw = m.get("content")
        if raw is None:
            continue
        s = raw if isinstance(raw, str) else str(raw)
        s = s.strip()
        if not s:
            continue
        return s[:max_chars]
    return ""


def compute_routing_utterance(
    *,
    user_input: str,
    prior_messages: list[dict[str, Any]],
    system_state: SystemState,
) -> str:
    ui = (user_input or "").strip()
    if not ui:
        return ui
    last_a = _last_assistant_snippet(prior_messages)

    if system_state == SystemState.AWAITING_CLARIFICATION and last_a:
        return f"[澄清上下文]\n{last_a}\n---\n用户答复: {ui}"

    if len(ui) <= 48 and _PRONOUN_RE.search(ui) and last_a:
        last_u = ""
        for m in reversed(prior_messages or []):
            if (m.get("role") or "").strip().lower() == "user":
                c = m.get("content")
                last_u = (c if isinstance(c, str) else str(c or "")).strip()[:200]
                break
        if last_u:
            return f"[指代上下文]\n上一轮用户: {last_u}\n上一轮助理摘要: {last_a[:280]}\n---\n当前用户: {ui}"

    return ui
