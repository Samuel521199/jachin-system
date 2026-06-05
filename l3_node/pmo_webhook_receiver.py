"""
PMO 飞书 Bitable 变更 Webhook / 事件接收器。

POST /webhook/pmo_table_change
  → 飞书 URL 验证 / drive.file.bitable_record_changed_v1
  → 写入 pmo_change_queue
  → 刷新 pmo_bitable_watch 防抖会话
  → 立即 HTTP 200（3s 内）

长连接模式见：scripts/run_pmo_bitable_watch_long_connection.py

SSOT：docs/architecture/PMO_DB_REFACTOR_DESIGN.md · SKILL.change-alert.md
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

_ROUTE = "/webhook/pmo_table_change"
_LARK_EVENT = "drive.file.bitable_record_changed_v1"


def _enqueue_change(payload: dict[str, Any]) -> int | None:
    from l3_node.tools.pmo_db_tools import _connect, ensure_pmo_schema

    ensure_pmo_schema()
    event_body = payload.get("event") if isinstance(payload.get("event"), dict) else payload
    table_id = str(
        payload.get("table_id")
        or event_body.get("table_id")
        or ""
    ).strip()
    if not table_id:
        table_id = "unknown"

    view_id = str(payload.get("view_id") or event_body.get("view_id") or "").strip() or None
    record_id = str(payload.get("record_id") or event_body.get("record_id") or "").strip() or None
    change_type = str(
        payload.get("event_type")
        or payload.get("type")
        or event_body.get("type")
        or "updated"
    ).strip()
    changed = event_body.get("changed_fields") or payload.get("changed_fields")
    changed_json = json.dumps(changed, ensure_ascii=False) if changed is not None else None
    raw_json = json.dumps(payload, ensure_ascii=False)

    conn = _connect()
    try:
        cur = conn.execute(
            """
            INSERT INTO pmo_change_queue
              (table_id, view_id, record_id, change_type, changed_fields, raw_payload, status)
            VALUES (?, ?, ?, ?, ?, ?, 'pending')
            """,
            (table_id, view_id, record_id, change_type, changed_json, raw_json),
        )
        conn.commit()
        return int(cur.lastrowid or 0)
    finally:
        conn.close()


def enqueue_lark_bitable_payload(
    body: dict[str, Any],
    parsed_events: list[dict[str, Any]] | None = None,
) -> int | None:
    """Lark 官方多维表事件入队。"""
    event = body.get("event") if isinstance(body.get("event"), dict) else {}
    table_id = str(event.get("table_id") or "").strip() or "unknown"
    first_rid = ""
    if parsed_events:
        first_rid = str(parsed_events[0].get("record_id") or "").strip()
    payload = {
        **body,
        "table_id": table_id,
        "record_id": first_rid or None,
        "event_type": _LARK_EVENT,
    }
    return _enqueue_change(payload)


def _is_url_verification(body: dict[str, Any]) -> str | None:
    if body.get("type") == "url_verification":
        return str(body.get("challenge") or "")
    ev = body.get("event")
    if isinstance(ev, dict) and ev.get("type") == "url_verification":
        return str(ev.get("challenge") or "")
    return None


def _process_payload_async(body: dict[str, Any]) -> None:
    try:
        header = body.get("header") if isinstance(body.get("header"), dict) else {}
        event_type = str(header.get("event_type") or body.get("event_type") or "").strip()

        if event_type == _LARK_EVENT:
            from l3_node.tools.pmo_bitable_watch import handle_lark_bitable_record_changed

            out = handle_lark_bitable_record_changed(body)
            logger.info(
                "[PMO webhook] Lark bitable 事件 merged=%s queue=%s",
                out.get("merged"),
                out.get("queue_id"),
            )
            return

        queue_id = _enqueue_change(body)
        from l3_node.tools.pmo_bitable_watch import touch_webhook_debounce

        debounce = touch_webhook_debounce(body)
        logger.info(
            "[PMO webhook] 自定义 payload queue_id=%s merged=%s",
            queue_id,
            debounce.get("merged"),
        )
    except Exception:
        logger.exception("[PMO webhook] 异步处理失败")


async def handle_pmo_table_change_webhook(request: Any) -> Any:
    """aiohttp handler：飞书事件订阅 / 自定义 Bitable 变更回调。"""
    from aiohttp import web

    try:
        if request.content_type and "json" in request.content_type:
            body = await request.json()
        else:
            text = await request.text()
            body = json.loads(text) if text.strip() else {}
    except Exception as e:
        logger.warning("[PMO webhook] 解析 body 失败: %s", e)
        return web.json_response({"code": 1, "msg": "invalid json"}, status=400)

    if not isinstance(body, dict):
        return web.json_response({"code": 1, "msg": "body must be object"}, status=400)

    challenge = _is_url_verification(body)
    if challenge is not None:
        logger.info("[PMO webhook] URL 验证 challenge=%s", challenge[:20] if challenge else "")
        return web.json_response({"challenge": challenge})

    if body.get("encrypt"):
        logger.warning("[PMO webhook] 收到加密事件，请在 Lark 后台关闭事件加密或实现解密")
        return web.json_response({})

    threading.Thread(target=_process_payload_async, args=(body,), daemon=True).start()
    return web.json_response({"code": 0, "msg": "ok"})


def register_pmo_webhook_routes(app: Any) -> None:
    """注册 PMO Webhook 路由到 L3 HTTP app。"""
    app.router.add_post(_ROUTE, handle_pmo_table_change_webhook)
    logger.info("[PMO webhook] 已注册 POST %s（Lark 事件 + 自定义 payload）", _ROUTE)
