"""
Lark /test 模拟 Skill：落盘测试 txt + 向指定会话发送卡片。

触发：IM 里发「/test」（仅此一条，无额外参数）。
Workspace：优先 ``JACHIN_HOME/workspace``，否则 ``~/.jachin/workspace``
（Windows 上一般为 ``C:\\Users\\<用户>\\.jachin\\workspace``）。
通知会话：``TEST_SKILL_LARK_CHAT_ID``，默认用户提供的 oc_ 群。
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_NOTIFY_CHAT = "oc_367e7998b7dfe39c67d1598101defdfe"
_FILE_CONTENT = "我是一个测试文件"


def _workspace_dir() -> Path:
    home = Path(os.environ.get("JACHIN_HOME", str(Path.home() / ".jachin"))).expanduser()
    return (home / "workspace").resolve()


def _safe_filename_stamp() -> str:
    """本地时间，文件名不含 Windows 非法字符。"""
    return datetime.now().strftime("%Y-%m-%d_%H%M%S")


def _notify_chat_id() -> str:
    return (os.environ.get("TEST_SKILL_LARK_CHAT_ID") or _DEFAULT_NOTIFY_CHAT).strip()


_SLASH_TEST_LITERAL = "/test"
_SLASH_TEST_TOKEN_LEN = len(_SLASH_TEST_LITERAL)


def message_contains_slash_test_token(text: str) -> bool:
    """
    消息中是否出现 **ASCII ``/test`` 词元**（须带斜杠）。

    - ``下午17:14执行/test`` → True
    - ``下午17:14执行test``、裸 ``test`` → False（与本 Skill 无必然联系）
    - ``/testing`` → False（避免子串误匹配）
    """
    t = text or ""
    start = 0
    while True:
        i = t.find(_SLASH_TEST_LITERAL, start)
        if i < 0:
            return False
        after = i + _SLASH_TEST_TOKEN_LEN
        if after >= len(t) or not t[after].isalnum():
            return True
        start = i + 1


def is_slash_test_command(text: str) -> bool:
    """
    仅当**整段消息**（strip 后）恰为 ASCII ``/test`` 时成立。

    明确拒绝：单独 ``test``、``／test``（全角斜杠）、``请执行 /test``、``/testing`` 等，
    避免与正常聊天或英文单词混淆。
    """
    t = (text or "").strip()
    if len(t) != len(_SLASH_TEST_LITERAL):
        return False
    if t != _SLASH_TEST_LITERAL:
        return False
    if not t.isascii():
        return False
    if not t.startswith("/"):
        return False
    return True


def run_test_file_skill() -> dict[str, Any]:
    """
    执行模拟 Skill：写 txt + 发 Lark 卡片。

    Returns:
        dict: ok, path, file_error, card_error, chat_id, markdown_for_card
    """
    out: dict[str, Any] = {
        "ok": False,
        "path": "",
        "file_error": "",
        "card_error": "",
        "chat_id": _notify_chat_id(),
    }
    ws = _workspace_dir()
    name = f"现在是{_safe_filename_stamp()}.txt"
    target = ws / name
    try:
        ws.mkdir(parents=True, exist_ok=True)
        target.write_text(_FILE_CONTENT, encoding="utf-8")
        out["path"] = str(target)
    except OSError as e:
        out["file_error"] = str(e)
        logger.warning("[test-skill] 写文件失败: %s", e)
        return out

    stamp_human = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    md = (
        f"**测试 Skill 已完成**\n\n"
        f"- 时间：`{stamp_human}`\n"
        f"- 文件：`{target}`\n"
        f"- 内容：`{_FILE_CONTENT}`\n"
    )
    out["markdown_for_card"] = md

    chat = out["chat_id"]
    if not chat:
        out["card_error"] = "未配置 TEST_SKILL_LARK_CHAT_ID"
        return out

    try:
        from l3_node.channels.lark.client import get_lark_api_base, resolve_lark_credentials
        from l3_node.channels.lark.im import send_interactive_card

        aid, sec, api_base = resolve_lark_credentials()
        base = api_base or get_lark_api_base()
        card: dict[str, Any] = {
            "config": {"wide_screen_mode": True, "enable_forward": True},
            "header": {
                "title": {"tag": "plain_text", "content": f"✅ 测试文件已创建 · {stamp_human}"},
                "template": "green",
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": md},
                },
                {
                    "tag": "note",
                    "elements": [
                        {
                            "tag": "plain_text",
                            "content": "由 IM 指令 /test 触发（模拟 Skill：lark_test_file_skill）",
                        }
                    ],
                },
            ],
        }
        result = send_interactive_card(
            chat,
            card,
            app_id=aid or None,
            app_secret=sec or None,
            api_base=base,
        )
        if result.get("status") != "success":
            out["card_error"] = str(result.get("error") or result)
            logger.warning("[test-skill] 卡片发送失败: %s", out["card_error"])
        else:
            out["ok"] = True
    except Exception as e:
        out["card_error"] = str(e)
        logger.exception("[test-skill] 发卡片异常")

    return out


def try_test_lark_file_skill_intercept(text: str) -> str | None:
    """
    若消息为 ``/test``，执行并返回对用户可见的回复；否则返回 None。
    """
    if not is_slash_test_command(text):
        return None
    r = run_test_file_skill()
    lines = [
        "【/test 模拟 Skill】",
        f"工作目录：{_workspace_dir()}",
        f"文件：{r.get('path') or '(未写入)'}",
    ]
    if r.get("file_error"):
        lines.append(f"写文件失败：{r['file_error']}")
    elif r.get("path"):
        lines.append("本地文件已写入。")
    if r.get("ok"):
        lines.append(f"卡片已发送到会话：{r.get('chat_id')}")
    elif r.get("card_error"):
        lines.append(f"卡片发送失败：{r['card_error']}")
    return "\n".join(lines)
