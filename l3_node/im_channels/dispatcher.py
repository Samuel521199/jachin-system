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
import hashlib
import logging
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from l3_node.im_channels.lark_interaction_hourly_log import append_lark_interaction_record
from l3_node.lark_session import load_lark_session, save_lark_session

if TYPE_CHECKING:
    from l3_node.llm_client import LiteLLMEngine

logger = logging.getLogger(__name__)


# ─── 安抚消息：方案B（qwen-turbo 意图摘要）+ 方案A 兜底 ─────────────────────────

def _quick_task_summary(user_input: str, timeout_sec: float = 2.0) -> str:
    """
    用 qwen-turbo 在 timeout_sec 秒内生成 ≤15 字的任务意图描述。
    超时或失败返回空字符串，调用方降级到截取原文。
    """
    import os
    try:
        from core.brain.llm.dashscope_regional import get_dashscope_regional_credentials
        api_key, api_base = get_dashscope_regional_credentials()
    except Exception:
        api_key = (
            os.environ.get("DASHSCOPE_API_KEY_SEA")
            or os.environ.get("DASHSCOPE_API_KEY")
            or ""
        )
        region = os.environ.get("JACHIN_ACTIVE_REGION", "CN").upper()
        api_base = (
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
            if region == "SEA"
            else "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
    if not api_key:
        return ""

    prompt = (
        f"用15字以内描述以下消息的核心任务，只输出描述本身，不要解释或加标点：\n{user_input[:200]}"
    )
    try:
        import litellm
        resp = litellm.completion(
            model="dashscope/qwen-turbo",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=20,
            temperature=0.0,
            timeout=timeout_sec,
            api_key=api_key,
            api_base=api_base,
        )
        return (resp.choices[0].message.content or "").strip()[:30]
    except Exception as e:
        logger.debug("[IM Dispatcher] 意图摘要 qwen-turbo 失败（降级截字）: %s", e)
        return ""


def _build_ack_message(user_input: str, summary: str) -> str:
    """用意图摘要或截取原文构造安抚消息。"""
    if summary:
        return f"🤖 正在{summary}，请稍候…（复杂任务约需 1-2 分钟）"
    snip = user_input.strip()
    if len(snip) > 50:
        snip = snip[:50] + "…"
    return f"🤖 正在处理：「{snip}」，请稍候…（复杂任务约需 1-2 分钟）"


# ─────────────────────────────────────────────────────────────────────────────

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


_IM_INBOUND_RECENT: dict[str, float] = {}
_IM_INBOUND_TTL_SEC = 30.0
_IM_INBOUND_GUARD = threading.Lock()


def _should_skip_duplicate_inbound(chat_id: str, text: str) -> bool:
    """飞书长连接偶发同一条文本短时投递两次：同会话 + 归一化文案在 TTL 内只处理一次。"""
    cid = (chat_id or "").strip()
    if not cid:
        return False
    norm = " ".join((text or "").strip().split())
    if not norm:
        return False
    digest = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:20]
    key = f"{cid}\0{digest}"
    now = time.monotonic()
    with _IM_INBOUND_GUARD:
        stale = [k for k, t in _IM_INBOUND_RECENT.items() if now - t > _IM_INBOUND_TTL_SEC]
        for k in stale:
            del _IM_INBOUND_RECENT[k]
        prev = _IM_INBOUND_RECENT.get(key)
        if prev is not None and (now - prev) < _IM_INBOUND_TTL_SEC:
            return True
        _IM_INBOUND_RECENT[key] = now
    return False


# 招聘类消息关键词：命中则走 HR process_lark_message。
# 禁止含「同步」「多维表」「bitable」等 PMO/飞书表常见词单独触达，否则通用群对话大量误进 HR。
_HR_RECRUITMENT_KEYWORDS = [
    "招聘", "发布", "发职位", "职位", "JD", "岗位", "简历", "打招呼", "推荐牛人",
    "同意", "确认发布", "直接发布", "收网", "抓取简历", "抓简历",
    "清除岗位", "清除全部", "清空岗位", "删除岗位",
    "post", "greet", "harvest",
]


