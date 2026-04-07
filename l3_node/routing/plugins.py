"""
领域入站插件：在 run_agent 前链式调用（可选）。
签名: async def plugin(ctx: dict) -> None；ctx 含 user_input, messages, prior_messages, tools, engine, kwargs 等。
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

InboundPlugin = Callable[[dict[str, Any]], Awaitable[None]]

_PLUGINS: list[InboundPlugin] = []


def register_inbound_plugin(fn: InboundPlugin) -> InboundPlugin:
    _PLUGINS.append(fn)
    return fn


def clear_inbound_plugins_for_tests() -> None:
    _PLUGINS.clear()


async def apply_registered_plugins(ctx: dict[str, Any]) -> None:
    for p in list(_PLUGINS):
        try:
            await p(ctx)
        except Exception as e:
            logger.warning("[RoutingPlugins] 插件 %s 异常: %s", getattr(p, "__name__", p), e)
