#!/usr/bin/env python3
"""
飞书自建应用 Open API 连通性自检（tenant_access_token + im/v1/messages）。

历史：本脚本曾用于「自定义机器人 Webhook」；仓库已统一巡检等通道为 Open API，
此处改为验证 ``FEISHU_*`` 凭证（与 ``l3_node/channels/lark/kalaroko_inspection_notify.py`` 一致）。

环境变量::

    FEISHU_APP_ID
    FEISHU_APP_SECRET   # 必填（勿提交 Git）
    FEISHU_CHAT_ID      # 群 chat_id（oc_...）

用法::

    python scripts/test_lark_webhook_send.py

脚本会**自动**合并加载项目根 ``.env``（及 ``~/.jachin/.env``，与 ``core.l3_dotenv_merge`` 一致），无需事先 ``export``。

    # 仅模拟成功响应（不调 Open API，供 CI / 无密钥环境）
    python scripts/test_lark_webhook_send.py --mock

可选：末尾 ``text`` 发送纯文本（默认）；``interactive`` 发送一条极简 schema 2.0 卡片。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
_MESSAGES_URL = "https://open.feishu.cn/open-apis/im/v1/messages"


def _load_project_dotenv() -> None:
    """把仓库根（含 ``scripts/`` 的父目录）加入 path，并合并 ``.env`` 到 ``os.environ``。"""
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from core.l3_dotenv_merge import merge_l3_dotenv_into_os

        merge_l3_dotenv_into_os(l3_project_root=str(root))
    except Exception:
        try:
            from dotenv import load_dotenv

            for p in (root / ".env", Path.home() / ".jachin" / ".env"):
                if p.is_file():
                    load_dotenv(p, encoding="utf-8")
        except ImportError:
            pass


def _post_json(
    url: str,
    body: dict,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> dict:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    h = {"Content-Type": "application/json; charset=utf-8"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw) if raw.strip() else {}


def _get_token(app_id: str, app_secret: str) -> str:
    j = _post_json(_TOKEN_URL, {"app_id": app_id, "app_secret": app_secret}, timeout=25.0)
    if int(j.get("code", -1)) != 0:
        raise RuntimeError(f"tenant_token 失败: {j}")
    tok = (j.get("tenant_access_token") or "").strip()
    if not tok:
        raise RuntimeError(f"tenant_token 空: {j}")
    return tok


def _send_text(token: str, chat_id: str, text: str) -> dict:
    url = f"{_MESSAGES_URL}?receive_id_type=chat_id"
    payload = {
        "receive_id": chat_id,
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
    }
    return _post_json(
        url,
        payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=45.0,
    )


def _send_interactive_smoke(token: str, chat_id: str) -> dict:
    card = {
        "schema": "2.0",
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "Open API 连通测试"},
            "template": "green",
        },
        "body": {
            "elements": [
                {
                    "tag": "markdown",
                    "content": "✅ **Jachin Feishu Open API**\n\n脚本：`scripts/test_lark_webhook_send.py`",
                }
            ],
        },
    }
    url = f"{_MESSAGES_URL}?receive_id_type=chat_id"
    payload = {
        "receive_id": chat_id,
        "msg_type": "interactive",
        "content": json.dumps(card, ensure_ascii=False),
    }
    return _post_json(
        url,
        payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=45.0,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="飞书 Open API 连通自检")
    ap.add_argument(
        "--mock",
        action="store_true",
        help="不调真实接口，打印模拟成功 JSON（CI / 无密钥）",
    )
    ap.add_argument(
        "mode",
        nargs="?",
        default="text",
        choices=("text", "interactive"),
        help="发送类型（默认 text）",
    )
    args = ap.parse_args()
    _load_project_dotenv()

    if args.mock:
        fake = {
            "mock": True,
            "tenant_access_token": "mock_tenant_xxx",
            "send": {"code": 0, "data": {"message_id": "mock_om_xxx"}},
        }
        print(json.dumps(fake, ensure_ascii=False, indent=2))
        return 0

    app_id = (os.environ.get("FEISHU_APP_ID") or "").strip()
    secret = (os.environ.get("FEISHU_APP_SECRET") or "").strip()
    chat_id = (os.environ.get("FEISHU_CHAT_ID") or "").strip()
    if not (app_id and secret and chat_id):
        print(
            "请配置 FEISHU_APP_ID、FEISHU_APP_SECRET、FEISHU_CHAT_ID 后重试，或加 --mock。\n"
            "说明见脚本顶部文档字符串。",
            file=sys.stderr,
        )
        return 2

    try:
        token = _get_token(app_id, secret)
    except Exception as e:
        print(f"取 token 失败: {e}", file=sys.stderr)
        return 1

    body = (
        "【Jachin Open API 测试】\n"
        "这是一条纯文本自检（im/v1/messages · msg_type=text）。"
    )
    try:
        if args.mode == "interactive":
            result = _send_interactive_smoke(token, chat_id)
        else:
            result = _send_text(token, chat_id, body)
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace") if e.fp else str(e)
        print(f"HTTP {e.code}: {err}", file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(f"网络错误: {e.reason}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"发送失败: {e}", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    if int(result.get("code", -1)) != 0:
        print(f"飞书返回非零 code={result.get('code')} msg={result.get('msg')}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
