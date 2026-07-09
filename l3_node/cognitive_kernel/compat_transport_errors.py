"""Error formatting for the compatibility tool transport.

The compatibility text transport is still a bridge for tools that have not yet
been promoted to a direct RoleExecutor path.  Keep user-facing and evidence
messages here so ``agent_core.py`` does not keep accumulating policy text.
"""

from __future__ import annotations


def format_tool_transport_error(tool: str, exc: BaseException) -> str:
    """Return a clear observation for a tool transport exception."""

    return (
        f"[工具执行失败] {type(exc).__name__}: {exc}\n"
        f"tool={tool or '<unknown>'}\n"
        "这次工具调用已被转换为失败 Observation，主进程不会因此退出。"
        "如果访问的是网络、本地窗口或外部服务，请检查网络、权限、窗口状态或稍后重试。"
    )


def transport_exception_section_title() -> str:
    """Human-readable Evidence/terminal section title."""

    return "[L3 Agent] 工具调度异常（已转为 Observation，避免进程退出）"
