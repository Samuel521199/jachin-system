"""
飞书推送工具 — mcp:atom_lark_notifier

契约: docs/bi_daily_report/01_PARALLEL_DEVELOPMENT_GUIDE.md
使用 l3_node.channels.lark 通道层实现。
支持两种模式：Webhook URL（群自定义机器人）或 chat_id + App 凭证（应用机器人）。

配置: config/mcps/atom_lark_notifier/config.yaml（或 ~/.jachin/config/mcps/atom_lark_notifier/）
  - app_id, app_secret: IM API；可与进程内其它业务的 LARK_APP_ID 解耦——YAML 里有字面量时优先用于本工具
  - lark_use_feishu: **覆盖**进程环境里的国际/国内域名选择（避免根 .env 残留 FEISHU=1 却把租户配在国际 Lark）
  - default_chat_id: 未传 chat_id 时使用
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

_root = Path(__file__).resolve().parent.parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from l3_node.channels.lark import send_markdown
from l3_node.channels.lark.im import send_markdown_card


def _load_atom_lark_notifier_config() -> dict[str, Any]:
    try:
        from l3_node.jachin_config import load_mcp_config

        return load_mcp_config("atom_lark_notifier", project_root=_root)
    except Exception:
        return {}


def _cfg_app_pair(cfg: dict[str, Any]) -> tuple[str, str]:
    aid = (cfg.get("app_id") or "").strip()
    sec = (cfg.get("app_secret") or "").strip()
    if str(aid).startswith("${"):
        aid = ""
    if str(sec).startswith("${"):
        sec = ""
    return aid, sec


def _im_api_base_from_notifier_cfg(cfg: dict[str, Any]) -> str:
    from l3_node.channels.lark.client import FEISHU_API_BASE, LARK_API_BASE_DEFAULT

    if cfg.get("lark_use_feishu") in (True, "true", "1", "yes"):
        return FEISHU_API_BASE
    return LARK_API_BASE_DEFAULT


def _inject_env_app_from_cfg_if_missing(cfg: dict[str, Any]) -> None:
    """环境变量未配置应用凭证时，从 MCP YAML 写入（兼容占位符解析前的旧部署）。"""
    if os.environ.get("LARK_APP_ID") or os.environ.get("FEISHU_APP_ID"):
        return
    aid, sec = _cfg_app_pair(cfg)
    if aid and sec:
        os.environ.setdefault("LARK_APP_ID", aid)
        os.environ.setdefault("LARK_APP_SECRET", sec)


def send_lark_markdown(
    webhook_url: str,
    markdown_content: str,
    title: str | None = None,
    chart_spec: dict | None = None,
    chat_id: str | None = None,
) -> dict[str, Any]:
    """
    通过飞书发送 Markdown 消息。
    优先 Webhook URL；若无有效 Webhook（空或含占位符），则用 chat_id + App 凭证（IM API）。

    Args:
        webhook_url: 飞书机器人 Webhook URL，或空
        markdown_content: Markdown 正文
        title: 卡片标题（可选）
        chart_spec: 图表配置（可选），仅 Webhook 模式支持
        chat_id: 群 chat_id（如 oc_xxx），无 Webhook 时用于 IM API 推送

    Returns:
        {"status": "success", "msg": "飞书已送达"} 或 {"status": "error", "error": "..."}
    """
    # 有效 Webhook：非空且非占位符（不以 ${ 开头）
    has_webhook = (webhook_url or "").strip() and not str(webhook_url).strip().startswith("${")
    if has_webhook and not chart_spec:
        return send_markdown(
            webhook_url=webhook_url.strip(),
            markdown_content=markdown_content,
            title=title,
            chart_spec=chart_spec,
        )

    cfg = _load_atom_lark_notifier_config()
    api_base = _im_api_base_from_notifier_cfg(cfg)
    os.environ["LARK_USE_FEISHU"] = (
        "1" if cfg.get("lark_use_feishu") in (True, "true", "1", "yes") else "0"
    )

    _chat_id = (chat_id or "").strip()
    if not _chat_id:
        _chat_id = (cfg.get("default_chat_id") or "").strip()
        if str(_chat_id).startswith("${"):
            _chat_id = ""

    if _chat_id:
        aid, sec = _cfg_app_pair(cfg)
        card_kw: dict[str, Any] = {"api_base": api_base}
        if aid and sec:
            card_kw["app_id"] = aid
            card_kw["app_secret"] = sec
        else:
            _inject_env_app_from_cfg_if_missing(cfg)

        return send_markdown_card(
            receive_id=_chat_id,
            markdown_content=markdown_content,
            title=title,
            receive_id_type="chat_id",
            **card_kw,
        )

    if has_webhook and chart_spec:
        return send_markdown(
            webhook_url=webhook_url.strip(),
            markdown_content=markdown_content,
            title=title,
            chart_spec=chart_spec,
        )
    return {
        "status": "error",
        "error": "请配置 default_webhook_url、BI_LARK_WEBHOOK_URL，或在 config 中设置 default_chat_id / BI_LARK_CHAT_ID",
    }


if __name__ == "__main__":
    try:
        from l3_node.jachin_config import load_mcp_config

        cfg = load_mcp_config("atom_lark_notifier", project_root=_root)
        webhook = (cfg.get("default_webhook_url") or "").strip()
        cid = (cfg.get("default_chat_id") or "").strip()
        if str(webhook).startswith("${"):
            webhook = ""
    except Exception:
        webhook = ""
        cid = ""

    SAMPLE_MD = """# 📊 每日 BI 深度分析战报 — Lark 通道测试

本消息由 **tool_lark_notifier** (mcp:atom_lark_notifier) 发送。
"""
    r1 = send_lark_markdown(webhook or "", SAMPLE_MD, title="BI 战报 Lark 测试", chat_id=cid or None)
    print("lark (纯文):", r1)
