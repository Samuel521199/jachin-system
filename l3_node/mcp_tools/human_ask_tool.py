"""
Human-in-the-Loop 人工劫持工具 — mcp:human_ask

当大模型调用此工具且无预注入决策时，不返回结果，而是：
  1. 将 prompt_msg 和 options 输出到日志（或通过飞书 webhook 通知）
  2. 抛出 SuspendForHumanException，触发 workflow 挂起等待人工决策

在 Node 中使用时，必须传入 context 中的 _human_decision（resume 时由 inject 注入）：

    result = ask_human_for_decision("确认执行越权操作？", ["批准", "拒绝"],
                                   injected_choice=context.get("_human_decision"))
"""
from __future__ import annotations

import logging
from typing import Any

from core.errors import SuspendForHumanException

logger = logging.getLogger(__name__)

# 预留：从 ~/.jachin/config/mcps/human_ask/config.yaml 读取 webhook
_HUMAN_ASK_WEBHOOK: str | None = None


def _get_human_ask_webhook() -> str | None:
    """预留飞书 webhook，用于将待决策信息推送给统帅。"""
    global _HUMAN_ASK_WEBHOOK
    if _HUMAN_ASK_WEBHOOK is not None:
        return _HUMAN_ASK_WEBHOOK
    try:
        from pathlib import Path

        from l3_node.jachin_config import load_mcp_config

        cfg = load_mcp_config("human_ask")
        url = (cfg.get("webhook_url") or "").strip()
        if url and not url.startswith("${"):
            _HUMAN_ASK_WEBHOOK = url
            return url
    except Exception:
        pass
    return None


def ask_human_for_decision(
    prompt_msg: str,
    options: list[str],
    *,
    injected_choice: Any = None,
) -> str:
    """
    向人类请求决策。当被大模型/Node 调用时，不直接返回结果！

    - 若调用方传入 injected_choice（来自 inject_human_decision_and_resume），则直接返回该值。
    - 否则：记录日志（可选发送飞书），并抛出 SuspendForHumanException，挂起 workflow。

    Args:
        prompt_msg: 展示给人类的提示文案
        options: 可选选项列表，如 ["批准", "拒绝", "暂缓"]
        injected_choice: 续跑时由 workflow 注入的人工决策（来自 context["_human_decision"]）

    Returns:
        当存在 injected_choice 时，返回该值；否则永不返回（抛出异常）
    """
    if injected_choice is not None:
        return str(injected_choice)

    # 无预注入：输出到日志，预留飞书 webhook
    opts_str = " | ".join(options) if options else "(无选项)"
    log_line = f"[HITL] 等待人工决策: {prompt_msg}\n  选项: {opts_str}"
    logger.warning(log_line)

    webhook_url = _get_human_ask_webhook()
    if webhook_url:
        try:
            from l3_node.channels.lark import send_markdown

            body = f"**🛑 人工决策待处理**\n\n{prompt_msg}\n\n**选项:** {opts_str}"
            send_markdown(webhook_url=webhook_url, markdown_content=body, title="Jachin HITL")
        except Exception as e:
            logger.debug("[HITL] 飞书 webhook 发送失败（已记录日志）: %s", e)

    raise SuspendForHumanException(prompt_msg=prompt_msg, options=options)
