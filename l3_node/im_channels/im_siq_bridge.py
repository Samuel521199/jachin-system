"""
飞书 IM → SessionInstructionQueue 全量接入（路线图 §1 · AU · 飞书全量 SIQ）

将 Lark 进线从「线程池 + chat 锁」升级为 SIQ 调度：
  SERIAL   — 同 chat_id 有序执行（替代仅 inflight 排队）
  PARALLEL — 真·双轨：并发执行，不再使用 per-chat 互斥锁

环境变量
--------
JACHIN_SIQ_ENABLE=1                 总开关（与 HTTP 共用）
JACHIN_IM_SIQ_ENABLE=1              飞书专用（未设时跟随 JACHIN_SIQ_ENABLE）
JACHIN_IM_SIQ_DISABLE=1           强制关闭飞书 SIQ（回退旧路径）
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Callable

logger = logging.getLogger(__name__)


def im_siq_enabled() -> bool:
    if (os.environ.get("JACHIN_IM_SIQ_DISABLE") or "").strip().lower() in (
        "1", "true", "yes",
    ):
        return False
    im = (os.environ.get("JACHIN_IM_SIQ_ENABLE") or "").strip().lower()
    if im in ("0", "false", "no"):
        return False
    if im in ("1", "true", "yes"):
        return True
    try:
        from l3_node.session_instruction_queue import siq_enabled

        return siq_enabled()
    except ImportError:
        return False


def im_siq_submission_allowed() -> bool:
    """飞书可单独开 SIQ（JACHIN_IM_SIQ_ENABLE=1），不必与 HTTP 共用 JACHIN_SIQ_ENABLE。"""
    return im_siq_enabled()


def im_session_key(chat_id: str, user_id: str = "") -> str:
    cid = (chat_id or "").strip()
    if cid:
        return f"lark:{cid}"
    return f"lark:anon:{(user_id or 'unknown')[:32]}"


async def peek_im_session_pending(session_key: str) -> tuple[int, int]:
    """返回 (queue_depth, active_parallel)。"""
    try:
        from l3_node.session_instruction_queue import peek_session_queue_depth

        st = await peek_session_queue_depth(session_key)
        return (int(st.get("queue_depth") or 0), int(st.get("active_parallel") or 0))
    except Exception:
        return (0, 0)


async def schedule_im_message_via_siq(
    *,
    text: str,
    chat_id: str,
    user_id: str,
    run_agent_fn: Callable[..., Any],
    engine: Any,
    main_loop: asyncio.AbstractEventLoop,
    send_reply_fn: Callable[[str, str], bool],
    timeout: float,
    prior_inflight_before: int,
    do_agent_work_fn: Callable[..., str],
) -> None:
    """
  由 Lark 主事件循环调度：将一条 IM 消息提交到 SIQ。
  do_agent_work_fn 须为同步函数，返回 reply 文本（飞书路径内仍会 send_reply_fn）。
    """
    if not im_siq_enabled():
        return

    from l3_node.session_instruction_queue import (
        SIQInstruction,
        siq_mode,
        submit_instruction,
    )

    session_key = im_session_key(chat_id, user_id)
    meta = {
        "channel": "lark_im_dispatcher",
        "chat_id": chat_id,
        "user_id": user_id,
        "prior_inflight_before": prior_inflight_before,
    }

    async def execute_fn(instr: SIQInstruction) -> str:
        _scope = ""
        if siq_mode() == "PARALLEL":
            _scope = (instr.instruction_id or "")[:48]
        return await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: do_agent_work_fn(
                instr.intent,
                chat_id,
                user_id,
                run_agent_fn,
                engine,
                main_loop,
                send_reply_fn,
                timeout,
                prior_inflight_before=int(instr.metadata.get("prior_inflight_before") or 0),
                session_scope=_scope,
            ),
        )

    status = await submit_instruction(
        session_key,
        text,
        execute_fn,
        metadata=meta,
    )

    cid = (chat_id or "").strip()
    if status == "rejected" and cid:
        send_reply_fn(cid, "当前会话指令队列已满，请稍后再试。")
        return

    if status == "parallel" and cid:
        try:
            from l3_node.session_instruction_queue import siq_mode as _mode

            if _mode() == "PARALLEL":
                send_reply_fn(
                    cid,
                    "🔀 已按并行模式处理本条消息（与上一任务同时进行，结果将分别回复）。",
                )
        except Exception:
            pass

    logger.info(
        "[IM-SIQ] submitted session=%s status=%s mode=%s prior=%d",
        session_key[:24],
        status,
        siq_mode(),
        prior_inflight_before,
    )
