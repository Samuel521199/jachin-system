# -*- coding: utf-8 -*-
"""只读 SubAgent 工具层硬隔离。"""
from __future__ import annotations

from l3_node.primitives.multi_agent.readonly_agent import (
    filter_tools_for_readonly_subagent,
    is_readonly_subagent_role,
    is_write_or_side_effect_tool,
    readonly_tool_block_observation,
    sanitize_allowed_skills_for_readonly,
)


def test_is_readonly_role_by_prefix():
    assert is_readonly_subagent_role("readonly_analyst")
    assert is_readonly_subagent_role("readonly_custom")
    assert not is_readonly_subagent_role("analyst")


def test_write_tools_detected():
    assert is_write_or_side_effect_tool("core:fs_write")
    assert is_write_or_side_effect_tool("core:shell_exec")
    assert is_write_or_side_effect_tool("mcp:write_query")
    assert is_write_or_side_effect_tool("util:lark_send_text")
    assert not is_write_or_side_effect_tool("core:fs_read")
    assert not is_write_or_side_effect_tool("mcp:read_query")


def test_sanitize_allowed_skills_strips_write():
    allowed = [
        "core:fs_read",
        "core:fs_write",
        "core:shell_exec",
        "core:local_memory_search",
        "mcp:*",
    ]
    out = sanitize_allowed_skills_for_readonly(allowed)
    assert "core:fs_read" in out
    assert "core:local_memory_search" in out
    assert "core:fs_write" not in out
    assert "core:shell_exec" not in out
    assert "mcp:*" not in out


def test_filter_tools_for_readonly_subagent():
    tools = [
        {"id": "core:fs_read", "desc": "read"},
        {"id": "core:fs_write", "desc": "write"},
        {"id": "core:local_memory_search", "desc": "mem"},
    ]
    kept = filter_tools_for_readonly_subagent(tools)
    ids = {t["id"] for t in kept}
    assert ids == {"core:fs_read", "core:local_memory_search"}


def test_readonly_block_observation_json():
    obs = readonly_tool_block_observation("core:fs_write")
    assert "readonly_subagent_forbidden" in obs
    assert "core:fs_write" in obs
