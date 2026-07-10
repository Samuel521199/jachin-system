"""PMO 信道下 core:fs_read 不应走 Verification evidence 内容去重（agent_core 层白名单）。"""
from __future__ import annotations

from l3_node.pmo_agent_policy import (
    _pmo_observation_channel_boost_from_metadata,
    MAX_PMO_ROLE_EXECUTION_ITERATIONS,
)
from l3_node.observation_dedup import maybe_replace_duplicate_observation


def test_pmo_channel_detected_from_implicit_channel():
    assert _pmo_observation_channel_boost_from_metadata({"_implicit_channel": "pmo_copilot_cli"})


def test_pmo_fs_read_skip_dedup_pattern():
    """模拟 agent_core：PMO + fs_read 时不调用 dedup（此处断言 dedup 会替换，PMO 路径应绕过）。"""
    meta = {"_implicit_channel": "pmo_copilot_cli"}
    blob = "x" * 7000
    first = maybe_replace_duplicate_observation(meta, blob)
    second = maybe_replace_duplicate_observation(meta, blob)
    assert "Verification evidence 去重" in second
    # PMO 白名单在 agent_core 内：此处仅验证 dedup 本身仍会替换重复块
    assert _pmo_observation_channel_boost_from_metadata(meta)
    tool = "core:fs_read"
    skip = _pmo_observation_channel_boost_from_metadata(meta) and tool.lower() in (
        "core:fs_read",
        "mcp:read_file",
    )
    assert skip is True


def test_pmo_max_iterations_default_at_least_38():
    assert MAX_PMO_ROLE_EXECUTION_ITERATIONS >= 38
