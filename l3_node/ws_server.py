"""
L3 本地 WebSocket 服务

监听 127.0.0.1:18981（189xx 系列，与 L2 18888、Sensory 18881 互不冲突），
接收前端 JSON 消息，交给 run_agent 执行，流式回传 chunk、thought、action、observation、answer。
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
WS_PORT = 18981  # 189xx 系列，与 L2(18888)、Sensory(18881) 分离


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
    """处理单客户端连接。维护 per-connection 对话历史，供多轮「确认」等上下文理解。"""
    session_messages: list[dict] = []
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

            logger.debug("[L3 WS] 收到输入 intent_len=%d history=%d run_agent", len(intent), len(session_messages))
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
                    _session_messages=session_messages,
                )
                await _send_safe(websocket, {
                    "step_type": "answer",
                    "content": reply or "",
                    "run_id": run_id,
                })
            except Exception as e:
                logger.debug("[L3 WS] run_agent 异常 intent_len=%d err=%s", len(intent), type(e).__name__)
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


def _is_port_in_use_error(e: BaseException) -> bool:
    """判断是否为端口占用错误（Windows 10048, Linux 98）"""
    if isinstance(e, OSError):
        # Windows: 10048 = WSAEADDRINUSE; Linux: 98 = EADDRINUSE
        return getattr(e, "errno", None) in (10048, 98)
    return False


async def run_ws_server(
    engine: "LiteLLMEngine",
    run_agent_fn,
    host: str = WS_HOST,
    port: int = WS_PORT,
) -> None:
    """启动 WebSocket 服务，异步非阻塞。端口被占用时自动尝试 18982、18983..."""
    try:
        import websockets
    except ImportError:
        raise RuntimeError("需要安装 websockets: pip install websockets")

    async def handler(websocket):
        await _handle_client(websocket, engine, run_agent_fn)

    # 189xx 系列，跳过 18888（L2）、18991（L3 HTTP）
    skip_ports = {18888, 18991}
    ports_to_try = [p for p in range(port, port + 15) if p not in skip_ports][:12]
    last_err: BaseException | None = None
    for i, try_port in enumerate(ports_to_try):
        try:
            server = await websockets.serve(
                handler,
                host,
                try_port,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5,
            )
            if i > 0:
                logger.warning(
                    "端口 %d 被占用，已改用 %d。前端需 VITE_SENSORY_WS_PORT=%d 或关闭占用进程",
                    port, try_port, try_port,
                )
            logger.info("L3 WebSocket 服务已启动 ws://%s:%d/sensory", host, try_port)
            await server.wait_closed()
            return
        except OSError as e:
            last_err = e
            if _is_port_in_use_error(e):
                logger.warning("端口 %d 已被占用 (errno=%s)，尝试下一端口...", try_port, getattr(e, "errno", "?"))
                continue
            raise

    # 所有端口均失败
    raise RuntimeError(
        f"端口 {ports_to_try[0]}~{ports_to_try[-1]} 均被占用。请关闭其他 L3 实例: netstat -ano | findstr 18981"
    ) from last_err
