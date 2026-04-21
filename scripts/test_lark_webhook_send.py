#!/usr/bin/env python3
"""
向 Lark / 飞书「自定义机器人」Webhook 发送一条测试消息。

飞书开放平台要求 POST JSON，且必须包含 ``msg_type``；否则会返回
``{"code":19002,"msg":"params error, msg_type need"}``。

用法（任选其一）::

    # 推荐：环境变量（勿把 Webhook URL 提交到 Git）
    set LARK_WEBHOOK_URL=https://open.larksuite.com/open-apis/bot/v2/hook/xxxx
    python scripts/test_lark_webhook_send.py

    # 或命令行传入（同一 URL）
    python scripts/test_lark_webhook_send.py "https://open.larksuite.com/open-apis/bot/v2/hook/xxxx"

可选：第二个参数改为 ``text`` 则发送纯文本（``msg_type=text``），默认发送交互卡片（与仓库 ``l3_node/channels/lark/webhook.py`` 一致）。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


def _send_interactive(webhook_url: str) -> dict:
    """msg_type=interactive + card（lark_md）"""
    card: dict = {
        "config": {"wide_screen_mode": True, "enable_forward": True},
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        "✅ **Jachin Webhook 连通测试**\n\n"
                        "- 脚本：`scripts/test_lark_webhook_send.py`\n"
                        "- 格式：`msg_type` + `interactive` 卡片\n\n"
                        "若本消息出现在群内，说明 Webhook 可用。"
                    ),
                },
            },
        ],
        "header": {
            "title": {"tag": "plain_text", "content": "Webhook 测试"},
        },
    }
    payload = {"msg_type": "interactive", "card": card}
    return _post_json(webhook_url, payload)


def _send_text(webhook_url: str) -> dict:
    """msg_type=text（部分老机器人/简单场景）"""
    text = (
        "【Jachin Webhook 测试】\n"
        "这是一条纯文本测试消息（msg_type=text）。"
    )
    payload = {"msg_type": "text", "content": json.dumps({"text": text})}
    return _post_json(webhook_url, payload)


def _post_json(webhook_url: str, payload: dict) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        webhook_url.strip(),
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    try:
        return json.loads(body) if body.strip() else {}
    except json.JSONDecodeError:
        return {"raw": body}


def main() -> int:
    argv = [a for a in sys.argv[1:] if a]
    mode = "interactive"
    if argv and argv[-1] in ("text", "interactive"):
        mode = argv.pop()
    url = (os.environ.get("LARK_WEBHOOK_URL") or "").strip()
    if argv:
        url = argv[0].strip()
    if not url:
        print(
            "用法: set LARK_WEBHOOK_URL=<webhook> 后运行，或:\n"
            '  python scripts/test_lark_webhook_send.py "https://open.larksuite.com/open-apis/bot/v2/hook/..."\n'
            "可选末尾参数: text | interactive（默认 interactive）",
            file=sys.stderr,
        )
        return 2

    try:
        if mode == "text":
            result = _send_text(url)
        else:
            result = _send_interactive(url)
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace") if e.fp else str(e)
        print(f"HTTP {e.code}: {err}", file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(f"网络错误: {e.reason}", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    code = result.get("code")
    # Lark 成功通常为 code 0 或 HTTP 200 且 StatusCode 在 body（视版本）
    if code is not None and int(code) != 0:
        # 部分接口 code 在 data 内；19021 等亦为失败
        print(f"Lark 返回非零 code={code} msg={result.get('msg')}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
