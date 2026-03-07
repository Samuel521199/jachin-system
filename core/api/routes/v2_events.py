"""
Jachin Nexus V2 - L2 到 L3 的 SSE 消息推送中心

GET /api/v2/events/ui-sync 提供 Server-Sent Events 流，
当 Inventory 热重载、技能更新等事件发生时，实时推送给所有连接的 L3 客户端。
使用 core.event_broadcaster 管理订阅者。
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from core.event_broadcaster import (
    create_subscriber_queue,
    register_subscriber,
    unregister_subscriber,
)

router = APIRouter(prefix="/api/v2/events", tags=["events"])


@router.get("/ui-sync")
async def ui_sync_stream() -> StreamingResponse:
    """
    SSE 流：L3 客户端通过 EventSource 连接，接收 UI 同步事件。
    事件类型: INVENTORY_UPDATED（技能/MCP 热重载完成）
    """
    queue = create_subscriber_queue(maxsize=64)

    async def event_stream():
        await register_subscriber(queue)
        try:
            # 立即发送一次连接成功事件，便于客户端确认
            yield f"data: {json.dumps({'event': 'CONNECTED', 'type': 'CONNECTED', 'message': 'SSE 已连接', 'timestamp': datetime.now(timezone.utc).isoformat()}, ensure_ascii=False)}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    # 心跳，保持连接
                    yield ": heartbeat\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            await unregister_subscriber(queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
