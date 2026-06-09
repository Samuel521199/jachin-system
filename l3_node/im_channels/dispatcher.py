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
import os
import re
import sys
import threading
import time
import traceback
from collections import deque
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


def _im_ack_delay_sec() -> float:
    """Agent 仍未返回时才发安抚；默认 40s，可用 ``JACHIN_IM_ACK_DELAY_SEC`` 覆盖。"""
    raw = (os.environ.get("JACHIN_IM_ACK_DELAY_SEC") or "40").strip()
    try:
        return max(5.0, float(raw))
    except ValueError:
        return 40.0


def _should_send_delayed_ack(user_input: str) -> bool:
    """
    仅对「预期长耗时」的 run_agent 轮次启用延时安抚。
    寒暄/致谢等短句不发送（避免「你好」也弹「请稍候」）。
    """
    t = (user_input or "").strip()
    if not t:
        return False
    try:
        from l3_node.routing.output_format_signals import heuristic_trivial_chitchat_only

        if heuristic_trivial_chitchat_only(t):
            return False
    except Exception:
        pass
    # 显式 Skill / PMO 重型入口另有即时 ack，run_agent 路径不再重复
    try:
        from l3_node.slash_hash_skill_router import is_slash_hash_skill_invocation

        if is_slash_hash_skill_invocation(t):
            return False
    except Exception:
        pass
    if re.match(r"^/pmo\b|^/board\b", t, re.I):
        return False
    return True


def _build_ack_message(user_input: str, summary: str) -> str:
    """用意图摘要或截取原文构造安抚消息（仅长任务超时后发送）。"""
    if summary:
        return f"🤖 仍在{summary}，请稍候…"
    snip = user_input.strip()
    if len(snip) > 50:
        snip = snip[:50] + "…"
    return f"🤖 仍在处理：「{snip}」，请稍候…"


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


def _use_im_chat_lock() -> bool:
    """SIQ 已负责同会话串行/并行调度时，不再叠加 per-chat 互斥锁。"""
    try:
        from l3_node.im_channels.im_siq_bridge import im_siq_enabled

        return not im_siq_enabled()
    except ImportError:
        return True


# 每 chat 当前在线程池中有多少条「已提交尚未结束」的 IM 任务（用于第二条进线立即 ack）
_im_chat_inflight: dict[str, int] = {}
_im_chat_inflight_mutex = threading.Lock()


def _adjust_im_chat_inflight(chat_id: str, delta: int) -> None:
    cid = (chat_id or "").strip()
    if not cid or delta == 0:
        return
    with _im_chat_inflight_mutex:
        n = _im_chat_inflight.get(cid, 0) + delta
        if n <= 0:
            _im_chat_inflight.pop(cid, None)
        else:
            _im_chat_inflight[cid] = n


_rollup_mutex = threading.Lock()
_rollup: dict[str, deque[str]] = {}


