"""
Lark 通道 — IM 消息发送（需 App 凭证）

通过 Lark Open API 向群聊/单聊发送文本消息。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from l3_node.channels.lark.client import get_lark_api_base, get_tenant_access_token

logger = logging.getLogger(__name__)


def send_text(
    receive_id: str,
    text: str,
    receive_id_type: str = "chat_id",
    token: str | None = None,
    *,
    app_id: str | None = None,
    app_secret: str | None = None,
    api_base: str | None = None,
) -> dict[str, Any]:
    """
    向 Lark 发送文本消息。

    Args:
        receive_id: 群 chat_id 或 user_id
        text: 消息正文
        receive_id_type: chat_id（群/单聊）或 user_id
        token: 可选，不传则内部获取 tenant_access_token

    Returns:
        {"status": "success", "msg": "..."} 或 {"status": "error", "error": "..."}
    """
    if not (receive_id or "").strip():
        return {"status": "error", "error": "receive_id 不能为空"}
    if not (text or "").strip():
        return {"status": "error", "error": "text 不能为空"}

    try:
        base = api_base or get_lark_api_base()
        tkn = token or get_tenant_access_token(
            app_id=app_id, app_secret=app_secret, api_base=base
        )
        try:
            import requests
        except ImportError:
            return {"status": "error", "error": "请安装 requests: pip install requests"}

        url = f"{base}/im/v1/messages"
        params = {"receive_id_type": receive_id_type or "chat_id"}
        payload = {
            "receive_id": receive_id.strip(),
            "msg_type": "text",
            "content": json.dumps({"text": text}),
        }
        resp = requests.post(
            url,
            params=params,
            headers={"Authorization": f"Bearer {tkn}", "Content-Type": "application/json"},
            json=payload,
            timeout=10,
        )
        data = resp.json()
        if data.get("code") != 0:
            err = data.get("msg", str(data))
            logger.warning("Lark 消息发送失败: %s", err)
            return {"status": "error", "error": str(err)}
        return {"status": "success", "msg": "已送达"}
    except Exception as e:
        logger.warning("Lark 消息发送异常: %s", e)
        return {"status": "error", "error": str(e)}


def send_markdown_card(
    receive_id: str,
    markdown_content: str,
    title: str | None = None,
    receive_id_type: str = "chat_id",
    token: str | None = None,
) -> dict[str, Any]:
    """
    通过 IM API 向群聊/单聊发送 Markdown 卡片（需 App 凭证）。
    当无 Webhook URL 时，可用 chat_id + app 凭证发送战报。

    Args:
        receive_id: 群 chat_id（如 oc_xxx）或 user_id
        markdown_content: Markdown 正文
        title: 卡片标题（可选）
        receive_id_type: chat_id 或 user_id
        token: 可选

    Returns:
        {"status": "success", "msg": "..."} 或 {"status": "error", "error": "..."}
    """
    if not (receive_id or "").strip():
        return {"status": "error", "error": "receive_id 不能为空"}
    if not (markdown_content or "").strip():
        return {"status": "error", "error": "markdown_content 不能为空"}

    try:
        tkn = token or get_tenant_access_token()
        try:
            import requests
        except ImportError:
            return {"status": "error", "error": "请安装 requests: pip install requests"}

        md_content = (markdown_content or "").strip()
        card_title = str(title).strip()[:100] if title and str(title).strip() else None

        card: dict[str, Any] = {
            "config": {"wide_screen_mode": True, "enable_forward": True},
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": md_content}}],
        }
        if card_title:
            card["header"] = {"title": {"tag": "plain_text", "content": card_title}}

        url = f"{get_lark_api_base()}/im/v1/messages"
        params = {"receive_id_type": receive_id_type or "chat_id"}
        payload = {
            "receive_id": receive_id.strip(),
            "msg_type": "interactive",
            "content": json.dumps(card),
        }
        resp = requests.post(
            url,
            params=params,
            headers={"Authorization": f"Bearer {tkn}", "Content-Type": "application/json; charset=utf-8"},
            json=payload,
            timeout=10,
        )
        data = resp.json()
        if data.get("code") != 0:
            err = data.get("msg", str(data))
            logger.warning("Lark 卡片发送失败: %s", err)
            return {"status": "error", "error": str(err)}
        return {"status": "success", "msg": "飞书已送达"}
    except Exception as e:
        logger.warning("Lark 卡片发送异常: %s", e)
        return {"status": "error", "error": str(e)}
