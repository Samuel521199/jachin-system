"""
L3 本地 WebSocket 服务

监听 127.0.0.1:18981（189xx 系列，与 L2 18888、Sensory 18881 互不冲突），
接收前端 JSON 消息，交给 run_agent 执行，流式回传 chunk、thought、action、observation、answer。

Lark 接入时每次消息新建连接，需通过 chat_id 持久化会话，否则「同意」等回复无法获取上一轮 JD 配置。

【终端- Lark 镜像】终端为主（笔记本），Lark 为从（显示器）：
- 终端可 subscribe_mirror(lark_chat_id) 订阅某 Lark 会话的实时流
- Lark 发消息时：广播 mirror_input 到终端，再执行，回复同时给 Lark 与终端
- 终端发消息时（带 chat_id）：执行后回复给终端，并 POST 到 LARK_MIRROR_PUSH_URL 同步到 Lark
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from l3_node.llm_client import LiteLLMEngine

logger = logging.getLogger(__name__)

from l3_node.lark_session import load_lark_session as _load_lark_session, save_lark_session as _save_lark_session

# 终端-Lark 镜像：chat_id -> 订阅该会话的 WebSocket 集合（终端连接）
_mirror_subscribers: dict[str, set] = {}
_mirror_subscribers_lock = asyncio.Lock()
# LARK_MIRROR_PUSH_URL：终端发消息后，L3 将回复推送到该 URL，由 webhook 转发到 Lark
_LARK_MIRROR_PUSH_URL = os.environ.get("LARK_MIRROR_PUSH_URL", "http://127.0.0.1:5000/api/mirror-push")

WS_HOST = "127.0.0.1"
WS_PORT = 18981  # 189xx 系列，与 L2(18888)、Sensory(18881) 分离


async def _send_safe(websocket, payload: dict) -> None:
    """安全发送，忽略连接已关闭等异常。"""
    try:
        await websocket.send(json.dumps(payload, ensure_ascii=False))
    except Exception as e:
        logger.debug("WebSocket send failed: %s", e)


async def _broadcast_to_mirror_subscribers(chat_id: str, payload: dict) -> None:
    """向订阅该 chat_id 的终端连接广播消息。"""
    if not chat_id or not payload:
        return
    async with _mirror_subscribers_lock:
        subs = _mirror_subscribers.get(chat_id, set()).copy()
    for ws in subs:
        try:
            await _send_safe(ws, payload)
        except Exception:
            pass


async def _push_reply_to_lark(chat_id: str, content: str) -> None:
    """将回复推送到 Lark（终端发消息后，由 webhook 转发）。"""
    url = _LARK_MIRROR_PUSH_URL.strip()
    if not url or not chat_id or content is None:
        return
    try:
        import httpx
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(url, json={"chat_id": chat_id, "content": content})
            if r.status_code != 200:
                logger.warning("[L3 WS] mirror-push 失败 status=%d %s", r.status_code, r.text[:200])
    except Exception as e:
        logger.debug("[L3 WS] mirror-push 异常: %s", e)


def _make_on_step(websocket, run_id: str, chat_id: str, broadcast: bool):
    """构建 on_step 回调；chat_id 且 broadcast 时同时广播到镜像订阅者。"""
    def on_step(step_type: str, content: str, rid: str) -> None:
        payload = {"step_type": step_type, "content": content, "run_id": rid}
        asyncio.create_task(_send_safe(websocket, payload))
        if broadcast and chat_id:
            asyncio.create_task(_broadcast_to_mirror_subscribers(chat_id, payload))
    return on_step


async def _handle_client(websocket, engine: "LiteLLMEngine", run_agent_fn):
    """处理单客户端连接。维护 per-connection 对话历史；Lark 通过 chat_id 持久化。
    支持终端-Lark 镜像：subscribe_mirror 订阅、广播 mirror_input/answer、终端回复推送到 Lark。"""
    session_messages: list[dict] = []
    _my_lark_chat_id: str = ""  # 本连接订阅的 chat_id（终端镜像模式）
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

            # 终端订阅 Lark 镜像：后续该 chat_id 的消息会广播到此连接
            if msg_type == "subscribe_mirror":
                cid = (msg.get("lark_chat_id") or msg.get("chat_id") or "").strip()
                if cid:
                    async with _mirror_subscribers_lock:
                        _mirror_subscribers.setdefault(cid, set()).add(websocket)
                    _my_lark_chat_id = cid
                    logger.info("[L3 WS] 终端已订阅 Lark 镜像 chat_id=%s", cid[:20])
                continue
            if msg_type == "unsubscribe_mirror":
                cid = _my_lark_chat_id or (msg.get("lark_chat_id") or msg.get("chat_id") or "").strip()
                if cid:
                    async with _mirror_subscribers_lock:
                        s = _mirror_subscribers.get(cid, set())
                        s.discard(websocket)
                        if not s:
                            _mirror_subscribers.pop(cid, None)
                    _my_lark_chat_id = ""
                continue

            intent = (msg.get("intent") or msg.get("content") or "").strip()
            if not intent:
                continue

            chat_id = (msg.get("chat_id") or "").strip()
            origin_terminal = str(msg.get("origin", "")).lower() == "terminal"
            if chat_id:
                session_messages = _load_lark_session(chat_id)
                logger.debug("[L3 WS] chat_id=%s 加载历史 %d 条", chat_id[:20], len(session_messages))

            # 有 chat_id 时向镜像订阅者广播「用户输入」，终端可同步显示
            if chat_id:
                asyncio.create_task(_broadcast_to_mirror_subscribers(chat_id, {
                    "step_type": "mirror_input",
                    "content": intent,
                    "run_id": "",
                }))

            logger.debug("[L3 WS] 收到输入 intent_len=%d history=%d run_agent", len(intent), len(session_messages))
            run_id = str(uuid.uuid4())[:8]
            broadcast = bool(chat_id)
            on_step = _make_on_step(websocket, run_id, chat_id, broadcast)

            async def on_chunk(chunk: str) -> None:
                p = {"step_type": "chunk", "content": chunk, "run_id": run_id}
                await _send_safe(websocket, p)
                if broadcast and chat_id:
                    await _broadcast_to_mirror_subscribers(chat_id, p)

            try:
                reply = await run_agent_fn(
                    intent,
                    engine,
                    on_step=on_step,
                    on_chunk=on_chunk,
                    _session_messages=session_messages,
                )
                if chat_id and session_messages:
                    _save_lark_session(chat_id, session_messages)
                    logger.debug("[L3 WS] chat_id=%s 已保存会话 %d 条", chat_id[:20], len(session_messages))

                ans_payload = {"step_type": "answer", "content": reply or "", "run_id": run_id}
                await _send_safe(websocket, ans_payload)
                if broadcast and chat_id:
                    await _broadcast_to_mirror_subscribers(chat_id, ans_payload)
                # 终端发起的消息：将回复同步推送到 Lark
                if origin_terminal and chat_id and reply:
                    asyncio.create_task(_push_reply_to_lark(chat_id, reply))
            except Exception as e:
                logger.debug("[L3 WS] run_agent 异常 intent_len=%d err=%s", len(intent), type(e).__name__)
                logger.exception("run_agent failed: %s", e)
                err_payload = {"step_type": "error", "content": str(e), "run_id": run_id}
                await _send_safe(websocket, err_payload)
                if broadcast and chat_id:
                    await _broadcast_to_mirror_subscribers(chat_id, err_payload)
    except Exception as e:
        logger.warning("WebSocket client error: %s", e)
    finally:
        if _my_lark_chat_id:
            async with _mirror_subscribers_lock:
                s = _mirror_subscribers.get(_my_lark_chat_id, set())
                s.discard(websocket)
                if not s:
                    _mirror_subscribers.pop(_my_lark_chat_id, None)
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