def _im_queue_rollup_disabled() -> bool:
    return os.environ.get("JACHIN_IM_QUEUE_ROLLUP_DISABLE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _im_append_queue_rollup(chat_id: str, user_text: str) -> None:
    """prior>0 时摘录进线文案；持锁执行前合并进本轮 intent（轻量排队上下文，路线图 **X**）。"""
    if _im_queue_rollup_disabled():
        return
    cid = (chat_id or "").strip()
    if not cid:
        return
    snip = (user_text or "").strip()[:500]
    if not snip:
        return
    with _rollup_mutex:
        d = _rollup.setdefault(cid, deque(maxlen=12))
        if d and d[-1] == snip:
            return
        d.append(snip)


def _im_consume_queue_rollup_prefix(chat_id: str, current_intent_raw: str) -> str:
    """取出并清空排队摘录，排除与本轮主句完全相同的重复。"""
    cid = (chat_id or "").strip()
    cur = (current_intent_raw or "").strip()
    if not cid or not cur:
        return ""
    with _rollup_mutex:
        d = _rollup.pop(cid, None)
        items = list(d) if d else []
    if not items:
        return ""
    seen: set[str] = set()
    parts: list[str] = []
    for t in items:
        st = (t or "").strip()
        if not st or st == cur:
            continue
        if st in seen:
            continue
        seen.add(st)
        parts.append(st)
    if not parts:
        return ""
    return "【排队期间用户另发（请合并理解）】\n" + "\n".join(parts[:6]) + "\n\n"


def _notify_im_when_prior_turn_inflight(
    chat_id: str,
    user_text: str,
    send_reply_fn: Callable[[str, str], bool],
) -> None:
    cid = (chat_id or "").strip()
    if not cid:
        return
    try:
        from l3_node.im_second_instruction import (
            classify_busy_followup,
            analyze_second_im_intent_llm_sync,
        )
        from l3_node.foreground_run_registry import get_active_run_id
        from l3_node.primitives.agent_tasks.agent_cancel import request_cancel_run

        # AX：LLM 冲突仲裁（JACHIN_IM_LLM_CONFLICT_RESOLVE=1 时启用，否则退回规则版本）
        try:
            import os
            if (os.environ.get("JACHIN_IM_LLM_CONFLICT_RESOLVE") or "").strip().lower() in (
                "1", "true", "yes"
            ):
                _task_summary = getattr(
                    __import__("l3_node.task_runtime_registry", fromlist=["format_combined_runtime_prompt_suffix"]),
                    "format_combined_runtime_prompt_suffix",
                    lambda: "",
                )()
                kind = analyze_second_im_intent_llm_sync(
                    user_text, current_task_summary=_task_summary
                )
            else:
                kind = classify_busy_followup(user_text)
        except Exception:
            kind = classify_busy_followup(user_text)
        if kind == "interrupt":
            rid = get_active_run_id(cid)
            if rid and request_cancel_run(rid):
                send_reply_fn(
                    cid,
                    "⏹️ 已请求停止当前任务；随后将开始处理你这条新消息"
                    "（若底层同步调用无法立即终止，仍可能稍后才完全停下）。",
                )
                return
            send_reply_fn(
                cid,
                "⏹️ 已记下你希望切换任务；若当前轮次无法立刻中止，将在本轮结束后处理本条消息。",
            )
            return
        if kind == "parallel":
            try:
                from l3_node.im_channels.im_siq_bridge import im_siq_enabled
                from l3_node.session_instruction_queue import siq_mode

                if im_siq_enabled() and siq_mode() == "PARALLEL":
                    send_reply_fn(
                        cid,
                        "🔀 已按并行模式排队，本条将与上一任务同时进行（独立会话上下文）。",
                    )
                    return
            except Exception:
                pass
            send_reply_fn(
                cid,
                "🔀 已记录新需求。本会话仍按顺序执行，本条将在上一任务结束后立即处理"
                "（开启 JACHIN_IM_SIQ_ENABLE + JACHIN_SIQ_MODE=PARALLEL 可真·并行）。",
            )
            return
        if kind == "supplement":
            send_reply_fn(
                cid,
                "📝 已理解为对**当前正在进行任务**的补充或纠正；本条仍在队列中，"
                "上轮结束后将携带此意合并处理（真·并行仍见路线图后续）。",
            )
            return
        snip = (user_text or "").strip()
        if len(snip) > 52:
            snip = snip[:52] + "…"
        try:
            from l3_node.im_channels.im_siq_bridge import im_siq_enabled

            _siq_on = im_siq_enabled()
        except ImportError:
            _siq_on = False
        if _siq_on:
            send_reply_fn(
                cid,
                f"⏳ 上一任务仍在处理中，本条「{snip}」已进入会话指令队列（SIQ），将按序或并行执行。",
            )
        else:
            send_reply_fn(
                cid,
                f"⏳ 上一任务仍在处理中，本条「{snip}」已排队，将在完成后立即执行。",
            )
    except Exception as e:
        logger.debug("[IM Dispatcher] 第二条进线 ack 失败: %s", e)


def _do_agent_work_tracked(
    text: str,
    chat_id: str,
    user_id: str,
    run_agent_fn: Callable[..., Any],
    engine: Any,
    loop: asyncio.AbstractEventLoop,
    send_reply_fn: Callable[[str, str], bool],
    timeout: float,
    *,
    prior_inflight_before: int = 0,
    session_scope: str = "",
) -> str:
    try:
        return _do_agent_work(
            text,
            chat_id,
            user_id,
            run_agent_fn,
            engine,
            loop,
            send_reply_fn,
            timeout,
            prior_inflight_before=prior_inflight_before,
            session_scope=session_scope,
        )
    finally:
        _adjust_im_chat_inflight(chat_id, -1)


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


def _is_recruitment_message(text: str, *, prior_messages: list | None = None) -> bool:
    """判断是否为招聘类消息（PMO/产研追问不走 HR 包）。"""
    try:
        from l3_node.routing.intent_signals import lark_message_should_use_hr_recruitment

        return lark_message_should_use_hr_recruitment(
            text or "",
            prior_messages=prior_messages if isinstance(prior_messages, list) else None,
        )
    except Exception:
        if not text or not text.strip():
            return False
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
    *,
    prior_inflight_before: int = 0,
    session_scope: str = "",
) -> str:
    """
    在线程池中执行 Agent 工作，不阻塞 Lark WebSocket 线程。
    按 chat_id 加锁，避免同一会话并发导致 session 损坏（SIQ 开启时由队列负责串行/并行）。
    返回 reply 文本（飞书路径内仍会 send_reply_fn）。
    """
    cid = chat_id or ""
    _scope = (session_scope or "").strip()
    lock = _get_chat_lock(cid) if cid and _use_im_chat_lock() else threading.Lock()
    with lock:
        session_messages = load_lark_session(cid, _scope) if cid else []
        intent_raw = (text or "").strip()
        if _should_skip_duplicate_inbound(cid, intent_raw):
            logger.info(
                "[IM Dispatcher] 忽略短时重复投递（同会话同文案）chat_id=%s preview=%s",
                cid[:24] if cid else "",
                intent_raw[:48],
            )
            append_lark_interaction_record(
                "duplicate_inbound_suppressed",
                chat_id=cid,
                user_id=user_id or "",
                user_text=intent_raw,
                route="im_dispatcher",
                status="skipped_duplicate_within_ttl",
            )
            return ""
        rpfx = _im_consume_queue_rollup_prefix(cid, intent_raw)
        intent = (rpfx + intent_raw) if rpfx else intent_raw
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
            # ── 通用定时任务确定性拦截（可选；关闭后完全由 LLM + util:schedule_task，见 JACHIN_DISABLE_DEFERRED_TIMED_TASK_INTERCEPT）
            deferred_reply: str | None = None
            _skip_def_ix = (
                os.environ.get("JACHIN_DISABLE_DEFERRED_TIMED_TASK_INTERCEPT", "")
                .strip()
                .lower()
                in ("1", "true", "yes", "on")
            )
            if not _skip_def_ix:
                try:
                    from l3_node.deferred_task_scheduler import try_generic_timed_task_intercept

                    deferred_reply = try_generic_timed_task_intercept(intent, lark_chat_id=cid or None)
                except Exception as _def_ex:
                    logger.debug("[IM Dispatcher] 通用定时拦截跳过: %s", _def_ex)
            else:
                logger.debug(
                    "[IM Dispatcher] 已按环境变量跳过通用定时拦截（走 LLM） chat_id=%s",
                    cid[:20] if cid else "",
                )

            if deferred_reply is not None:
                route = "deferred_task_scheduler"
                reply = deferred_reply
                turn_status = "ok"
                if cid:
                    session_messages.append({"role": "user", "content": intent})
                    session_messages.append({"role": "assistant", "content": deferred_reply})

            # ── /test 模拟 Skill（落盘 + 指定会话卡片，用于联调）──
            test_reply: str | None = None
            if deferred_reply is None:
                try:
                    from l3_node.lark_test_file_skill import is_slash_test_command, try_test_lark_file_skill_intercept
                    from l3_node.lark_test_schedule import try_test_schedule_intercept

                    if is_slash_test_command(intent):
                        test_reply = try_test_lark_file_skill_intercept(intent)
                    else:
                        test_reply = try_test_schedule_intercept(intent)
                        if test_reply is not None:
                            route = "test_lark_schedule"
                except Exception as _test_ex:
                    logger.debug("[IM Dispatcher] /test 触发器跳过: %s", _test_ex)

            if test_reply is not None and route == "unknown":
                route = "test_lark_file_skill"
            if test_reply is not None:
                reply = test_reply
                turn_status = "ok"
                if cid:
                    session_messages.append({"role": "user", "content": intent})
                    session_messages.append({"role": "assistant", "content": test_reply})
            # ── #*# / /#/ 显式 Skill 触发（PMO 硬路由走 pmo_copilot_cli + 飞书卡片）──
            hash_star_reply: str | None = None
            if deferred_reply is None and test_reply is None:
                try:
                    from l3_node.slash_hash_skill_router import try_hash_star_skill_lark_intercept

                    hash_star_reply = try_hash_star_skill_lark_intercept(
                        intent,
                        cid,
                        send_reply_fn,
                        engine,
                        loop,
                        session_messages,
                    )
                except Exception as _hs_ex:
                    logger.debug("[IM Dispatcher] #*# Skill 触发器跳过: %s", _hs_ex)

            if hash_star_reply is not None:
                route = "hash_star_skill_pmo"
                reply = hash_star_reply
                turn_status = "ok"
                if cid:
                    session_messages.append({"role": "user", "content": intent})
                    session_messages.append({"role": "assistant", "content": hash_star_reply})
            # ── PMO 双重触发器（精确指令 / 模糊确认卡片 / 卡片回复）──
            pmo_reply: str | None = None
            if deferred_reply is None and test_reply is None and hash_star_reply is None:
                try:
                    from l3_node.pmo_lark_trigger import try_pmo_lark_intercept

                    pmo_reply = try_pmo_lark_intercept(
                        intent,
                        cid,
                        user_id or "",
                        send_reply_fn,
                        run_agent_fn,
                        engine,
                        loop,
                        session_messages,
                    )
                except Exception as _pmo_ex:
                    logger.debug("[IM Dispatcher] PMO 触发器跳过: %s", _pmo_ex)

            if test_reply is None and hash_star_reply is None and pmo_reply is not None:
                route = "pmo_lark_trigger"
                reply = pmo_reply
                turn_status = "ok"
                if cid:
                    session_messages.append({"role": "user", "content": intent})
                    session_messages.append({"role": "assistant", "content": pmo_reply})
            elif deferred_reply is None and test_reply is None and hash_star_reply is None:
                try:
                    if _is_hr_package_available() and _is_recruitment_message(
                        intent, prior_messages=session_messages
                    ):
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
                        if prior_inflight_before > 0:
                            from l3_node.im_second_instruction import classify_busy_followup

                            _iatt["lark_busy_followup"] = True
                            _iatt["lark_busy_followup_kind"] = classify_busy_followup(intent)
                        future = asyncio.run_coroutine_threadsafe(
                            run_agent_fn(
                                intent,
                                engine,
                                _session_messages=session_messages,
                                implicit_attribution=_iatt,
                            ),
                            loop,
                        )
                        # 延时安抚：默认 40s 后若 Agent 仍未返回才发（寒暄等短句不启用）
                        _ack_cancelled = threading.Event()
                        _ack_timer: threading.Timer | None = None
                        if _should_send_delayed_ack(intent):

                            def _delayed_ack() -> None:
                                if _ack_cancelled.is_set() or not cid:
                                    return
                                try:
                                    _summary = _quick_task_summary(intent, timeout_sec=2.0)
                                    _ack = _build_ack_message(intent, _summary)
                                    send_reply_fn(cid, _ack)
                                except Exception as _ae:
                                    logger.debug("[IM Dispatcher] 延时安抚发送失败: %s", _ae)

                            _ack_timer = threading.Timer(_im_ack_delay_sec(), _delayed_ack)
                            _ack_timer.daemon = True
                            _ack_timer.start()
                        try:
                            # 不设超时：Agent 跑多久都等；结果通过 send_reply_fn 直连 API 推回主群
                            reply = future.result(timeout=None)
                            turn_status = "ok"
                            try:
                                from l3_node.deferred_task_scheduler import (
                                    heal_schedule_reply_if_bogus,
                                )

                                _healed = heal_schedule_reply_if_bogus(
                                    intent, str(reply or ""), lark_chat_id=cid or None
                                )
                                if _healed:
                                    reply = _healed
                                    if session_messages:
                                        for _hi in range(len(session_messages) - 1, -1, -1):
                                            if session_messages[_hi].get("role") == "assistant":
                                                session_messages[_hi]["content"] = _healed
                                                break
                            except Exception as _h_ex:
                                logger.debug("[IM Dispatcher] deferred heal 跳过: %s", _h_ex)
                        finally:
                            _ack_cancelled.set()
                            if _ack_timer is not None:
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
            save_lark_session(cid, session_messages, _scope)
            logger.debug("[IM Dispatcher] chat_id=%s 已保存会话 %d 条", cid[:20], len(session_messages))
        if reply and cid:
            _out = str(reply).strip()
            try:
                from l3_node.react_ui_sanitize import sanitize_final_answer_for_lark_im

                _out = sanitize_final_answer_for_lark_im(_out)  # UI 脱敏 + 去 Markdown 加粗
            except Exception:
                pass
            ok = send_reply_fn(cid, _out)
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
        return (reply or "").strip()


