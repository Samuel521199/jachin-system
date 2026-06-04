"""Lark 群机器人 Incoming Webhook URL 校验（区分 chat_id oc_xxx）。"""
from __future__ import annotations

import re

_LARK_INCOMING_HOOK_RE = re.compile(
    r"^https?://open\.(?:feishu\.cn|larksuite\.com)/open-apis/bot/v2/hook/[a-zA-Z0-9_-]+$",
    re.IGNORECASE,
)


def is_valid_lark_incoming_webhook_url(url: str) -> bool:
    """合法 Webhook：须为 bot/v2/hook/{token}，不能是 chat_id。"""
    return bool(_LARK_INCOMING_HOOK_RE.match((url or "").strip()))


def looks_like_lark_chat_id(value: str) -> bool:
    s = (value or "").strip()
    return s.startswith("oc_") and len(s) >= 12
