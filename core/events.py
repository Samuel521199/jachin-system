"""
Jachin Nexus V2 - 全局事件发布器

供 inventory_scanner、sync_daemon 等模块在热重载、技能更新时触发 UI 同步事件，
由 core.event_broadcaster 通过 SSE 推送给 L3 客户端。
"""
from __future__ import annotations

from typing import Any


def emit_ui_sync_event(event_type: str, message: str, **extra: Any) -> None:
    """
    触发 UI 同步事件，通过 SSE 推送给所有连接的 L3 客户端。

    Args:
        event_type: 事件类型，如 INVENTORY_UPDATED
        message: 人类可读消息
        **extra: 额外字段
    """
    try:
        from core.event_broadcaster import broadcast_event, build_inventory_updated_event
        if event_type == "INVENTORY_UPDATED":
            payload = build_inventory_updated_event(message, **extra)
        else:
            from datetime import datetime, timezone
            payload = {
                "event": event_type,
                "type": event_type,
                "message": message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                **extra,
            }
        broadcast_event(payload)
    except ImportError:
        pass
    except Exception:
        pass
