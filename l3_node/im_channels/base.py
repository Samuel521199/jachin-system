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

        - 默认（``exclusive_sessions`` 未开）：本机为**默认节点**，处理长连接上的全部会话；
          ``chat_ids`` 仅标记本机明确绑定的会话，未绑定的会话仍由本机处理。
        - ``exclusive_sessions=true`` 且 ``chat_ids`` 非空：白名单，仅处理列表内会话。
        """
        if not config.get("exclusive_sessions"):
            return True
        allowed = config.get("chat_ids")
        if not allowed or not isinstance(allowed, list):
            return True
        ids = [str(c).strip() for c in allowed if c]
        if not ids:
            return True
        return (chat_id or "").strip() in ids
