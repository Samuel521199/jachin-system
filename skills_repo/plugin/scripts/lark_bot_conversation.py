#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Lark 机器人 AI 对话 - 入口脚本

核心逻辑在 tools/atom_lark_chat.py，本脚本仅负责：
  - 本地交互模式：终端输入 → 调用 process_lark_message → 可选发到 Lark
  - Webhook 模式：Flask 接收事件 → 调用 process_lark_message → 发回复

使用方式：
  python scripts\\lark_bot_conversation.py --interactive
  python scripts\\lark_bot_conversation.py --webhook --port 5000
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 已处理的消息/事件 ID，用于去重（避免 Lark 重试导致重复回复）
_processed_ids = set()
_MAX_PROCESSED_IDS = 500
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "2-track-a-atomic-mcp"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _ensure_dotenv():
    from dotenv import load_dotenv
    env_path = ROOT / ".env"
    load_dotenv(env_path)
    if not env_path.exists():
        logger.warning("未找到 .env 文件: %s，L3_WS_URL 等需在环境中手动配置", env_path)


def _send_reply(chat_id: str, text: str) -> bool:
    """向 Lark 发送回复"""
    _ensure_dotenv()
    try:
        from tools.atom_lark_send_message import _get_tenant_access_token, _send_lark_message
        token = _get_tenant_access_token()
        return _send_lark_message(token, chat_id, text)
    except Exception as e:
        logger.warning("发送回复失败: %s", e)
        return False


def run_interactive():
    """本地交互模式：终端输入，模拟对话"""
    _ensure_dotenv()
    chat_id = os.environ.get("LARK_CHAT_ID", "")
    l3_url = os.environ.get("L3_WS_URL", "").strip()
    print("=== Lark HR 机器人 - 本地交互模式 ===")
    if l3_url:
        print(f"[L3 壳模式] 对话转发至 Jachin L3: {l3_url}")
    else:
        print("输入问题将用百炼回复；任务关键词（同步、抓取等）仅记录")
    print("输入 quit 退出\n")
    if not chat_id:
        print("[提示] 未配置 LARK_CHAT_ID，回复不会发送到 Lark，仅本地显示")

    while True:
        try:
            user_input = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            break
        from tools.atom_lark_chat import process_lark_message
        out = process_lark_message(user_input, chat_id=chat_id, user_id="local")
        reply = out["reply"]
        print("机器人:", reply)
        if chat_id:
            ok = _send_reply(chat_id, reply)
            if ok:
                print("  [已同步到 Lark]")
            else:
                print("  [Lark 发送失败]")


