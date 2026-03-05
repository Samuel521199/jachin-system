"""
Sensory WebSocket Server - Layer 3 全息感官通道

供 Layer 2 daemon (Full/Light) 共享，启动 ws://localhost:18881/sensory。
Layer 3 客户端连接后可发送聊天输入、接收 thought/action/chunk/answer 广播。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import websockets

logger = logging.getLogger(__name__)

SENSORY_WS_PORT = int(os.environ.get("SENSORY_WS_PORT", "18881"))
SENSORY_WS_PATH = "/sensory"

_sensory_clients: dict = {}  # type: dict[Any, list[str]]
_sensory_server = None

_CAP_UI_RENDER = "ui_render"
_CAP_HITL_POPUP = "hitl_popup"
_CAP_STREAM_CHUNK = "stream_chunk"


async def _handle_sensory_inbound(msg: str, websocket=None) -> tuple[bool, list[str] | None]:
    """处理 Layer 3 入站消息。返回 (is_manifest, caps)。"""
    try:
        data = json.loads(msg)
        msg_type = (data.get("type") or "").lower()

        if msg_type == "manifest":
            caps = data.get("caps") or []
            return (True, [str(c) for c in caps] if isinstance(caps, list) else [])

        if msg_type in ("input", "chat"):
            intent = data.get("intent") or data.get("message") or data.get("content") or ""
            if intent:
                from core.event_bus import emit_omni_input
                emit_omni_input("layer3", str(intent).strip(), data.get("metadata") or {})
                logger.info("[Layer3] 收到聊天输入，已注入总线")
            return (False, None)

        action = (data.get("action") or "").upper()
        task_id = data.get("task_id") or ""
        worker_id = data.get("worker_id") or ""

        if task_id and action == "TASK_CLAIM" and websocket:
            from core.swarm_registry import claim_task, get_task_payload
            if claim_task(task_id, worker_id or "unknown"):
                payload = get_task_payload(task_id) or {}
                task_msg = json.dumps({
                    "step_type": "task_assigned",
                    "task_id": task_id,
                    "payload": payload,
                }, ensure_ascii=False)
                await websocket.send(task_msg)
                logger.info("[Swarm] 节点 %s 已接单 %s", worker_id or "unknown", task_id[:8])
            return (False, None)

        if task_id and action == "TASK_RESULT":
            from core.swarm_registry import resolve_task
            result_data = data.get("data", "")
            resolve_task(task_id, result_data)
            await _broadcast_task_completed(task_id)
            return (False, None)

        if task_id and action in ("HITL_APPROVE", "HITL_REJECT"):
            from core.hitl_registry import resolve
            from core.event_bus import emit_omni_input
            resolve(task_id, action == "HITL_APPROVE")
            emit_omni_input("sprite", "HITL_RESPONSE", {"action": action, "task_id": task_id})
            return (False, None)
    except json.JSONDecodeError:
        pass
    except Exception as e:
        logger.warning("[Sensory] 入站处理异常: %s", e)
    return (False, None)


async def _sensory_ws_handler(websocket, port: int = SENSORY_WS_PORT) -> None:
    """WebSocket 连接处理。"""
    manifest_received = False
    try:
        logger.info("[Layer3] 客户端已连接 ws://localhost:%d%s", port, SENSORY_WS_PATH)
        while True:
            try:
                msg = await asyncio.wait_for(websocket.recv(), timeout=60.0)
                is_manifest, caps = await _handle_sensory_inbound(msg, websocket)
                if is_manifest and caps is not None:
                    _sensory_clients[websocket] = caps
                    manifest_received = True
                elif not manifest_received:
                    _sensory_clients[websocket] = [_CAP_UI_RENDER, _CAP_HITL_POPUP]
                    manifest_received = True
            except asyncio.TimeoutError:
                continue
            except websockets.exceptions.ConnectionClosed:
                break
    except websockets.exceptions.ConnectionClosed:
        pass
    except Exception as e:
        logger.warning("[Layer3] WebSocket 连接异常: %s", e)
    finally:
        _sensory_clients.pop(websocket, None)
        logger.info("[Layer3] 客户端已断开")


async def _broadcast_task_completed(task_id: str) -> None:
    """广播 task_completed 至 ui_render 客户端。"""
    out = {"step_type": "task_completed", "content": "", "task_id": task_id}
    payload = json.dumps(out, ensure_ascii=False)
    dead = []
    for ws, caps in list(_sensory_clients.items()):
        if _CAP_UI_RENDER not in caps:
            continue
        try:
            await ws.send(payload)
        except (websockets.exceptions.ConnectionClosed, Exception):
            dead.append(ws)
    for ws in dead:
        _sensory_clients.pop(ws, None)


def _has_worker_cap(caps: list[str]) -> bool:
    return any(str(c).startswith("worker_") for c in caps)


def _should_send_to_client(step_type: str, caps: list[str]) -> bool:
    if _CAP_UI_RENDER in caps and step_type in ("thought", "action", "observation", "answer", "task_completed"):
        return True
    if _CAP_HITL_POPUP in caps and step_type == "HITL_REQUIRED":
        return True
    if _CAP_STREAM_CHUNK in caps and step_type == "chunk":
        return True
    if step_type == "task_offer" and _has_worker_cap(caps):
        return True
    return False


async def _broadcast_to_ui(ev: Any) -> None:
    """订阅 layer3_broadcast 的输出，按 caps 过滤广播。"""
    meta = getattr(ev, "payload", None) or {}
    step_type = meta.get("step_type") or "unknown"
    content = getattr(ev, "result", "") or getattr(ev, "content", "")
    content_out = content if step_type == "chunk" else content[:500]
    out = {
        "step_type": step_type,
        "content": content_out,
        "source": getattr(ev, "source", "layer3_broadcast"),
        "task_id": meta.get("task_id"),
    }
    if step_type == "chunk":
        out["run_id"] = meta.get("run_id", "")
    if step_type == "task_offer":
        out["tool"] = meta.get("tool", "")
        out["payload"] = meta.get("payload", {})
    payload = json.dumps(out, ensure_ascii=False)
    dead = []
    for ws, caps in list(_sensory_clients.items()):
        if not _should_send_to_client(step_type, caps):
            continue
        try:
            await ws.send(payload)
        except (websockets.exceptions.ConnectionClosed, Exception):
            dead.append(ws)
    for ws in dead:
        _sensory_clients.pop(ws, None)


_original_exception_handler = None
_ws_log_filter_added = False


class _HandshakeFailureFilter(logging.Filter):
    """过滤 websockets 的握手失败日志，避免刷屏（客户端断开/非 WS 请求）。"""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            msg = str(record.msg)
        if "opening handshake failed" in msg.lower():
            return False
        return True


def _sensory_exception_handler(loop, context):
    """asyncio 异常处理器：将 InvalidMessage/握手失败降级为 debug，避免刷屏。"""
    exc = context.get("exception")
    if exc is not None:
        err_msg = str(exc).lower()
        if "invalid" in err_msg or "valid http request" in err_msg or "eof" in err_msg or "connection closed" in err_msg:
            logger.debug("[Sensory] 连接异常（客户端可能已断开或非 WebSocket 请求）: %s", exc)
            return
    if _original_exception_handler is not None:
        _original_exception_handler(loop, context)
    else:
        loop.default_exception_handler(context)


async def start_sensory_server(port: int = SENSORY_WS_PORT) -> None:
    """启动 Sensory WebSocket Server。"""
    global _sensory_server, _original_exception_handler, _ws_log_filter_added
    try:
        loop = asyncio.get_running_loop()
        _original_exception_handler = loop.get_exception_handler()
        loop.set_exception_handler(_sensory_exception_handler)
    except RuntimeError:
        pass
    if not _ws_log_filter_added:
        ws_logger = logging.getLogger("websockets.server")
        ws_logger.addFilter(_HandshakeFailureFilter())
        _ws_log_filter_added = True
    try:
        handler = lambda ws: _sensory_ws_handler(ws, port=port)
        _sensory_server = await websockets.serve(
            handler,
            "localhost",
            port,
            ping_interval=20,
            ping_timeout=10,
            close_timeout=5,
        )
        logger.info("[Sensory] 全息共振通道已启动 ws://localhost:%d%s", port, SENSORY_WS_PATH)
    except OSError as e:
        logger.warning("[Sensory] 端口 %d 占用，跳过: %s", port, e)
