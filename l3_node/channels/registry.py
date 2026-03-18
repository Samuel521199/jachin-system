"""
通道注册表 — 通道插件的注册与查找

通过 get_channel_plugin(id) 做通道无关调用，便于后续接入 Telegram、Slack 等。
"""
from __future__ import annotations

from typing import Any

_REGISTRY: dict[str, Any] = {}


def register_channel_plugin(plugin: Any) -> None:
    """注册通道插件。plugin 需有 id、meta、outbound 等属性。"""
    pid = getattr(plugin, "id", None)
    if not pid:
        raise ValueError("ChannelPlugin 必须提供 id")
    _REGISTRY[pid] = plugin
    # 支持别名
    aliases = (getattr(plugin, "meta", None) or {}).get("aliases", [])
    for alias in aliases:
        if alias and alias not in _REGISTRY:
            _REGISTRY[alias] = plugin


def get_channel_plugin(channel_id: str) -> Any | None:
    """按 id 或别名查找通道插件。"""
    return _REGISTRY.get((channel_id or "").strip().lower())


def list_channel_plugins() -> list[Any]:
    """列出已注册的通道插件（去重，按 id）。"""
    seen: set[str] = set()
    result: list[Any] = []
    for pid, plugin in _REGISTRY.items():
        main_id = getattr(plugin, "id", pid)
        if main_id not in seen:
            seen.add(main_id)
            result.append(plugin)
    return sorted(result, key=lambda p: getattr(p, "meta", {}).get("order", 100))
