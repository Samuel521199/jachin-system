"""
IM 通道抽象 — Lark/Telegram 等同维度

各通道实现 InboundIMChannel 接口，L3 启动时按配置启动入站接收。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable

from typing import TypedDict


class IMChannelConfig(TypedDict, total=False):
    """通道配置基类，各通道可扩展"""
    enabled: bool
    chat_ids: list[str]  # 多机共享时，本节点只处理这些 chat_id


class InboundIMChannel(ABC):
    """入站 IM 通道抽象 — 接收用户消息"""

    id: str = ""
    label: str = ""

    @abstractmethod
    def start(
        self,
        config: dict[str, Any],
        on_message: Callable[[str, str, str], None],
    ) -> None:
        """
        启动入站接收，阻塞直到进程退出。
        on_message(text, chat_id, user_id) 由实现方在收到消息时调用；**chat_id 为会话 ID**（常配 LARK_CHAT_ID）。
        """
        ...

    def should_handle_chat(self, config: dict[str, Any], chat_id: str) -> bool:
        """
        多机共享时，判断本节点是否应处理该 chat_id。
        chat_ids 为空则处理全部；非空则仅处理列表中的。
        """
        allowed = config.get("chat_ids")
        if not allowed or not isinstance(allowed, list):
            return True
        return (chat_id or "").strip() in [str(c).strip() for c in allowed if c]
