"""
PMO 对用户可见文案脱敏：内部双推用的「监控群」、``oc_*`` chat_id 不得出现在任何对话回复中。
"""
from __future__ import annotations

import re

_OC_CHAT_RE = re.compile(r"\boc_[a-z0-9]{8,}\b", re.I)

# 内部 SSOT；勿在用户可见字符串中引用
PMO_MONITOR_CHAT_ID = "oc_0e321f92d758ecb44aea5b499c90510b"

_PUSH_CLAUSE_RE = re.compile(
    r"(已成功|已经|战报已).{0,48}(推送|发送|送达).{0,160}",
    re.I | re.DOTALL,
)


def pmo_text_needs_confidential_sanitize(text: str) -> bool:
    s = text or ""
    return (
        "监控群" in s
        or "監控群" in s
        or _OC_CHAT_RE.search(s) is not None
        or bool(re.search(r"主群.{0,48}监控", s))
    )


def sanitize_pmo_confidential_wording(text: str) -> str:
    """
    去掉「监控群」、``oc_*`` chat_id 及「主群（…）与监控群（…）」类对外泄露措辞。
    保留 Sprint / 需求数等业务摘要。
    """
    s = (text or "").strip()
    if not s or not pmo_text_needs_confidential_sanitize(s):
        return s

    # 拆分首句（常为推送确认）与后续业务摘要
    m = re.match(r"^([^。！?\n]+[。！?])(.*)$", s, re.DOTALL)
    if m and pmo_text_needs_confidential_sanitize(m.group(1)):
        head = _sanitize_push_clause(m.group(1))
        tail = (m.group(2) or "").strip()
        if tail:
            tail = sanitize_pmo_confidential_wording(tail) if pmo_text_needs_confidential_sanitize(tail) else tail
            return f"{head}{tail}"
        return head

    return _sanitize_push_clause(s)


def _sanitize_push_clause(clause: str) -> str:
    c = (clause or "").strip()
    if not c:
        return c

    if _PUSH_CLAUSE_RE.search(c) or "监控群" in c or _OC_CHAT_RE.search(c):
        if re.search(r"PMO|K11|宏观战报", c, re.I):
            head = "✅ K11 · PMO 宏观看板战报已推送至飞书，请在本群消息列表中查看卡片。"
        else:
            head = "✅ 战报已成功推送至飞书，请在本群消息列表中查看卡片。"
        return head

    c = _OC_CHAT_RE.sub("", c)
    c = re.sub(r"[与和、]\s*监控群[^。；\n]*", "", c)
    c = re.sub(r"监控群[（(][^）)]*[）)]?", "", c)
    c = re.sub(r"监控群", "", c)
    c = re.sub(r"主群[（(][^）)]*[）)]?", "", c)
    c = re.sub(r"推送至主群", "已推送至飞书", c)
    c = re.sub(r"主群与\s*", "", c)
    c = re.sub(r"[（(]\s*[）)]", "", c)
    c = re.sub(r"\s{2,}", " ", c)
    c = re.sub(r"[，,]\s*[，,]+", "，", c)
    return c.strip()
