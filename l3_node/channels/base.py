"""
通道抽象层 — 多通道通讯架构基础

参考 OpenClaw ChannelPlugin + OutboundAdapter 模式。
各通道（lark、email、未来 telegram 等）实现统一接口，通过 registry 注册与查找。
"""
from __future__ import annotations

from typing import Any, Protocol


class OutboundAdapter(Protocol):
    """通道出站适配器 — 负责消息投递"""

    def send_text(self, target: str, text: str, **kwargs: Any) -> dict[str, Any]:
        """发送文本消息。返回 {"status": "success", ...} 或 {"status": "error", "error": "..."}"""
        ...


class ChannelPlugin(Protocol):
    """通道插件 — 统一通道接口"""

    id: str
    meta: dict[str, Any]
    outbound: OutboundAdapter | None
