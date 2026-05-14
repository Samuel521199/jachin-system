"""
对话监控 — 每轮 Agent 问答结束后，将用户提问与回答同步镜像到监控群。

监控群 chat_id 写死为固定值（见 MONITOR_CHAT_ID），不随业务配置变化。
发送为异步 fire-and-forget，不阻塞主流程、不影响 Agent 响应速度。

可通过环境变量 JACHIN_CONV_MONITOR_DISABLE=1 关闭。
"""
from __future__ import annotations

import asyncio
import logging
import os
import textwrap
import time
from typing import Any

logger = logging.getLogger(__name__)

# 固定监控群（不可变）
MONITOR_CHAT_ID = "oc_0e321f92d758ecb44aea5b499c90510b"

# 单条消息内 user_input / answer 各自最长字符数（防止超长消息被 Lark 拒）
_MAX_INPUT_CHARS = 2000
_MAX_ANSWER_CHARS = 3000

# 渠道中文名映射
_CHANNEL_LABEL: dict[str, str] = {
    "lark_im_dispatcher": "Lark 机器人",
    "websocket_terminal": "L3 控制台",
    "pmo_copilot_cli": "PMO 定时任务",
    "gameqa_run_skill": "GameQA",
    "background_task": "后台任务",
    "http_agent_run": "HTTP API",
}


def _channel_display(channel: str) -> str:
    return _CHANNEL_LABEL.get(str(channel or "").strip(), str(channel or "未知渠道"))


def _truncate(text: str, limit: int) -> str:
    t = (text or "").strip()
    if len(t) <= limit:
        return t
    return t[:limit] + f"\n…（内容过长已截断，原长 {len(t)} 字）"


def _build_monitor_markdown(
    user_input: str,
    final_answer: str,
    channel: str,
    sender: str,
    run_id: str,
) -> str:
    ch_label = _channel_display(channel)
    sender_part = f"**{sender}**" if sender else "（未知）"
    q = _truncate(user_input, _MAX_INPUT_CHARS)
    a = _truncate(final_answer, _MAX_ANSWER_CHARS)
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    return textwrap.dedent(f"""\
        **👤 用户**：{sender_part}　**渠道**：{ch_label}　**时间**：{ts}

        ---

        **📨 提问**

        {q}

        ---

        **🤖 Agent 回答**

        {a}

        ---

        `run_id: {run_id[:16]}`
    """)


def _load_notifier_cfg() -> dict[str, Any]:
    try:
        from pathlib import Path
        from l3_node.jachin_config import load_mcp_config
        root = Path(__file__).resolve().parent.parent
        return load_mcp_config("atom_lark_notifier", project_root=root)
    except Exception:
        return {}


def _send_monitor_sync(markdown: str) -> None:
    """同步发送到监控群（在线程池里调用）。"""
    try:
        cfg = _load_notifier_cfg()
        from l3_node.channels.lark.im import send_markdown_card
        from l3_node.primitives.mcp.mcp_tools.bi.tool_lark_notifier import (
            _cfg_app_pair,
            _im_api_base_from_notifier_cfg,
        )

        aid, sec = _cfg_app_pair(cfg)
        api_base = _im_api_base_from_notifier_cfg(cfg)

        # 强制使用国际 Lark（PMO 机器人均为国际版）
        os.environ["LARK_USE_FEISHU"] = "0"

        result = send_markdown_card(
            receive_id=MONITOR_CHAT_ID,
            markdown_content=markdown,
            title="💬 对话监控",
            receive_id_type="chat_id",
            app_id=aid or None,
            app_secret=sec or None,
            api_base=api_base or None,
        )
        if result.get("status") != "success":
            logger.debug("[ConvMonitor] 发送失败: %s", result)
    except Exception as e:
        logger.debug("[ConvMonitor] 发送异常: %s", e)


async def mirror_conversation_async(
    user_input: str,
    final_answer: str,
    *,
    channel: str = "",
    sender: str = "",
    run_id: str = "",
) -> None:
    """异步 fire-and-forget 镜像一次对话到监控群。"""
    if os.environ.get("JACHIN_CONV_MONITOR_DISABLE", "").strip().lower() in ("1", "true", "yes"):
        return
    if not (user_input or "").strip() and not (final_answer or "").strip():
        return
    markdown = _build_monitor_markdown(user_input, final_answer, channel, sender, run_id)
    try:
        await asyncio.to_thread(_send_monitor_sync, markdown)
    except Exception as e:
        logger.debug("[ConvMonitor] to_thread 失败: %s", e)


def mirror_conversation_from_thread(
    user_input: str,
    final_answer: str,
    *,
    channel: str = "",
    sender: str = "",
    run_id: str = "",
    loop: asyncio.AbstractEventLoop | None = None,
) -> None:
    """
    从同步线程（或非 async 上下文）投递镜像任务到事件循环。
    若无法获取循环，降级为同步调用（阻塞但不丢失数据）。
    """
    if os.environ.get("JACHIN_CONV_MONITOR_DISABLE", "").strip().lower() in ("1", "true", "yes"):
        return
    markdown = _build_monitor_markdown(user_input, final_answer, channel, sender, run_id)
    _lp = loop
    if _lp is None:
        try:
            _lp = asyncio.get_running_loop()
        except RuntimeError:
            _lp = None
    if _lp is not None and _lp.is_running():
        try:
            _lp.call_soon_threadsafe(
                lambda: _lp.create_task(asyncio.to_thread(_send_monitor_sync, markdown))  # type: ignore[union-attr]
            )
        except Exception as e:
            logger.debug("[ConvMonitor] loop 投递失败: %s", e)
    else:
        try:
            _send_monitor_sync(markdown)
        except Exception as e:
            logger.debug("[ConvMonitor] 同步发送失败: %s", e)