def run_webhook_server(port: int = 5000):
    """启动 Webhook 服务器，接收 Lark 事件"""
    try:
        from flask import Flask, request, jsonify
    except ImportError:
        print("请安装 flask: pip install flask")
        sys.exit(1)

    _ensure_dotenv()
    app = Flask(__name__)

    @app.route("/api/mirror-push", methods=["POST"])
    def mirror_push():
        """终端- Lark 镜像：L3 将终端发起的回复推送到此接口，由本服务转发到 Lark"""
        try:
            body = request.get_json(silent=True) or {}
            chat_id = (body.get("chat_id") or "").strip()
            content = body.get("content", "")
            if not chat_id:
                return jsonify({"ok": False, "error": "chat_id 缺失"}), 400
            ok = _send_reply(chat_id, str(content) if content is not None else "")
            return jsonify({"ok": ok}), 200
        except Exception as e:
            logger.exception("mirror-push 异常: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/lark-webhook", methods=["GET", "POST"])
    def webhook():
        # GET：部分平台会先发 GET 探测，必须返回合法 JSON
        if request.method == "GET":
            return jsonify({"status": "ok", "message": "Lark Webhook"}), 200

        # POST：解析 body，兼容 get_json 失败或 Content-Type 不匹配
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

        # URL 验证：Lark 保存请求地址时会发送，必须返回 {"challenge": "xxx"}
        # 兼容多种结构：直接 body 或 body.event（schema 2.0）
        challenge = body.get("challenge")
        if body.get("type") == "url_verification" or (body.get("event") and body.get("event", {}).get("type") == "url_verification"):
            challenge = challenge or (body.get("event") or {}).get("challenge", "")
            logger.info("URL 验证: challenge=%s", challenge[:20] if challenge else "empty")
            return jsonify({"challenge": challenge or ""}), 200
        # 加密模式：body 可能是 {"encrypt": "xxx"}
        event = body
        if body.get("encrypt"):
            # TODO: 解密，需 LARK_ENCRYPT_KEY
            logger.warning("收到加密事件，当前未实现解密，请在 Lark 后台关闭事件加密")
            return jsonify({})
        # 普通事件：兼容多种 event 结构（schema 1.0 / 2.0、群聊/私聊）
        header = body.get("header", {})
        ev = body.get("event", body)
        event_type = header.get("event_type") or ev.get("type") or ""
        if "im.message.receive" not in str(event_type) and "message" not in str(event_type).lower():
            return jsonify({})

        # 提取 message（兼容 event.message 或 event 即 message）
        msg = ev.get("message", ev)
        if isinstance(msg, dict):
            content_raw = msg.get("content", "{}")
            content = json.loads(content_raw) if isinstance(content_raw, str) else (content_raw or {})
        else:
            content = {}
        text = (content.get("text", "") or "").strip()
        # chat_id：群聊与私聊(p2p)均在此字段，兼容多种路径
        chat_id = ""
        if isinstance(msg, dict):
            chat_id = (msg.get("chat_id") or msg.get("open_chat_id") or ev.get("chat_id") or ev.get("open_chat_id") or "")
        if not chat_id and isinstance(ev, dict):
            chat_id = ev.get("chat_id") or ev.get("open_chat_id") or ""
        # 兼容 body 顶层可能的 chat_id（部分回调格式）
        if not chat_id:
            chat_id = body.get("chat_id") or body.get("open_chat_id") or ""
        sender = ev.get("sender") or {}
        sid = sender.get("sender_id")
        if isinstance(sid, dict):
            user_id = sid.get("user_id", "")
            sender_type = sid.get("sender_type", "")
        else:
            user_id = str(sid or "") or sender.get("user_id", "")
            sender_type = sender.get("sender_type", "")
        # 避免回复机器人自己的消息
        if sender_type == "app":
            return jsonify({})
        if not text:
            logger.debug("忽略空消息: %s", body)
            return jsonify({})

        # 去重：Lark 超时重试会导致同一消息多次触发，避免重复回复
        event_id = header.get("event_id") or body.get("event_id") or ""
        message_id = msg.get("message_id", "") if isinstance(msg, dict) else ""
        dedup_key = event_id or message_id
        if not dedup_key:
            dedup_key = f"{chat_id or 'x'}_{text[:30]}_{id(body)}"
        if dedup_key in _processed_ids:
            logger.info("已处理过 event_id=%s，跳过", (event_id or message_id)[:20] if (event_id or message_id) else dedup_key[:30])
            return jsonify({}), 200
        _processed_ids.add(dedup_key)
        if len(_processed_ids) > _MAX_PROCESSED_IDS:
            _processed_ids.clear()

        # 私聊/群聊均需从事件中拿到 chat_id，否则无法正确回复
        if not chat_id:
            if os.environ.get("LARK_DEBUG_EVENT"):
                logger.info("事件结构(chat_id 缺失): %s", json.dumps(body, ensure_ascii=False, default=str)[:2000])
            logger.warning(
                "未从事件中解析到 chat_id，无法回复。"
                "私聊需在飞书后台开通「获取用户发给机器人的单聊消息」权限，"
                "详见 docs/LARK_BOT_CONVERSATION.md"
            )
            chat_id = os.environ.get("LARK_CHAT_ID", "")  # 仅作为兜底，不推荐
        chat_type = msg.get("chat_type", "") if isinstance(msg, dict) else ""
        logger.info("收到: chat_id=%s chat_type=%s text=%s", chat_id, chat_type, text[:50])

        # 先返回 200，避免 Lark 超时重试导致重复回复；实际处理在后台线程
        def _do_process_and_reply():
            try:
                from tools.atom_lark_chat import process_lark_message
                out = process_lark_message(text, chat_id=chat_id, user_id=user_id)
                reply = out["reply"]
                if chat_id and reply:
                    ok = _send_reply(chat_id, reply)
                    logger.info("Lark 回复已发送 chat_id=%s len=%d ok=%s", chat_id[:20], len(reply), ok)
                elif reply and not chat_id:
                    logger.warning("无 chat_id，无法回复（请在事件中传递或配置 LARK_CHAT_ID）")
            except Exception as e:
                logger.exception("后台处理异常: %s", e)

        threading.Thread(target=_do_process_and_reply, daemon=True).start()
        return jsonify({}), 200

    l3_url = os.environ.get("L3_WS_URL", "").strip()
    if l3_url:
        print("[L3 壳模式] Lark 机器人仅做转发，所有对话由 Jachin L3 执行:", l3_url)
    else:
        print("[独立模式] 使用本地百炼，任务仅记录不执行")
        print("  提示: 在 .env 中添加 L3_WS_URL=ws://127.0.0.1:18981/sensory 可切换到 L3")
    print(f"Webhook 服务: http://0.0.0.0:{port}/lark-webhook")
    print("使用 ngrok 暴露: ngrok http", port)
    print("在 Lark 后台「事件与回调」中配置请求地址为: https://xxx.ngrok.io/lark-webhook")
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--interactive", "-i", action="store_true", help="本地交互模式（终端输入）")
    p.add_argument("--webhook", "-w", action="store_true", help="启动 Webhook 服务器")
    p.add_argument("--port", type=int, default=5000, help="Webhook 端口")
    args = p.parse_args()
    if args.webhook:
        run_webhook_server(args.port)
    else:
        run_interactive()
