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
import sys
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
# LARK_MIRROR_PUSH_URL：终端发消息后，可 POST 到独立 lark_bot webhook；未配置或默认 localhost:5000 时，
# 若已配置 Lark 凭证则优先走 Open API 直连（与长连接模式一致，避免 5000 被占用或非 webhook 返回 503）。
_DEFAULT_MIRROR_PUSH = "http://127.0.0.1:5000/api/mirror-push"

WS_HOST = "127.0.0.1"
WS_PORT = 18981  # 189xx 系列，与 L2(18888)、Sensory(18881) 分离


async def _send_safe(websocket, payload: dict) -> None:
    """安全发送，忽略连接已关闭等异常。"""
    try:
        await websocket.send(json.dumps(payload, ensure_ascii=False))
    except Exception as e:
        logger.debug("WebSocket send failed: %s", e)


async def _maybe_push_memory_compact_suggest(websocket) -> None:
    """连接建立后：若到达整理周期，推送倒计时提示（由前端决定是否自动开始）。"""
    try:
        from l3_node.memory_compact_schedule import (
            build_ws_prompt_payload,
            record_prompt_sent,
            should_send_prompt_now,
        )

        if not should_send_prompt_now():
            return
        await _send_safe(websocket, build_ws_prompt_payload())
        record_prompt_sent()
    except Exception as e:
        logger.debug("[L3 WS] memory_compact_suggest 跳过: %s", e)


async def _run_scheduled_memory_compact_background() -> None:
    try:
        from l3_node.memory_compact_control import reset_memory_compact_cancel
        from l3_node.memory_compactor import compact_local_memory_if_needed
        from l3_node.local_memory import main_local_memory_json_path

        reset_memory_compact_cancel()
        report = await compact_local_memory_if_needed(str(main_local_memory_json_path()))
        if (report or "").strip():
            logger.info("[MemoryCompact] %s", report.strip())
    except Exception as e:
        logger.debug("[MemoryCompact] 后台整理失败: %s", e)


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


def _mirror_push_url_effective() -> str:
    return (os.environ.get("LARK_MIRROR_PUSH_URL") or _DEFAULT_MIRROR_PUSH).strip()


def _is_default_mirror_push_url(url: str) -> bool:
    u = (url or "").strip().rstrip("/").lower()
    return u in (
        "http://127.0.0.1:5000/api/mirror-push",
        "http://localhost:5000/api/mirror-push",
    )


def _push_via_lark_open_api_sync(chat_id: str, content: str) -> bool:
    """使用与 IM 长连接相同的凭证，经 Open API 发送文本（不依赖 :5000 webhook）。"""
    cid = (chat_id or "").strip()
    if not cid or content is None:
        return False
    try:
        from l3_node.channels.lark.client import get_lark_api_base, resolve_lark_credentials
        from l3_node.channels.lark.im import send_text

        aid, sec, yb = resolve_lark_credentials()
        if not aid or not sec:
            return False
        base = yb or get_lark_api_base()
        res = send_text(cid, str(content), app_id=aid, app_secret=sec, api_base=base)
        return res.get("status") == "success"
    except Exception as e:
        logger.debug("[L3 WS] mirror 直连 Lark Open API 失败: %s", e)
        return False


async def _push_reply_to_lark(chat_id: str, content: str) -> None:
    """将回复同步到 Lark：默认 URL 或凭证可用时优先 Open API；否则或失败时再 POST mirror-push。"""
    url = _mirror_push_url_effective()
    if not chat_id or content is None:
        return

    prefer_direct_first = _is_default_mirror_push_url(url) or not url
    if prefer_direct_first:
        if await asyncio.to_thread(_push_via_lark_open_api_sync, chat_id, content):
            return
        if not url:
            logger.debug("[L3 WS] mirror-push 未配置 URL 且 Lark API 未发送成功，跳过")
            return

    try:
        import httpx

        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                r = await client.post(url, json={"chat_id": chat_id, "content": content})
            except httpx.RequestError as e:
                logger.debug("[L3 WS] mirror-push 请求异常: %s", e)
                if await asyncio.to_thread(_push_via_lark_open_api_sync, chat_id, content):
                    logger.info("[L3 WS] mirror-push 不可用，已改用 Lark Open API 发送成功")
                else:
                    logger.warning("[L3 WS] mirror-push 失败（网络）且 Lark API 未成功: %s", e)
                return

            if r.status_code == 200:
                return
            if r.status_code in (502, 503, 504):
                if await asyncio.to_thread(_push_via_lark_open_api_sync, chat_id, content):
                    logger.info(
                        "[L3 WS] mirror-push HTTP %d，已改用 Lark Open API 发送成功",
                        r.status_code,
                    )
                    return
            logger.warning("[L3 WS] mirror-push 失败 status=%d %s", r.status_code, r.text[:200])
    except Exception as e:
        logger.debug("[L3 WS] mirror-push 异常: %s", e)
        if await asyncio.to_thread(_push_via_lark_open_api_sync, chat_id, content):
            logger.info("[L3 WS] mirror-push 异常后已改用 Lark Open API 发送成功")


