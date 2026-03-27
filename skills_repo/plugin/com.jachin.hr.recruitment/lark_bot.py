#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HR Lark 机器人 — 入口脚本

【推荐】L3 已内置 Lark 长连接：配置 ~/.jachin/config/im_channels.yaml 后，
直接启动 L3（python -m l3_node --ws-only）即可，无需单独运行本脚本。

本脚本仍可用于：
  - 本地交互模式：终端输入 → process_lark_message → 可选发到 Lark
  - Webhook 模式：channels 入站 → handle_message → process_lark_message + 发回复
  - 长连接模式：python lark_bot.py --long-connection（与 L3 内置等价）

使用方式（在包目录或从仓库根用 python -m 调用时，请保证 cwd / PYTHONPATH 含本包根）：
  cd skills_repo/plugin/com.jachin.hr.recruitment
  python lark_bot.py --interactive
  python lark_bot.py --webhook --port 5000
  python lark_bot.py --long-connection   # 长连接模式，无需 ngrok
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# 确保仓库根、plugin、HR MCP 包根在 path 中（供 tools.* 导入）
_PKG_ROOT = Path(__file__).resolve().parent  # com.jachin.hr.recruitment
_PLUGIN_ROOT = _PKG_ROOT.parent  # skills_repo/plugin
_PROJECT_ROOT = _PLUGIN_ROOT.parent.parent  # 仓库根
for _p in (_PROJECT_ROOT, _PLUGIN_ROOT, _PKG_ROOT):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)

import logging

logger = logging.getLogger(__name__)

# 招聘类消息：先发「正在处理」避免用户久等
_RECRUITMENT_KEYWORDS = (
    "招聘", "发布", "招人", "职位", "发职位", "post", "java", "python", "工程师"
)


def _ensure_dotenv() -> None:
    from dotenv import load_dotenv
    env_path = _PLUGIN_ROOT / ".env"
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


def _handle_message(text: str, chat_id: str, user_id: str) -> None:
    """收到 Lark 消息时的业务处理：process_lark_message + 发回复"""
    reply_chat_id = chat_id or (os.environ.get("LARK_CHAT_ID") or "").strip()
    if not reply_chat_id:
        if os.environ.get("LARK_DEBUG_EVENT"):
            logger.info("chat_id 缺失，无法回复")
        logger.warning(
            "未从事件中解析到 chat_id，无法回复。"
            "私聊需在飞书后台开通「获取用户发给机器人的单聊消息」权限，"
            "详见 docs/LARK_BOT_CONVERSATION.md"
        )
        return

    if any(kw in text for kw in _RECRUITMENT_KEYWORDS) and os.environ.get("L3_WS_URL"):
        _send_reply(reply_chat_id, "正在处理招聘需求，请稍候…（通常需 1–3 分钟）")

    from tools.atom_lark_chat import process_lark_message
    out = process_lark_message(text, chat_id=reply_chat_id, user_id=user_id)
    reply = out["reply"]
    if reply_chat_id and reply:
        ok = _send_reply(reply_chat_id, reply)
        logger.info("Lark 回复已发送 chat_id=%s len=%d ok=%s", reply_chat_id[:20], len(reply), ok)
    elif reply and not reply_chat_id:
        logger.warning("无 chat_id，无法回复（请在事件中传递或配置 LARK_CHAT_ID）")


def run_interactive() -> None:
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


def run_webhook_server(port: int = 5000) -> None:
    """启动 Webhook 服务器：channels 入站 + mirror-push 路由"""
    try:
        from flask import request, jsonify
    except ImportError:
        print("请安装 flask: pip install flask")
        sys.exit(1)

    from l3_node.channels.lark.inbound_webhook import create_lark_webhook_app

    _ensure_dotenv()
    app = create_lark_webhook_app(on_message=_handle_message)

    @app.route("/api/mirror-push", methods=["POST"])
    def mirror_push():
        """终端-Lark 镜像：L3 将终端发起的回复推送到此接口，由本服务转发到 Lark"""
        try:
            body = request.get_json(silent=True) or {}
            cid = (body.get("chat_id") or "").strip()
            content = body.get("content", "")
            if not cid:
                return jsonify({"ok": False, "error": "chat_id 缺失"}), 400
            ok = _send_reply(cid, str(content) if content is not None else "")
            return jsonify({"ok": ok}), 200
        except Exception as e:
            logger.exception("mirror-push 异常: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500

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


def run_long_connection() -> None:
    """启动 Lark 长连接，无需 ngrok/公网。连接成功后需在 Lark 后台切换订阅方式。"""
    _ensure_dotenv()
    app_id = os.environ.get("LARK_APP_ID") or os.environ.get("FEISHU_APP_ID")
    app_secret = os.environ.get("LARK_APP_SECRET") or os.environ.get("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        print("请配置 LARK_APP_ID 和 LARK_APP_SECRET（在 .env 或环境变量中）")
        sys.exit(1)
    try:
        from l3_node.channels.lark.long_connection import start_long_connection
    except ImportError as e:
        print(f"长连接依赖缺失: {e}")
        print("请安装: pip install lark-oapi")
        sys.exit(1)
    domain = os.environ.get("LARK_DOMAIN") or os.environ.get("FEISHU_DOMAIN")
    if not domain or not domain.startswith("http"):
        domain = "https://open.feishu.cn" if os.environ.get("LARK_USE_FEISHU", "").lower() in ("1", "true", "yes") else "https://open.larksuite.com"
    print("[长连接模式] 无需 ngrok，连接成功后请在 Lark 后台切换订阅方式为「使用长连接接收回调」")
    start_long_connection(app_id, app_secret, on_message=_handle_message, domain=domain)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--interactive", "-i", action="store_true", help="本地交互模式（终端输入）")
    p.add_argument("--webhook", "-w", action="store_true", help="启动 Webhook 服务器")
    p.add_argument("--long-connection", "-l", action="store_true", help="长连接模式（无需 ngrok）")
    p.add_argument("--port", type=int, default=5000, help="Webhook 端口")
    args = p.parse_args()
    if args.webhook:
        run_webhook_server(args.port)
    elif args.long_connection:
        run_long_connection()
    else:
        run_interactive()
