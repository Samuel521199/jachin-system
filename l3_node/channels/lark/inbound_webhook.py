"""
Lark 通道 — 入站 Webhook 传输层

接收 Lark 的 HTTP 回调，解析事件为 (text, chat_id, user_id)，调用业务回调。
不包含任何业务逻辑，仅负责传输与解析。
"""
from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

_processed_ids: set[str] = set()
_MAX_PROCESSED_IDS = 500


def parse_lark_im_message(body: dict[str, Any]) -> tuple[str, str, str] | None:
    """
    从 Lark 事件 body 解析 im.message.receive 类消息。
    返回 (text, chat_id, user_id)。**chat_id 为会话 ID**（单聊多为 oc_），与 LARK_CHAT_ID / HR 发消息一致。
    解析失败或非消息类事件返回 None。
    """
    if not isinstance(body, dict):
        return None
    header = body.get("header", {})
    ev = body.get("event", body)
    event_type = header.get("event_type") or ev.get("type") or ""
    if "im.message.receive" not in str(event_type) and "message" not in str(event_type).lower():
        return None

    msg = ev.get("message", ev)
    if not isinstance(msg, dict):
        return None
    content_raw = msg.get("content", "{}")
    content = json.loads(content_raw) if isinstance(content_raw, str) else (content_raw or {})
    text = (content.get("text", "") or "").strip()

    chat_id = (
        msg.get("chat_id") or msg.get("open_chat_id")
        or ev.get("chat_id") or ev.get("open_chat_id")
        or body.get("chat_id") or body.get("open_chat_id")
        or ""
    )
    sender = ev.get("sender") or {}
    sid = sender.get("sender_id")
    open_id_log = ""
    if isinstance(sid, dict):
        open_id_log = (sid.get("open_id") or "").strip()
        user_id = (sid.get("user_id") or "").strip()
        sender_type = sid.get("sender_type", "")
    else:
        user_id = str(sid or "") or sender.get("user_id", "")
        sender_type = sender.get("sender_type", "")
    if sender_type == "app":
        return None
    if not text:
        return None
    _cid = chat_id or ""
    _cid_show = (_cid[:36] + "…") if len(_cid) > 36 else _cid
    _txt_show = (text[:48] + "…") if len(text) > 48 else text
    logger.info(
        "[Lark Webhook 入站] 会话ID(chat_id)→可配 LARK_CHAT_ID | %s | sender.open_id=%s sender.user_id=%s | text=%s",
        _cid_show,
        open_id_log or "(empty)",
        user_id or "(empty)",
        _txt_show,
    )
    return text, _cid, user_id or ""


def create_lark_webhook_app(
    on_message: Callable[[str, str, str], None],
    *,
    deduplication: bool = True,
) -> Any:
    """
    创建 Lark Webhook 的 Flask 应用（仅 /lark-webhook 路由）。

    :param on_message: 回调 (text, chat_id, user_id)，收到消息时在后台线程调用
    :param deduplication: 是否对 event_id/message_id 去重
    :return: Flask app，调用方可追加路由后再 app.run()
    """
    try:
        from flask import Flask, request, jsonify
    except ImportError:
        raise ImportError("请安装 flask: pip install flask") from None

    app = Flask(__name__)

    @app.route("/lark-webhook", methods=["GET", "POST"])
    def webhook():
        if request.method == "GET":
            return jsonify({"status": "ok", "message": "Lark Webhook"}), 200

        try:
            body = request.get_json(silent=True)
        except Exception:
            body = None
        if body is None:
            try:
                raw = request.get_data(as_text=True)
                body = json.loads(raw) if raw else {}
            except Exception:
                body = {}
        if not isinstance(body, dict):
            body = {}

        # URL 验证
        if body.get("type") == "url_verification" or (
            body.get("event") and body.get("event", {}).get("type") == "url_verification"
        ):
            challenge = body.get("challenge") or (body.get("event") or {}).get("challenge", "")
            logger.info("URL 验证: challenge=%s", challenge[:20] if challenge else "empty")
            return jsonify({"challenge": challenge or ""}), 200

        if body.get("encrypt"):
            logger.warning("收到加密事件，当前未实现解密，请在 Lark 后台关闭事件加密")
            return jsonify({}), 200

        parsed = parse_lark_im_message(body)
        if parsed is None:
            return jsonify({}), 200
        text, chat_id, user_id = parsed

        header = body.get("header", {})
        msg = body.get("event", body).get("message", body.get("event", body)) or {}
        if deduplication:
            event_id = header.get("event_id") or body.get("event_id") or ""
            message_id = msg.get("message_id", "") if isinstance(msg, dict) else ""
            dedup_key = event_id or message_id or f"{chat_id or 'x'}_{text[:30]}_{id(body)}"
            if dedup_key in _processed_ids:
                logger.info("已处理过 event_id=%s，跳过", (event_id or message_id)[:20] or dedup_key[:30])
                return jsonify({}), 200
            _processed_ids.add(dedup_key)
            if len(_processed_ids) > _MAX_PROCESSED_IDS:
                _processed_ids.clear()

        chat_type = msg.get("chat_type", "") if isinstance(msg, dict) else ""
        logger.info("收到: chat_id=%s chat_type=%s text=%s", chat_id, chat_type, text[:50])

        def _dispatch():
            try:
                on_message(text, chat_id, user_id)
            except Exception:
                logger.exception("on_message 回调异常")

        threading.Thread(target=_dispatch, daemon=True).start()
        return jsonify({}), 200

    return app