def _make_on_step(websocket, run_id: str, chat_id: str, broadcast: bool):
    """构建 on_step 回调；chat_id 且 broadcast 时同时广播到镜像订阅者。

    注意：须与 on_chunk、以及 run_agent 返回后的 answer 使用**同一会话级** ``run_id``。
    Agent 传入的第三参为 ``ctx.run_id``（完整 UUID），若写入 payload 会与 chunk 的短 id 不一致，
    桌面端 ``l3ActiveRunIdRef`` 在「末帧为 chunk（短 id）→ answer（长 id）」时会丢弃 answer，
    表现为一直转圈直至超时或中断。此处始终使用本 WS 消息轮次的 ``run_id``。
    """
    def on_step(step_type: str, content: str, _ctx_run_id: str) -> None:
        payload = {"step_type": step_type, "content": content, "run_id": run_id}
        asyncio.create_task(_send_safe(websocket, payload))
        if broadcast and chat_id:
            asyncio.create_task(_broadcast_to_mirror_subscribers(chat_id, payload))
    return on_step


async def _handle_client(websocket, engine: "LiteLLMEngine", run_agent_fn):
    """处理单客户端连接。维护 per-connection 对话历史；Lark 通过 chat_id 持久化。
    支持终端-Lark 镜像：subscribe_mirror 订阅、广播 mirror_input/answer、终端回复推送到 Lark。"""
    session_messages: list[dict] = []
    _my_lark_chat_id: str = ""  # 本连接订阅的 chat_id（终端镜像模式）
    _bg_task_subscribed: bool = False
    try:
        async for raw in websocket:
            try:
                msg = json.loads(raw) if isinstance(raw, str) else raw
            except (json.JSONDecodeError, TypeError):
                continue
            msg_type = msg.get("type") or msg.get("action", "")
            if msg_type == "manifest":
                await _send_safe(websocket, {"type": "manifest_ack", "caps": msg.get("caps", [])})
                asyncio.create_task(_maybe_push_memory_compact_suggest(websocket))
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

            if msg_type == "subscribe_background_tasks":
                try:
                    from l3_node.l3_event_bus import register_background_task_subscriber

                    await register_background_task_subscriber(websocket)
                    _bg_task_subscribed = True
                    await _send_safe(
                        websocket,
                        {"type": "background_task_subscribed", "ok": True},
                    )
                except Exception as e:
                    await _send_safe(
                        websocket,
                        {"type": "background_task_subscribed", "ok": False, "error": str(e)},
                    )
                continue

            # 前端 /clear：控制帧清空 per-connection 缓冲与（可选）Lark 持久化会话，不进入 intent/LLM
            if msg_type == "clear_session":
                cid = (msg.get("chat_id") or _my_lark_chat_id or "").strip()
                session_messages.clear()
                if cid:
                    _save_lark_session(cid, session_messages)
                logger.debug("[L3 WS] clear_session 已清空 chat_id=%s", cid[:20] if cid else "-")
                continue

            # 记忆整理调度：控制帧（无 intent 亦可）
            if msg_type == "memory_compact_defer":
                try:
                    from l3_node.memory_compact_schedule import defer_hours

                    defer_hours(float(msg.get("hours", 24)))
                except Exception as e:
                    logger.debug("[L3 WS] memory_compact_defer: %s", e)
                await _send_safe(
                    websocket,
                    {
                        "step_type": "system_status",
                        "content": json.dumps({"status": "记忆整理已推迟"}, ensure_ascii=False),
                        "run_id": "",
                    },
                )
                continue
            if msg_type in ("memory_compact_confirm", "memory_compact_auto_start"):
                asyncio.create_task(_run_scheduled_memory_compact_background())
                await _send_safe(
                    websocket,
                    {
                        "step_type": "system_status",
                        "content": json.dumps({"status": "记忆整理已在后台启动"}, ensure_ascii=False),
                        "run_id": "",
                    },
                )
                continue
            if msg_type == "memory_compact_cancel":
                try:
                    from l3_node.memory_compact_control import request_memory_compact_cancel

                    request_memory_compact_cancel()
                except Exception as e:
                    logger.debug("[L3 WS] memory_compact_cancel: %s", e)
                await _send_safe(
                    websocket,
                    {
                        "step_type": "system_status",
                        "content": json.dumps({"status": "已请求取消记忆整理（写入前生效）"}, ensure_ascii=False),
                        "run_id": "",
                    },
                )
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

            # 与前端 /clear 对齐：不进 run_agent、不发 thought kick，就地清空本会话缓冲
            if intent == "/clear":
                session_messages.clear()
                if chat_id:
                    _save_lark_session(chat_id, session_messages)
                reply_clear = "[System] 后端上下文已强制清空。"
                ans_payload = {"step_type": "answer", "content": reply_clear, "run_id": run_id}
                await _send_safe(websocket, ans_payload)
                if broadcast and chat_id:
                    await _broadcast_to_mirror_subscribers(chat_id, ans_payload)
                if origin_terminal and chat_id:
                    asyncio.create_task(_push_reply_to_lark(chat_id, reply_clear))
                continue

            try:
                from l3_node.lark_workflow_command_interceptor import try_lark_workflow_command_intercept

                cmd_reply = try_lark_workflow_command_intercept(intent, channel_id=chat_id or "")
            except Exception:
                cmd_reply = None
            if cmd_reply:
                if chat_id:
                    session_messages.append({"role": "user", "content": intent})
                    session_messages.append({"role": "assistant", "content": cmd_reply})
                    _save_lark_session(chat_id, session_messages)
                    logger.debug("[L3 WS] chat_id=%s 遥控拦截已保存会话", chat_id[:20])
                ans_payload = {"step_type": "answer", "content": cmd_reply, "run_id": run_id}
                await _send_safe(websocket, ans_payload)
                if broadcast and chat_id:
                    await _broadcast_to_mirror_subscribers(chat_id, ans_payload)
                if origin_terminal and chat_id:
                    asyncio.create_task(_push_reply_to_lark(chat_id, cmd_reply))
                continue

            on_step = _make_on_step(websocket, run_id, chat_id, broadcast)

            # 首包：在 compaction / 首 token 之前可能静默数分钟，避免桌面端 300s 空气泡误判 + 超时后仍靠 run_id 收 answer。
            # OOD 硬拦路径与 Lark（仅最终 reply）对齐：不发送「长任务」占位 thought，避免聊天框多段思考 + 安全拒答。
            try:
                from l3_node.intent_gateway.ood_signals import should_skip_progress_thought_kick

                _skip_kick = should_skip_progress_thought_kick(raw_user_input=intent)
            except Exception:
                _skip_kick = False
            if not _skip_kick:
                _kick = {
                    "step_type": "thought",
                    "content": "已接入任务。若上下文较长会先执行记忆刷新与摘要压缩，随后再推理（可能需数分钟）。",
                    "run_id": run_id,
                }
                await _send_safe(websocket, _kick)
                if broadcast and chat_id:
                    await _broadcast_to_mirror_subscribers(chat_id, _kick)

            async def on_chunk(chunk: str) -> None:
                p = {"step_type": "chunk", "content": chunk, "run_id": run_id}
                await _send_safe(websocket, p)
                if broadcast and chat_id:
                    await _broadcast_to_mirror_subscribers(chat_id, p)

            _imp_sig = msg.get("implicit_signals")
            _imp_sig = _imp_sig if isinstance(_imp_sig, dict) else None
            _imp_attr = {
                "channel": "websocket_terminal" if origin_terminal else "websocket_lark",
                "has_chat_id": bool(chat_id),
            }
            if chat_id:
                _imp_attr["lark_chat_id"] = str(chat_id).strip()
            _att_meta = msg.get("attachments_metadata")
            _att_meta = _att_meta if isinstance(_att_meta, list) else None
            _gw_st = msg.get("gateway_system_state")
            _gw_st = str(_gw_st).strip() if _gw_st else None
            _gw_ch = str(msg.get("gateway_clarification_handle") or "").strip()
            try:
                _gw_dl = float(msg.get("gateway_clarification_deadline_ts") or 0.0)
            except (TypeError, ValueError):
                _gw_dl = 0.0
            try:
                reply = await run_agent_fn(
                    intent,
                    engine,
                    on_step=on_step,
                    on_chunk=on_chunk,
                    _session_messages=session_messages,
                    implicit_signals=_imp_sig,
                    implicit_attribution=_imp_attr,
                    attachments_metadata=_att_meta,
                    gateway_system_state=_gw_st,
                    gateway_clarification_handle=_gw_ch,
                    gateway_clarification_deadline_ts=_gw_dl,
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
        if _bg_task_subscribed:
            try:
                from l3_node.l3_event_bus import unregister_background_task_subscriber

                await unregister_background_task_subscriber(websocket)
            except Exception:
                pass
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
            _sig_tokens: list = []

            def _on_stop_signal() -> None:
                async def _close_srv() -> None:
                    logger.info("[WS] 收到停止信号，关闭 WebSocket 服务…")
                    await server.close()

                try:
                    asyncio.get_running_loop().create_task(_close_srv())
                except RuntimeError:
                    pass

            if sys.platform != "win32":
                import signal

                loop = asyncio.get_running_loop()
                for sig in (signal.SIGINT, signal.SIGTERM):
                    try:
                        loop.add_signal_handler(sig, _on_stop_signal)
                        _sig_tokens.append(sig)
                    except (NotImplementedError, RuntimeError, ValueError):
                        pass
            try:
                await server.wait_closed()
            finally:
                if _sig_tokens:
                    import signal

                    loop = asyncio.get_running_loop()
                    for sig in _sig_tokens:
                        try:
                            loop.remove_signal_handler(sig)
                        except Exception:
                            pass
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
