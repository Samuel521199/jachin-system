#!/usr/bin/env python3
"""
BI助手 — Lark 长连接监听脚本

专用于 BI助手 应用的 Lark 长连接，用于在飞书开放平台完成「使用长连接接收回调」的校验与保存。
连接成功后，可在 Lark 开发者后台 → 事件与回调 → 回调配置中启用并保存长连接模式。

收到 Lark 消息后会转发到 L3 WebSocket（若配置 L3_WS_URL），由 L3 Agent 执行并回复到 Lark。

Usage:
  python scripts/run_bi_lark_long_connection.py [--debug]

Env / Config:
  凭证优先级：atom_lark_notifier config > bi_daily_report lark_bitable > .env
  L3_WS_URL=ws://127.0.0.1:18981/sensory  转发到 L3 终端（不配置则仅记录消息）
  LARK_USE_FEISHU=1                       飞书中国版
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

_plugin_root = _root / "skills_repo" / "plugin"
_plugin_mcp = _plugin_root / "2-track-a-atomic-mcp"
for _p in (_root, str(_plugin_root), str(_plugin_mcp)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _apply_l3_ws_url_from_config() -> None:
    """从 atom_lark_notifier config 注入 L3_WS_URL（若环境变量未设置）"""
    if os.environ.get("L3_WS_URL"):
        return
    try:
        from l3_node.jachin_config import load_mcp_config

        cfg = load_mcp_config("atom_lark_notifier", project_root=_root)
        url = (cfg.get("l3_ws_url") or "").strip()
        if url and not str(url).startswith("${"):
            os.environ["L3_WS_URL"] = url
    except Exception:
        pass


def _load_bi_lark_credentials() -> None:
    """加载 Lark 凭证与 L3_WS_URL，优先级：atom_lark_notifier config > bi_daily_report > .env"""
    try:
        from dotenv import load_dotenv

        _env = _root / ".env"
        if _env.exists():
            load_dotenv(_env, encoding="utf-8")
        _plugin_env = _root / "skills_repo" / "plugin" / ".env"
        if _plugin_env.exists():
            load_dotenv(_plugin_env, encoding="utf-8")
    except ImportError:
        pass

    _apply_l3_ws_url_from_config()

    # 1. 优先 atom_lark_notifier config（BI 机器人专用，覆盖 .env 中的 lark_bitable 凭证）
    try:
        from l3_node.jachin_config import load_mcp_config

        cfg = load_mcp_config("atom_lark_notifier", project_root=_root)
        aid = (cfg.get("app_id") or "").strip()
        asec = (cfg.get("app_secret") or "").strip()
        if aid and asec and not str(aid).startswith("${"):
            os.environ["BI_LARK_APP_ID"] = aid
            os.environ["BI_LARK_APP_SECRET"] = asec
            os.environ["LARK_APP_ID"] = aid
            os.environ["LARK_APP_SECRET"] = asec
            if cfg.get("lark_use_feishu") in (True, "true", "1", "yes"):
                os.environ["LARK_USE_FEISHU"] = "1"
            else:
                # 明确使用 Lark 国际版，避免域名不匹配
                os.environ.pop("LARK_USE_FEISHU", None)
            return
    except Exception:
        pass
    try:
        import yaml

        cfg_path = _root / "config" / "skills" / "com.jachin.bi.daily_report" / "bi_daily_report.yaml"
        if cfg_path.exists():
            with open(cfg_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            lb = (cfg.get("lark_bitable") or {})
            aid = (lb.get("app_id") or "").strip()
            asec = (lb.get("app_secret") or "").strip()
            if aid and asec:
                os.environ["BI_LARK_APP_ID"] = aid
                os.environ["BI_LARK_APP_SECRET"] = asec
                os.environ.setdefault("LARK_APP_ID", aid)
                os.environ.setdefault("LARK_APP_SECRET", asec)
                _apply_l3_ws_url_from_config()
                return
    except Exception:
        pass
    _apply_l3_ws_url_from_config()


_load_bi_lark_credentials()


def _send_reply(chat_id: str, text: str) -> bool:
    """向 Lark 发送回复"""
    try:
        from tools.atom_lark_send_message import _get_tenant_access_token, _send_lark_message

        token = _get_tenant_access_token()
        ok = _send_lark_message(token, chat_id, text)
        return ok
    except Exception as e:
        logging.getLogger("bi_lark").warning("发送回复失败: %s", e)
        return False


def _on_message(text: str, chat_id: str, user_id: str) -> None:
    """收到 Lark 消息：转发到 L3，将回复发回 Lark"""
    log = logging.getLogger("bi_lark")
    # 诊断：若能看到此日志说明消息已到达
    log.info("[BI助手] >>> 收到消息 | chat=%s user=%s text=%r", (chat_id or "")[:20], user_id or "-", (text or "")[:100])
    reply_chat_id = (chat_id or "").strip()
    if not reply_chat_id:
        reply_chat_id = (os.environ.get("BI_LARK_CHAT_ID") or os.environ.get("LARK_CHAT_ID") or "").strip()
    if not reply_chat_id:
        try:
            from l3_node.jachin_config import load_mcp_config

            cfg = load_mcp_config("atom_lark_notifier", project_root=_root)
            reply_chat_id = (cfg.get("default_chat_id") or "").strip()
            if str(reply_chat_id).startswith("${"):
                reply_chat_id = ""
        except Exception:
            pass
    if not reply_chat_id:
        log.warning("未解析到 chat_id，无法回复")
        return

    # 快速诊断：发送 ping/测试/hello 时直接回显，验证收→发链路
    t = (text or "").strip()
    quick_test = t.lower() in ("ping", "测试", "test", "hello", "hi")
    if quick_test:
        reply = "pong" if t.lower() == "ping" else "收到！机器人收发正常。"
        ok = _send_reply(reply_chat_id, reply)
        log.info("[BI助手] 快速测试回复 ok=%s", ok)
        return

    try:
        from tools.atom_lark_chat import process_lark_message

        out = process_lark_message(text, chat_id=reply_chat_id, user_id=user_id)
        reply = out.get("reply", "")
        if reply:
            ok = _send_reply(reply_chat_id, reply)
            log.info("[BI助手] 回复已发送 len=%d ok=%s", len(reply), ok)
            if not ok:
                log.warning("发送失败，检查 LARK_USE_FEISHU 是否与 --domain 一致")
        else:
            log.warning("process_lark_message 返回空 reply")
    except Exception as e:
        log.exception("process_lark_message 异常: %s", e)
        _send_reply(reply_chat_id, "抱歉，处理时发生错误，请稍后再试。")


from l3_node.channels.lark.long_connection import (
    FEISHU_DOMAIN,
    LARK_DOMAIN,
    start_long_connection,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="BI助手 Lark 长连接监听")
    parser.add_argument("--debug", action="store_true", help="启用 DEBUG 日志")
    parser.add_argument(
        "--domain",
        choices=["feishu", "lark"],
        default=None,
        help="指定开放平台：feishu=中国版(open.feishu.cn)，lark=国际版(open.larksuite.com)。未指定时从 config 的 lark_use_feishu 读取",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    log = logging.getLogger("bi_lark")

    app_id = os.environ.get("BI_LARK_APP_ID") or os.environ.get("LARK_APP_ID")
    app_secret = os.environ.get("BI_LARK_APP_SECRET") or os.environ.get("LARK_APP_SECRET")

    if not app_id or not app_secret:
        log.error(
            "未配置凭证，请设置环境变量: BI_LARK_APP_ID / BI_LARK_APP_SECRET 或 LARK_APP_ID / LARK_APP_SECRET"
        )
        return 1

    if args.domain == "lark":
        use_feishu = False
        os.environ["LARK_USE_FEISHU"] = "0"  # 发消息 API 也使用 Lark 国际版
    elif args.domain == "feishu":
        use_feishu = True
        os.environ["LARK_USE_FEISHU"] = "1"
    else:
        use_feishu = os.environ.get("LARK_USE_FEISHU", "").lower() in ("1", "true", "yes")
    domain = FEISHU_DOMAIN if use_feishu else LARK_DOMAIN

    l3_url = os.environ.get("L3_WS_URL", "").strip()
    log.info("BI助手长连接启动中 | app_id=%s domain=%s", app_id or "-", domain)
    print(f"\n【请核对】当前连接的应用 ID: {app_id}")
    print("  → 必须在 Lark 后台配置【同一应用】的订阅方式与事件，否则收不到消息")
    print("  → 应用在哪创建就用哪个域名：飞书中国版 open.feishu.cn | 国际版 open.larksuite.com")
    print("  → 报错「Incorrect domain name」时：改用 --domain lark 或 --domain feishu 试另一端\n")
    if l3_url:
        log.info("L3 转发已配置: %s（需同时运行 L3 或桌面端）", l3_url)
    else:
        log.warning("L3_WS_URL 未配置，消息将走本地百炼或仅记录，无法连接 L3 终端")
    print("")
    print("=" * 60)
    print("【必做】收不到消息时：打开 Lark 后台 → 事件与回调 → 订阅方式")
    print("        把「请求地址/Webhook」改为「使用长连接接收事件」→ 保存")
    print("        （必须本脚本 connected 在线时才能保存成功）")
    print("        详见 docs/bi_daily_report/14_LARK_长连接配置步骤.md")
    print("")
    print("  验证：在群里发「ping」或「测试」，终端出现「>>> 收到消息」即表示事件已到达")
    print("=" * 60)
    print("")

    try:
        start_long_connection(
            app_id,
            app_secret,
            _on_message,
            domain=domain,
            log_level="DEBUG" if args.debug else "INFO",
        )
    except KeyboardInterrupt:
        log.info("已退出")
        return 0
    except Exception as e:
        err_code = getattr(e, "code", None)
        err_msg = str(e) or ""
        if err_code == 1000040351 or "Incorrect domain name" in err_msg:
            other = "feishu" if not use_feishu else "lark"
            log.error("域名不匹配：当前应用在「%s」创建，但用了「%s」域名。请改用: --domain %s", "飞书中国版" if use_feishu else "Lark 国际版", domain, other)
            return 1
        log.exception("长连接异常: %s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
