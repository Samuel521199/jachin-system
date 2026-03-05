"""
L3 本地 WebSocket 服务

监听 127.0.0.1:18881，接收前端 JSON 消息，交给 run_agent 执行，
流式回传 chunk、thought、action、observation、answer。
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from l3_node.llm_client import LiteLLMEngine

logger = logging.getLogger(__name__)

WS_HOST = "127.0.0.1"
WS_PORT = 18881


async def _send_safe(websocket, payload: dict) -> None:
    """安全发送，忽略连接已关闭等异常。"""
    try:
        await websocket.send(json.dumps(payload, ensure_ascii=False))
    except Exception as e:
        logger.debug("WebSocket send failed: %s", e)


def _make_on_step(websocket, run_id: str):
    """构建 sync on_step 回调，内部用 create_task 异步发送。"""
    def on_step(step_type: str, content: str, rid: str) -> None:
        payload = {"step_type": step_type, "content": content, "run_id": rid}
        asyncio.create_task(_send_safe(websocket, payload))
    return on_step


async def _handle_client(websocket, engine: "LiteLLMEngine", run_agent_fn):
    """处理单客户端连接。"""
    try:
        async for raw in websocket:
            try:
                msg = json.loads(raw) if isinstance(raw, str) else raw
            except (json.JSONDecodeError, TypeError):
                continue
            msg_type = msg.get("type") or msg.get("action", "")
            if msg_type == "manifest":
                await _send_safe(websocket, {"type": "manifest_ack", "caps": msg.get("caps", [])})
                continue
            intent = (msg.get("intent") or msg.get("content") or "").strip()
            if not intent:
                continue

            run_id = str(uuid.uuid4())[:8]
            on_step = _make_on_step(websocket, run_id)

            async def on_chunk(chunk: str) -> None:
                payload = {"step_type": "chunk", "content": chunk, "run_id": run_id}
                await _send_safe(websocket, payload)

            try:
                reply = await run_agent_fn(
                    intent,
                    engine,
                    on_step=on_step,
                    on_chunk=on_chunk,
                )
                await _send_safe(websocket, {
                    "step_type": "answer",
                    "content": reply or "",
                    "run_id": run_id,
                })
            except Exception as e:
                logger.exception("run_agent failed: %s", e)
                await _send_safe(websocket, {
                    "step_type": "error",
                    "content": str(e),
                    "run_id": run_id,
                })
    except Exception as e:
        logger.warning("WebSocket client error: %s", e)
    finally:
        await websocket.close()


async def run_ws_server(
    engine: "LiteLLMEngine",
    run_agent_fn,
    host: str = WS_HOST,
    port: int = WS_PORT,
) -> None:
    """启动 WebSocket 服务，异步非阻塞。"""
    try:
        import websockets
    except ImportError:
        raise RuntimeError("需要安装 websockets: pip install websockets")

    async def handler(websocket):
        await _handle_client(websocket, engine, run_agent_fn)

    server = await websockets.serve(
        handler,
        host,
        port,
        ping_interval=20,
        ping_timeout=10,
        close_timeout=5,
    )
    logger.info("L3 WebSocket 服务已启动 ws://%s:%d/sensory", host, port)
    await server.wait_closed()
