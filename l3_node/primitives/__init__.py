"""
L3 执行层四大原语：tools、mcp、skills、agent_tasks。

聚合导出（原 ``l3_node.skills`` 包已移除，请使用本包或各子模块显式路径）。
"""
from __future__ import annotations

from l3_node.primitives.mcp.registry import MCPToolRegistry, get_mcp_registry
from l3_node.primitives.tools.loader import (
    build_tools_description,
    get_hr_invoke_defaults,
    is_tool_allowed,
    load_skills_for_ui,
    load_tools,
    run_tool,
)

__all__ = [
    "load_tools",
    "load_skills_for_ui",
    "run_tool",
    "build_tools_description",
    "get_hr_invoke_defaults",
    "is_tool_allowed",
    "MCPToolRegistry",
    "get_mcp_registry",
]
