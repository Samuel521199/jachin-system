"""
Jachin Nexus V2 - L3 技能加载器

将 Native Core 与可扩展技能转化为大模型可识别的 tools 格式。
"""
from __future__ import annotations

from l3_node.skills.loader import (
    build_tools_description,
    get_hr_invoke_defaults,
    is_tool_allowed,
    load_tools,
    load_skills_for_ui,
    run_tool,
)
from l3_node.skills.mcp_registry import MCPToolRegistry, get_mcp_registry

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
