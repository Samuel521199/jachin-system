"""
Local Ingress API - 边缘中枢本地网关

极轻量级 HTTP 服务，跑在 localhost:9000。
摄像头、GUI、硬件按钮等异构设备只需 POST JSON 到 /api/events，
即可唤醒内部 Event Bus 与 Workflow。

用法:
    POST http://localhost:9000/api/events
    Content-Type: application/json
    {"type": "audio.input", "payload": {"text": "用户说的话"}, "source_plugin_id": "external-camera"}
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# 延迟导入，避免循环依赖
_event_bus = None


def _get_event_bus():
    global _event_bus
    if _event_bus is None:
        from core.event_bus import emit_async
        _event_bus = emit_async
    return _event_bus


async def _handle_events(request: "aiohttp.Request") -> "aiohttp.Response":
    """处理 POST /api/events"""
    try:
        body = await request.json()
    except json.JSONDecodeError as e:
        return _json_response({"error": f"Invalid JSON: {e}"}, status=400)

    event_type = body.get("type")
    if not event_type:
        return _json_response({"error": "Missing 'type' field"}, status=400)

    payload = body.get("payload")
    if payload is None:
        payload = {}
    elif not isinstance(payload, dict):
        return _json_response({"error": "'payload' must be an object"}, status=400)

    source_plugin_id = body.get("source_plugin_id")

    emit = _get_event_bus()
    await emit(event_type, payload, source_plugin_id)

    logger.info(
        "Ingress: event %s from %s -> Event Bus",
        event_type,
        source_plugin_id or "anonymous",
    )
    return _json_response({"success": True, "type": event_type})


async def _handle_health(request: "aiohttp.Request") -> "aiohttp.Response":
    """GET /health"""
    return _json_response({"status": "ok", "service": "nexus-ingress"})


def _json_response(data: dict[str, Any], status: int = 200) -> "aiohttp.Response":
    from aiohttp import web
    return web.json_response(data, status=status)


async def start_ingress_server(host: str = "127.0.0.1", port: int = 9000) -> "aiohttp.web.AppRunner":
    """
    启动 Local Ingress HTTP 服务

    Returns:
        AppRunner（用于 shutdown 时 cleanup）
    """
    from aiohttp import web

    app = web.Application()
    app.router.add_post("/api/events", _handle_events)
    app.router.add_get("/health", _handle_health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()

    logger.info("[Ingress] 已就绪: http://%s:%d/api/events", host, port)
    return runner
