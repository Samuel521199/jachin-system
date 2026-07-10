"""
Lark 通道 — Webhook 推送（无需 App 凭证）

通过飞书机器人 Webhook URL 发送 Markdown 卡片。
适用于 BI 战报等单向推送场景。
"""
from __future__ import annotations

import json
from typing import Any

try:
    import urllib.request
    import urllib.error  # noqa: F401
    _HAS_URLLIB = True
except ImportError:
    _HAS_URLLIB = False


def post_interactive_card_webhook(webhook_url: str, card: dict[str, Any]) -> dict[str, Any]:
    """
    Webhook 发送任意交互式卡片 JSON（含 ``schema: \"2.0\"`` + ``body.elements`` 内 ``table``）。
    """
    if not _HAS_URLLIB:
        return {"status": "error", "error": "urllib 不可用"}
    if not (webhook_url or "").strip():
        return {"status": "error", "error": "webhook_url 不能为空"}
    if not isinstance(card, dict) or not card:
        return {"status": "error", "error": "card 不能为空"}
    try:
        payload: dict[str, Any] = {"msg_type": "interactive", "card": card}
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            webhook_url.strip(),
            data=data,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            result = json.loads(body) if body.strip() else {}
            if result.get("code") and result.get("code") != 0:
                return {"status": "error", "error": result.get("msg", str(result))}
        return {"status": "success", "msg": "飞书已送达"}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace") if e.fp else str(e)
        return {"status": "error", "error": f"HTTP {e.code}: {err_body[:500]}"}
    except urllib.error.URLError as e:
        return {"status": "error", "error": f"网络错误: {e.reason}"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def send_markdown(
    webhook_url: str,
    markdown_content: str,
    title: str | None = None,
    chart_spec: dict | None = None,
) -> dict[str, Any]:
    """
    通过飞书 Webhook 发送 Markdown 消息，可选附带统计图（Schema 2.0 图表组件）。

    Args:
        webhook_url: 飞书机器人 Webhook URL
        markdown_content: Markdown 正文
        title: 卡片标题（可选）
        chart_spec: 图表配置（可选），VChart 格式。

    Returns:
        {"status": "success", "msg": "飞书已送达"} 或 {"status": "error", "error": "..."}
    """
    if not _HAS_URLLIB:
        return {"status": "error", "error": "urllib 不可用"}
    if not (webhook_url or "").strip():
        return {"status": "error", "error": "webhook_url 不能为空"}
    if not (markdown_content or "").strip():
        return {"status": "error", "error": "markdown_content 不能为空"}

    try:
        md_content = (markdown_content or "").strip()
        card_title = str(title).strip()[:100] if title and str(title).strip() else None

        if chart_spec:
            elements: list[dict[str, Any]] = [
                {"tag": "markdown", "content": md_content},
                {"tag": "chart", "chart_spec": chart_spec},
            ]
            card: dict[str, Any] = {
                "schema": "2.0",
                "config": {"wide_screen_mode": True, "enable_forward": True},
                "body": {"elements": elements},
            }
            if card_title:
                card["header"] = {
                    "title": {"tag": "plain_text", "content": card_title},
                    "template": "blue",
                }
        else:
            card = {
                "config": {"wide_screen_mode": True, "enable_forward": True},
                "elements": [
                    {"tag": "div", "text": {"tag": "lark_md", "content": md_content}},
                ],
            }
            if card_title:
                card["header"] = {
                    "title": {"tag": "plain_text", "content": card_title},
                }

        payload: dict[str, Any] = {"msg_type": "interactive", "card": card}
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            webhook_url.strip(),
            data=data,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            result = json.loads(body) if body.strip() else {}
            if result.get("code") and result.get("code") != 0:
                return {"status": "error", "error": result.get("msg", str(result))}
        return {"status": "success", "msg": "飞书已送达"}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace") if e.fp else str(e)
        return {"status": "error", "error": f"HTTP {e.code}: {err_body[:200]}"}
    except urllib.error.URLError as e:
        return {"status": "error", "error": f"网络错误: {e.reason}"}
    except json.JSONDecodeError as e:
        return {"status": "error", "error": f"响应解析失败: {e}"}
    except Exception as e:
        return {"status": "error", "error": str(e)}
