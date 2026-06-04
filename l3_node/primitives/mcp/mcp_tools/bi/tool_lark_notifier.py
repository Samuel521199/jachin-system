"""
飞书推送工具 — mcp:atom_lark_notifier

契约: docs/bi_daily_report/01_PARALLEL_DEVELOPMENT_GUIDE.md
使用 l3_node.channels.lark 通道层实现。
支持两种模式：Webhook URL（群自定义机器人）或 chat_id + App 凭证（应用机器人）。

配置: config/mcps/atom_lark_notifier/config.yaml（或 ~/.jachin/config/mcps/atom_lark_notifier/）
  - app_id, app_secret: IM API；可与进程内其它业务的 LARK_APP_ID 解耦——YAML 里有字面量时优先用于本工具
  - lark_use_feishu: **覆盖**进程环境里的国际/国内域名选择（避免根 .env 残留 FEISHU=1 却把租户配在国际 Lark）
  - default_chat_id: 未传 chat_id 时使用
  - native_table_card: 默认开启；若 ``markdown_content`` 含 GFM 表格则使用 **飞书卡片 2.0 / tag:table**（无表则与旧版同为单块 lark_md）。显式 ``false`` 或 ``JACHIN_LARK_NATIVE_TABLE_CARD=0`` 可关闭
  - native_table_page_size: （可选）每张原生表「每页可见行数」1～10，默认 10；也可用 ``JACHIN_LARK_NATIVE_TABLE_PAGE_SIZE`` 覆盖（PMO 宏观看板常见设为 4）
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_root = Path(__file__).resolve().parent.parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from l3_node.channels.lark import send_markdown
from l3_node.channels.lark.im import send_interactive_card, send_markdown_card
from l3_node.channels.lark.webhook import post_interactive_card_webhook
from l3_node.channels.lark.webhook_url import (
    is_valid_lark_incoming_webhook_url,
    looks_like_lark_chat_id,
)


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


def _truthy_native_table(flag: bool | None, cfg: dict[str, Any]) -> bool:
    if flag is not None:
        return bool(flag)
    raw = cfg.get("native_table_card")
    if raw in (True, "true", "1", "yes", "on"):
        return True
    if raw in (False, "false", "0", "no", "off", ""):
        return False
    env = (os.environ.get("JACHIN_LARK_NATIVE_TABLE_CARD") or "").strip().lower()
    if env in ("1", "true", "yes", "on"):
        return True
    if env in ("0", "false", "no", "off"):
        return False
    # 配置与环境均未显式声明时默认开启（避免 ~/.jachin 旧 YAML 缺键时永远走 lark_md）
    return True


def _native_table_page_size(cfg: dict[str, Any]) -> int:
    """飞书 tag:table 的 page_size（1～10）；优先环境变量，其次 MCP YAML。"""
    env = (os.environ.get("JACHIN_LARK_NATIVE_TABLE_PAGE_SIZE") or "").strip()
    if env:
        try:
            return max(1, min(int(env), 10))
        except ValueError:
            pass
    raw = cfg.get("native_table_page_size")
    if raw is not None:
        try:
            return max(1, min(int(raw), 10))
        except (ValueError, TypeError):
            pass
    return 10


def send_lark_markdown(
    webhook_url: str,
    markdown_content: str,
    title: str | None = None,
    chart_spec: dict | None = None,
    chat_id: str | None = None,
    native_table_card: bool | None = None,
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
        native_table_card: 为 True 且正文含 GFM ``|`` 表格时，使用 **卡片 JSON 2.0** 的 ``tag: table``
            原生组件发送（Webhook / IM 均支持）。``None`` 时读 MCP ``native_table_card``、环境变量
            ``JACHIN_LARK_NATIVE_TABLE_CARD``；均未设置时 **默认 True**（关闭请写 ``false`` / ``0``）。

    Returns:
        {"status": "success", "msg": "飞书已送达"} 或 {"status": "error", "error": "..."}
    """
    cfg = _load_atom_lark_notifier_config()
    api_base = _im_api_base_from_notifier_cfg(cfg)
    os.environ["LARK_USE_FEISHU"] = (
        "1" if cfg.get("lark_use_feishu") in (True, "true", "1", "yes") else "0"
    )

    _wh = (webhook_url or "").strip()
    if not is_valid_lark_incoming_webhook_url(_wh):
        if looks_like_lark_chat_id(_wh):
            if not (chat_id or "").strip():
                chat_id = _wh
        _wh = ""
    cfg_wh = (cfg.get("default_webhook_url") or "").strip()
    if not _wh and is_valid_lark_incoming_webhook_url(cfg_wh):
        _wh = cfg_wh
    has_webhook = bool(_wh)

    _chat_id = (chat_id or "").strip()
    if not _chat_id:
        _chat_id = (cfg.get("default_chat_id") or "").strip()
        if str(_chat_id).startswith("${"):
            _chat_id = ""

    use_native = _truthy_native_table(native_table_card, cfg) and not chart_spec
    if use_native:
        from l3_node.channels.lark.md_native_table_card import build_schema_v2_card_from_markdown

        try:
            _mt = int((os.environ.get("JACHIN_LARK_NATIVE_TABLE_MAX") or "5").strip() or "5")
        except ValueError:
            _mt = 5
        _mt = max(1, min(_mt, 5))
        _ps = _native_table_page_size(cfg)
        v2 = build_schema_v2_card_from_markdown(
            markdown_content or "",
            title,
            max_tables=_mt,
            table_page_size=_ps,
        )
        if v2 is None:
            mc = markdown_content or ""
            if mc.count("|") >= 15:
                logger.warning(
                    "[atom_lark_notifier] native_table_card 已开启但正文未解析出可用 GFM 管道表 "
                    "(可能缺分隔行 `| :--- | :--- |`、表被围栏 ``` 包住或非标 `|` 对齐)；降为 lark_md 整卡，"
                    "**无右下角分页**。三节战报表须裸写于 markdown_content，勿置于代码围栏内。"
                )
        if v2 is not None:
            if has_webhook:
                return post_interactive_card_webhook(_wh, v2)
            if _chat_id:
                aid, sec = _cfg_app_pair(cfg)
                ic_kw: dict[str, Any] = {
                    "receive_id": _chat_id,
                    "card": v2,
                    "receive_id_type": "chat_id",
                    "api_base": api_base,
                    "http_timeout": 60.0,
                }
                if aid and sec:
                    ic_kw["app_id"] = aid
                    ic_kw["app_secret"] = sec
                else:
                    _inject_env_app_from_cfg_if_missing(cfg)
                return send_interactive_card(**ic_kw)

    if chart_spec and has_webhook:
        return send_markdown(
            webhook_url=_wh,
            markdown_content=markdown_content,
            title=title,
            chart_spec=chart_spec,
        )

    if has_webhook and not chart_spec:
        return send_markdown(
            webhook_url=_wh,
            markdown_content=markdown_content,
            title=title,
            chart_spec=chart_spec,
        )

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
