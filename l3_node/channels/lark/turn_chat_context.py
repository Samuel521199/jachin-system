"""
RoleExecutionAgent 单轮内绑定「当前会话的 Lark chat_id」，供 util:lark_send_text 等读取。

WebSocket 已将 lark_chat_id 写入 run_agent metadata，但 OpenAI function.name 为
util_lark_send_text 时，agent_core 注入分支可能未命中；此处用 ContextVar 兜底。
"""
from __future__ import annotations

import contextvars

_lark_chat_for_tools: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_lark_chat_for_tools", default=""
)


def bind_lark_chat_id_for_tools(chat_id: str) -> contextvars.Token[str]:
    return _lark_chat_for_tools.set((chat_id or "").strip())


def reset_lark_chat_id_for_tools(token: contextvars.Token[str]) -> None:
    _lark_chat_for_tools.reset(token)


def peek_lark_chat_id_for_tools() -> str:
    return (_lark_chat_for_tools.get() or "").strip()
