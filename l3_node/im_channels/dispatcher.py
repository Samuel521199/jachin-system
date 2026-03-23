"""
IM 消息分发 — 收到消息后调用 Agent 并回传

与具体通道解耦：dispatcher 只负责「执行 + 回复」逻辑，
通道负责「接收 + 发送」。

【会话持久化】按 chat_id 加载/保存对话历史（l3_lark_sessions.json），
否则招聘多轮流程（收集信息 → 输出 JD → 同意发布）无法跨消息追溯。

长连接集成：当 HR 招聘包 (com.jachin.hr.recruitment) 存在且消息为招聘类时，
路由到 process_lark_message（含 chat_id 持久化、招聘工具链），否则走 run_agent。

【非阻塞】Agent 工作提交到线程池执行，on_message 立即返回，避免阻塞 Lark 长连接
WebSocket 线程（否则 ping 超时导致连接断开）。
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from l3_node.lark_session import load_lark_session, save_lark_session

if TYPE_CHECKING:
    from l3_node.llm_client import LiteLLMEngine

logger = logging.getLogger(__name__)

# 线程池：Agent 工作在此执行，不阻塞 Lark WebSocket 线程
_AGENT_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="im-agent")
# 按 chat_id 串行化，避免同一会话并发导致 session 损坏
_chat_locks: dict[str, threading.Lock] = {}
_lock_mutex = threading.Lock()


def _get_chat_lock(chat_id: str) -> threading.Lock:
    with _lock_mutex:
        if chat_id not in _chat_locks:
            _chat_locks[chat_id] = threading.Lock()
        return _chat_locks[chat_id]

# 招聘类消息关键词：命中则走 HR process_lark_message
_HR_RECRUITMENT_KEYWORDS = [
    "招聘", "发布", "发职位", "职位", "JD", "岗位", "简历", "打招呼", "推荐牛人",
    "同意", "确认", "确认发布", "直接发布", "收网", "抓取", "同步", "多维表",
    "post", "greet", "harvest", "bitable",
]


def _is_hr_package_available() -> bool:
    """检测 HR 招聘 MCP 包是否已加载（支持 l3_mcp_cache 的 UUID 目录名）"""
    from l3_node.hr_loader import is_hr_package_available
    return is_hr_package_available()


def _is_recruitment_message(text: str) -> bool:
    """判断是否为招聘类消息"""
    if not text or not text.strip():
        return False
    t = text.strip().lower()
    for kw in _HR_RECRUITMENT_KEYWORDS:
        if kw.lower() in t or kw in text:
            return True
    return False


def _process_via_hr_package(
    text: str,
    chat_id: str,
    user_id: str,
    run_agent_fn: Callable[..., Any],
    engine: Any,
    loop: asyncio.AbstractEventLoop,
    timeout: float,
    session_messages: list,
) -> str:
    """通过 HR 包的 process_lark_message 处理（L3 内联模式）"""
    from l3_node.hr_loader import _get_hr_recruitment_plugin_root
    cache_dir = _get_hr_recruitment_plugin_root()
    if not cache_dir:
        return "抱歉，HR 招聘 MCP 包未找到，请从 L1 订阅 com.jachin.hr.recruitment。"
    import sys
    cache_str = str(cache_dir.resolve())
    if cache_str not in sys.path:
        sys.path.insert(0, cache_str)
    try:
        from tools.atom_lark_chat import process_lark_message
        out = process_lark_message(
            text,
            chat_id=chat_id or "",
            user_id=user_id or "",
            run_agent_fn=run_agent_fn,
            engine=engine,
            loop=loop,
            timeout=timeout,
            session_messages=session_messages,
        )
        return (out.get("reply") or "").strip()
    except Exception as e:
        logger.exception("[IM Dispatcher] HR process_lark_message 失败: %s", e)
        return "抱歉，招聘处理时发生错误，请稍后重试。"
    finally:
        if cache_str in sys.path:
            sys.path.remove(cache_str)


def _do_agent_work(
    text: str,
    chat_id: str,
    user_id: str,
    run_agent_fn: Callable[..., Any],
    engine: Any,
    loop: asyncio.AbstractEventLoop,
    send_reply_fn: Callable[[str, str], bool],
    timeout: float,
) -> None:
    """
    在线程池中执行 Agent 工作，不阻塞 Lark WebSocket 线程。
    按 chat_id 加锁，避免同一会话并发导致 session 损坏。
    """
    cid = chat_id or ""
    lock = _get_chat_lock(cid) if cid else threading.Lock()
    with lock:
        session_messages = load_lark_session(cid) if cid else []
        intent = (text or "").strip()
        reply = ""
        try:
            from l3_node.lark_workflow_command_interceptor import try_lark_workflow_command_intercept

            cmd_reply = try_lark_workflow_command_intercept(intent)
        except Exception as ex:
            logger.debug("[IM Dispatcher] workflow command intercept 不可用: %s", ex)
            cmd_reply = None

        if cmd_reply:
            reply = cmd_reply
            if cid:
                session_messages.append({"role": "user", "content": intent})
                session_messages.append({"role": "assistant", "content": cmd_reply})
        else:
            try:
                if _is_hr_package_available() and _is_recruitment_message(intent):
                    logger.debug(
                        "[IM Dispatcher] 招聘类消息，走 HR process_lark_message chat_id=%s",
                        cid[:20] if cid else "",
                    )
                    reply = _process_via_hr_package(
                        intent, cid, user_id, run_agent_fn, engine, loop, timeout, session_messages
                    )
                else:
                    future = asyncio.run_coroutine_threadsafe(
                        run_agent_fn(
                            intent,
                            engine,
                            _session_messages=session_messages,
                            implicit_attribution={"channel": "lark_im_dispatcher"},
                        ),
                        loop,
                    )
                    reply = future.result(timeout=timeout)
            except TimeoutError:
                reply = "处理超时，请稍后重试。"
                logger.warning("[IM Dispatcher] Agent 超时 chat_id=%s", cid[:20] if cid else "")
            except Exception as e:
                logger.exception("[IM Dispatcher] Agent 异常: %s", e)
                reply = "抱歉，处理时发生错误，请稍后重试。"
        if cid and session_messages:
            save_lark_session(cid, session_messages)
            logger.debug("[IM Dispatcher] chat_id=%s 已保存会话 %d 条", cid[:20], len(session_messages))
        if reply and cid:
            ok = send_reply_fn(cid, str(reply).strip())
            if not ok:
                logger.warning("[IM Dispatcher] 回复发送失败 chat_id=%s", cid[:20])


def create_im_message_handler(
    run_agent_fn: Callable[..., Any],
    engine: "LiteLLMEngine",
    send_reply_fn: Callable[[str, str], bool],
    *,
    main_loop: asyncio.AbstractEventLoop | None = None,
    timeout: float = 180.0,
) -> Callable[[str, str, str], None]:
    """
    创建 IM 消息处理回调。

    收到消息后立即提交到线程池并返回，不阻塞 Lark 长连接线程，
    避免 ping 超时导致 WebSocket 断开。

    :param run_agent_fn: async 的 run_agent 函数
    :param engine: LiteLLMEngine
    :param send_reply_fn: (chat_id, text) -> bool，向 IM 发送回复
    :param main_loop: 主事件循环，用于 run_coroutine_threadsafe
    :param timeout: Agent 执行超时
    """
    loop = main_loop or asyncio.get_event_loop()

    def on_message(text: str, chat_id: str, user_id: str) -> None:
        if not (text or "").strip():
            return
        _AGENT_EXECUTOR.submit(
            _do_agent_work,
            text,
            chat_id,
            user_id,
            run_agent_fn,
            engine,
            loop,
            send_reply_fn,
            timeout,
        )

    return on_message