def _is_hr_package_available() -> bool:
    """检测 HR 招聘 MCP 包是否已加载（支持 l3_mcp_cache 的 UUID 目录名）"""
    from l3_node.hr_loader import is_hr_package_available
    return is_hr_package_available()


def _line_parses_as_boss_job_select(text: str) -> bool:
    """
    「python工程师 杭州 15-25K」等一行选岗文案：应走 process_lark_message 预写 jd.json，
    避免仅关键词未命中时直进 Agent 把历史里的「抓取…」错当 job_name。
    """
    s = (text or "").strip()
    if len(s) < 6:
        return False
    try:
        from l3_node.hr_loader import _get_hr_recruitment_plugin_root

        root = _get_hr_recruitment_plugin_root()
        if not root or not root.exists():
            return False
        cache_str = str(root.resolve())
        prev = sys.path.copy()
        try:
            if cache_str not in sys.path:
                sys.path.insert(0, cache_str)
            from tools.boss_utils import extract_job_select_line_for_boss_from_hr_chat

            return bool(extract_job_select_line_for_boss_from_hr_chat(s))
        finally:
            sys.path = prev
    except Exception:
        return False


def _apply_hr_im_job_select_prelude(text: str) -> None:
    """
    在 ``try_lark_workflow_command_intercept`` 之前合并飞书里的 Boss 选岗行。

    否则整句「Python 工程师 _ 杭州 15-25K，打招呼改成20人」会先被拦截器命中，
    **永远不会**执行 ``process_lark_message`` 里的 ``apply_job_select_from_hr_im_text``，
    指针仍留在旧岗，批次参数误作用到产品经理等。
    """
    s = (text or "").strip()
    if not s:
        return
    if not _is_hr_package_available() or not _is_recruitment_message(s):
        return
    try:
        from l3_node.hr_loader import _get_hr_recruitment_plugin_root

        root = _get_hr_recruitment_plugin_root()
        if not root or not root.exists():
            return
        cache_str = str(root.resolve())
        prev = sys.path.copy()
        try:
            if cache_str not in sys.path:
                sys.path.insert(0, cache_str)
            from tools.atom_lark_chat import apply_job_select_from_hr_im_text

            r = apply_job_select_from_hr_im_text(s)
            if r.get("applied"):
                logger.info(
                    "[IM Dispatcher] 拦截前已合并选岗 jd_select=%r job_folder=%r job_name=%r",
                    (r.get("jd_select") or "")[:100],
                    r.get("job_folder") or "",
                    (r.get("job_name") or "")[:60],
                )
        finally:
            sys.path = prev
    except Exception as e:
        logger.debug("[IM Dispatcher] 选岗 prelude 跳过: %s", e)


