"""前台同步工具超时策略（默认 5s，可配置与豁免）。见 docs/前台闲聊与后台重负荷任务的物理隔离与背压熔断.md。"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_EXEMPT_CHANNELS = frozenset({"background_task", "delegate_sub_agent"})

_DEFAULT_ALLOW_PREFIXES = (
    "jpp:",
    "core:workflow_run",
    "core:domain_workflow_run",
    "core:submit_background_task",
    "core:check_background_task",
)

# 已废弃默认子串豁免（易误伤如 mcp:atom_download_*）；请改用 MCP 工具元数据 long_running 或 long_running_tool_ids。
_DEFAULT_ALLOW_SUBSTRINGS: tuple[str, ...] = ()


def _nexus_path() -> Path:
    try:
        from l3_node.jachin_config import get_jachin_root

        return get_jachin_root() / "nexus_config.json"
    except ImportError:
        import os

        return Path(os.environ.get("JACHIN_HOME", str(Path.home() / ".jachin"))) / "nexus_config.json"


def load_foreground_tools_config() -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "sync_timeout_sec": 5.0,
        "enabled": True,
        "exempt_channels": list(_DEFAULT_EXEMPT_CHANNELS),
        "allow_prefixes": list(_DEFAULT_ALLOW_PREFIXES),
        "allow_substrings": list(_DEFAULT_ALLOW_SUBSTRINGS),
        # mcp:fetch 常拉取大页/XML，易超默认 5s；Tavily 等检索工具由 MCP 元数据 long_running 声明
        "long_running_tool_ids": ["mcp:fetch"],
    }
    p = _nexus_path()
    if not p.exists():
        return cfg
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        ft = raw.get("foreground_tools")
        if isinstance(ft, dict):
            if "enabled" in ft:
                cfg["enabled"] = bool(ft["enabled"])
            if ft.get("sync_timeout_sec") is not None:
                try:
                    v = float(ft["sync_timeout_sec"])
                    cfg["sync_timeout_sec"] = max(0.0, min(300.0, v))
                except (TypeError, ValueError):
                    pass
            if isinstance(ft.get("exempt_channels"), list):
                cfg["exempt_channels"] = [str(x).strip() for x in ft["exempt_channels"] if str(x).strip()]
            if isinstance(ft.get("allow_prefixes"), list):
                cfg["allow_prefixes"] = [str(x).strip().lower() for x in ft["allow_prefixes"] if str(x).strip()]
            if isinstance(ft.get("allow_substrings"), list):
                cfg["allow_substrings"] = [str(x).strip().lower() for x in ft["allow_substrings"] if str(x).strip()]
            if isinstance(ft.get("long_running_tool_ids"), list):
                cfg["long_running_tool_ids"] = [
                    str(x).strip().lower() for x in ft["long_running_tool_ids"] if str(x).strip()
                ]
    except Exception as e:
        logger.debug("[ForegroundTools] 读取配置失败: %s", e)
    return cfg


def channel_exempt_from_timeout(channel: str, cfg: dict[str, Any] | None = None) -> bool:
    cfg = cfg or load_foreground_tools_config()
    ch = (channel or "").strip()
    exempt = cfg.get("exempt_channels") or []
    return ch in exempt or ch in _DEFAULT_EXEMPT_CHANNELS


def tool_bypasses_foreground_timeout(
    tool_id: str,
    cfg: dict[str, Any] | None = None,
    *,
    mcp_declares_long_running: bool = False,
) -> bool:
    """
    豁免顺序：通道在外层判断；此处为工具级。
    - Native：前缀（workflow 等）
    - MCP：注册元数据 long_running（mcp_declares_long_running）或 nexus long_running_tool_ids
    - 遗留：allow_substrings（仅当用户在 nexus_config 中显式配置时生效）
    """
    cfg = cfg or load_foreground_tools_config()
    tid = (tool_id or "").strip().lower()
    if not tid:
        return True
    if mcp_declares_long_running:
        return True
    lr_ids = {str(x).strip().lower() for x in (cfg.get("long_running_tool_ids") or []) if str(x).strip()}
    if tid in lr_ids:
        return True
    for p in cfg.get("allow_prefixes") or []:
        if tid.startswith(str(p).lower()):
            return True
    for s in cfg.get("allow_substrings") or []:
        if str(s).lower() in tid:
            return True
    return False
