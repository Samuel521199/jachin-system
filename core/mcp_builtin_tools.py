"""
L2 内置 MCP 工具

read_file（含 PDF 提取）已下放至 L3 本地执行，L2 不再提供。
L3 wasm_runner 的 mcp_read_file 优先本地 core.pdf_extractor，L2 仅作回退。
"""
from __future__ import annotations

from typing import Any


def get_builtin_tools() -> list[dict[str, Any]]:
    """返回内置 MCP 工具列表（read_file 已下放 L3，当前为空）。"""
    return []


async def invoke_builtin_tool(tool_name: str, arguments: dict[str, Any]) -> str:
    """执行内置 MCP 工具。"""
    raise ValueError(f"未知内置工具: {tool_name}")


def is_builtin_tool(tool_name: str) -> bool:
    """判断是否为内置工具。"""
    return False
