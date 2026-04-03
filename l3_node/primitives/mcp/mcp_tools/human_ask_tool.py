"""
Human-in-the-Loop 人工劫持工具 — mcp:human_ask

当大模型调用此工具且无预注入决策时，不返回结果，而是：
  1. 将 prompt_msg 和 options 输出到日志（或通过飞书 webhook / IM 通知）
  2. 抛出 SuspendForHumanException，触发 workflow 挂起等待人工决策

配置：~/.jachin/config/mcps/human_ask/config.yaml（或项目 config/mcps/human_ask/）
  - webhook_url: 群自定义机器人 Webhook（优先）
  - lark_chat_id / default_chat_id: 会话 oc_xxx；每人不同可改配置或设环境变量 HUMAN_ASK_LARK_CHAT_ID
  - app_id / app_secret: 可选，无 Webhook 走 IM API 且未设置 LARK_APP_* 时使用

在 Node 中使用时，必须传入 context 中的 _human_decision（resume 时由 inject 注入）：

    result = ask_human_for_decision("确认执行越权操作？", ["批准", "拒绝"],
                                   injected_choice=context.get("_human_decision"))
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from core.errors import SuspendForHumanException

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# 首次加载后缓存（与进程生命周期一致）；lark_chat_id 可通过环境变量 HUMAN_ASK_LARK_CHAT_ID 每次覆盖
_HUMAN_ASK_CFG: dict[str, Any] | None = None


def _get_human_ask_cfg() -> dict[str, Any]:
    global _HUMAN_ASK_CFG
    if _HUMAN_ASK_CFG is not None:
        return _HUMAN_ASK_CFG
    try:
        from l3_node.jachin_config import load_mcp_config

        _HUMAN_ASK_CFG = load_mcp_config("human_ask", project_root=_REPO_ROOT) or {}
    except Exception:
        _HUMAN_ASK_CFG = {}
    return _HUMAN_ASK_CFG


def _effective_webhook_url(cfg: dict[str, Any]) -> str:
    url = (cfg.get("webhook_url") or "").strip()
    if url and not url.startswith("${"):
        return url
    return ""


def _effective_lark_chat_id(cfg: dict[str, Any]) -> str:
    """环境变量优先，便于每人本地覆盖而无需改文件。"""
    env_cid = (os.environ.get("HUMAN_ASK_LARK_CHAT_ID") or "").strip()
    if env_cid:
        return env_cid
    cid = (cfg.get("lark_chat_id") or cfg.get("default_chat_id") or "").strip()
    if cid and not cid.startswith("${"):
        return cid
    return ""


def _inject_lark_credentials_from_human_ask(cfg: dict[str, Any]) -> None:
    """无全局 LARK_APP_ID 时，允许在 human_ask 配置中单独填应用凭证。"""
    if os.environ.get("LARK_APP_ID") or os.environ.get("FEISHU_APP_ID"):
        return
    aid = (cfg.get("app_id") or "").strip()
    asec = (cfg.get("app_secret") or "").strip()
    if aid and asec and not str(aid).startswith("${"):
        os.environ.setdefault("LARK_APP_ID", aid)
        os.environ.setdefault("LARK_APP_SECRET", asec)
    if cfg.get("lark_use_feishu") in (True, "true", "1", "yes"):
        os.environ.setdefault("LARK_USE_FEISHU", "1")


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

    cfg = _get_human_ask_cfg()
    webhook_url = _effective_webhook_url(cfg)
    chat_id = _effective_lark_chat_id(cfg) or None
    if webhook_url or chat_id:
        try:
            from l3_node.primitives.mcp.mcp_tools.bi.tool_lark_notifier import send_lark_markdown

            _inject_lark_credentials_from_human_ask(cfg)
            body = f"**🛑 人工决策待处理**\n\n{prompt_msg}\n\n**选项:** {opts_str}"
            send_lark_markdown(
                webhook_url or "",
                body,
                title="Jachin HITL",
                chat_id=chat_id,
            )
        except Exception as e:
            logger.debug("[HITL] 飞书通知发送失败（已记录日志）: %s", e)

    raise SuspendForHumanException(prompt_msg=prompt_msg, options=options)
