"""§11.3 补偿动作白名单：仅 Registry 登记 ID → 可调用处理器。"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Optional

CompensationHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class CompensationActionRegistry:
    def __init__(self) -> None:
        self._handlers: Dict[str, CompensationHandler] = {}

    def register(self, action_id: str, fn: CompensationHandler) -> None:
        aid = (action_id or "").strip()
        if aid:
            self._handlers[aid] = fn

    def get(self, action_id: str) -> Optional[CompensationHandler]:
        return self._handlers.get((action_id or "").strip())


_GLOBAL = CompensationActionRegistry()


def get_compensation_registry() -> CompensationActionRegistry:
    return _GLOBAL