def get_im_dispatcher_inflight_snapshot(
    *,
    chat_id: str | None = None,
    limit: int = 64,
) -> dict[str, Any]:
    """飞书 IM 分发器：每 chat 已提交线程池且尚未析构的工单计数（与 `_im_chat_inflight` 同源）。"""
    lim = max(1, min(256, int(limit)))
    with _im_chat_inflight_mutex:
        snap = dict(_im_chat_inflight)
    cid = (chat_id or "").strip()
    if cid:
        return {"query_chat_id": cid, "threadpool_submitted_pending": snap.get(cid, 0)}
    items = sorted(snap.items(), key=lambda kv: (-kv[1], kv[0]))[:lim]
    return {
        "chats_with_pending": len(snap),
        "top": [{"chat_id": k, "threadpool_submitted_pending": v} for k, v in items],
    }


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
        cid = (chat_id or "").strip()
        prior_before = 0
        if cid:
            with _im_chat_inflight_mutex:
                prior_before = _im_chat_inflight.get(cid, 0)
                _im_chat_inflight[cid] = prior_before + 1
            if prior_before > 0:
                _notify_im_when_prior_turn_inflight(cid, text, send_reply_fn)
                _im_append_queue_rollup(cid, text)
                if (os.environ.get("JACHIN_IM_SESSION_HOT_INJECT") or "").strip().lower() in (
                    "1",
                    "true",
                    "yes",
                ):
                    try:
                        from l3_node.session_hot_user_inject import record_pending_session_user_text

                        record_pending_session_user_text(cid, text)
                    except Exception:
                        pass
        try:
            from l3_node.im_channels.im_siq_bridge import im_siq_enabled, schedule_im_message_via_siq

            if im_siq_enabled():
                asyncio.run_coroutine_threadsafe(
                    schedule_im_message_via_siq(
                        text=text,
                        chat_id=chat_id,
                        user_id=user_id,
                        run_agent_fn=run_agent_fn,
                        engine=engine,
                        main_loop=loop,
                        send_reply_fn=send_reply_fn,
                        timeout=timeout,
                        prior_inflight_before=prior_before,
                        do_agent_work_fn=_do_agent_work_tracked,
                    ),
                    loop,
                )
                return
        except Exception as _siq_ex:
            logger.warning("[IM Dispatcher] SIQ 调度失败，回退线程池: %s", _siq_ex)
        _AGENT_EXECUTOR.submit(
            _do_agent_work_tracked,
            text,
            chat_id,
            user_id,
            run_agent_fn,
            engine,
            loop,
            send_reply_fn,
            timeout,
            prior_inflight_before=prior_before,
        )

    return on_message