def _is_recruitment_message(text: str) -> bool:
    """判断是否为招聘类消息"""
    if not text or not text.strip():
        return False
    try:
        from l3_node.routing.intent_signals import user_message_suggests_pmo_or_bi_context

        if user_message_suggests_pmo_or_bi_context(text):
            return False
    except Exception:
        pass
    if _line_parses_as_boss_job_select(text):
        return True
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
        if _should_skip_duplicate_inbound(cid, intent):
            logger.info(
                "[IM Dispatcher] 忽略短时重复投递（同会话同文案）chat_id=%s preview=%s",
                cid[:24] if cid else "",
                intent[:48],
            )
            append_lark_interaction_record(
                "duplicate_inbound_suppressed",
                chat_id=cid,
                user_id=user_id or "",
                user_text=intent,
                route="im_dispatcher",
                status="skipped_duplicate_within_ttl",
            )
            return
        reply = ""
        route = "unknown"
        turn_status = "pending"
        err_msg = ""
        err_tb = ""
        send_ok: bool | None = None
        _apply_hr_im_job_select_prelude(intent)
        try:
            from l3_node.lark_workflow_command_interceptor import try_lark_workflow_command_intercept

            cmd_reply = try_lark_workflow_command_intercept(intent, channel_id=cid)
        except Exception as ex:
            logger.debug("[IM Dispatcher] workflow command intercept 不可用: %s", ex)
            cmd_reply = None

        if cmd_reply:
            route = "lark_workflow_command"
            reply = cmd_reply
            turn_status = "ok"
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
                    route = "hr_process_lark_message"
                    reply = _process_via_hr_package(
                        intent, cid, user_id, run_agent_fn, engine, loop, timeout, session_messages
                    )
                    turn_status = "ok"
                else:
                    route = "run_agent"
                    _iatt = {"channel": "lark_im_dispatcher"}
                    if cid:
                        _iatt["lark_chat_id"] = str(cid).strip()
                    if user_id:
                        _iatt["lark_user_id"] = str(user_id).strip()
                    future = asyncio.run_coroutine_threadsafe(
                        run_agent_fn(
                            intent,
                            engine,
                            _session_messages=session_messages,
                            implicit_attribution=_iatt,
                        ),
                        loop,
                    )
                    # 延时安抚：12 秒后若 Agent 仍未返回，自动发送一条智能安抚消息
                    # 快速任务（<12s）用户直接收到答案，不会被多余消息打扰
                    _ack_cancelled = threading.Event()

                    def _delayed_ack() -> None:
                        if _ack_cancelled.is_set() or not cid:
                            return
                        try:
                            _summary = _quick_task_summary(intent, timeout_sec=2.0)
                            _ack = _build_ack_message(intent, _summary)
                            send_reply_fn(cid, _ack)
                        except Exception as _ae:
                            logger.debug("[IM Dispatcher] 延时安抚发送失败: %s", _ae)

                    _ACK_DELAY_SEC = 12.0
                    _ack_timer = threading.Timer(_ACK_DELAY_SEC, _delayed_ack)
                    _ack_timer.daemon = True
                    _ack_timer.start()
                    try:
                        # 不设超时：Agent 跑多久都等；结果通过 send_reply_fn 直连 API 推回主群
                        reply = future.result(timeout=None)
                        turn_status = "ok"
                    finally:
                        _ack_cancelled.set()
                        _ack_timer.cancel()
            except TimeoutError:
                reply = "处理超时，请稍后重试。"
                turn_status = "timeout"
                logger.warning("[IM Dispatcher] Agent 超时 chat_id=%s", cid[:20] if cid else "")
            except Exception as e:
                logger.exception("[IM Dispatcher] Agent 异常: %s", e)
                reply = "抱歉，处理时发生错误，请稍后重试。"
                turn_status = "error"
                err_msg = str(e)
                err_tb = traceback.format_exc()
        if cid and session_messages:
            save_lark_session(cid, session_messages)
            logger.debug("[IM Dispatcher] chat_id=%s 已保存会话 %d 条", cid[:20], len(session_messages))
        if reply and cid:
            ok = send_reply_fn(cid, str(reply).strip())
            send_ok = ok
            if not ok:
                logger.warning("[IM Dispatcher] 回复发送失败 chat_id=%s", cid[:20])
        elif cid and not (reply or "").strip():
            turn_status = f"{turn_status}|empty_reply" if turn_status != "pending" else "empty_reply"

        append_lark_interaction_record(
            "turn_finished",
            chat_id=cid,
            user_id=user_id or "",
            user_text=intent,
            reply=(reply or "").strip(),
            route=route,
            status=turn_status,
            error=err_msg,
            error_trace=err_tb,
            send_ok=send_ok,
        )


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
        try:
            from core.cron_thinker import _audit_log, feed_release_announcement_text, release_title_present

            _audit_log(
                "lark_im_cron_thinker_probe",
                chat_id=(chat_id or "")[:96],
                user_id_prefix=(user_id or "")[:24],
                text_len=len(text or ""),
                title_needle_hit=release_title_present(text or ""),
            )
            feed_release_announcement_text(text, source="lark", chat_id=chat_id or None)
        except Exception:
            logger.debug("[IM Dispatcher] cron_thinker 公告 ingest 失败", exc_info=True)
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
