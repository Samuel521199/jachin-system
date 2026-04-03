"""
MCP 工具 locality：LOCAL_PINNED 禁止跨节点委托（Pull / HTTP）。

与 l3_node.primitives.mcp.registry.L3_LOCAL_MCP_TOOLS 保持名称一致（仅工具名，无 mcp: 前缀）。
可选覆盖：~/.jachin/mcp_tool_locality.json
  {"local_pinned": ["extra_tool"], "routable_allow_delegate": ["some_tool"]}
routable_allow_delegate 从默认 pinned 集合中移除（高级用法）。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import FrozenSet

logger = logging.getLogger(__name__)

_OVERRIDES_PATH = Path.home() / ".jachin" / "mcp_tool_locality.json"

# 与 L3_LOCAL_MCP_TOOLS 同步：依赖本机 FS/CDP/调度状态的工具不得委派到他机
_DEFAULT_LOCAL_PINNED: FrozenSet[str] = frozenset({
    "read_file",
    "atom_post_job_boss",
    "atom_greet_recommend_boss",
    "add_automated_recruitment_task",
    "hr_scheduler_send_confirm_prompt",
    "stop_automated_recruitment",
    "get_recruitment_job_memory",
    "list_hr_scheduler_suspended_jobs",
    "resume_hr_job_scheduler",
    "atom_web_scraper",
    "atom_lark_notifier",
    "atom_email_sender",
    "atom_bi_project_context",
})


def _normalized_tool_name(tool_name: str) -> str:
    return (tool_name or "").strip().replace("mcp:", "", 1).strip()


def local_pinned_tool_names() -> FrozenSet[str]:
    extra: set[str] = set()
    allow_delegate: set[str] = set()
    if _OVERRIDES_PATH.exists():
        try:
            data = json.loads(_OVERRIDES_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for x in data.get("local_pinned") or []:
                    if isinstance(x, str) and x.strip():
                        extra.add(_normalized_tool_name(x))
                for x in data.get("routable_allow_delegate") or []:
                    if isinstance(x, str) and x.strip():
                        allow_delegate.add(_normalized_tool_name(x))
        except Exception as e:
            logger.debug("[McpLocality] 读取 %s 失败: %s", _OVERRIDES_PATH, e)
    base = set(_DEFAULT_LOCAL_PINNED) | extra
    return frozenset(base - allow_delegate)


def is_tool_local_pinned(tool_name: str) -> bool:
    return _normalized_tool_name(tool_name) in local_pinned_tool_names()
