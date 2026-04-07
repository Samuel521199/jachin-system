"""
L3 执行层四大原语：tools、mcp、skills、agent_tasks。

域技能与 MCP 实现位于 ``l3_node.primitives.skills`` / ``l3_node.primitives.mcp``；兼容旧导入仍可用 ``l3_node.skills`` / ``l3_node.mcp_tools``（薄转发）。
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
