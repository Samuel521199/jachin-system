"""
WebSocket / 终端 UI 推送前的回复净化：减少 Reasoning / WorkOrder 标签与口吃式重复泄漏到用户可见区。

环境变量：
- ``JACHIN_WS_EXPOSE_REASONING``：设为 ``1`` / ``true`` 时仍向前端推送原始 ``reasoning`` 步骤内容（默认不暴露全文）。
"""
from __future__ import annotations

import os
import re
from typing import Final

_RE_REASONING_LINE = re.compile(
    r"(?im)^\s*Reasoning(?:\s+note)?\s*:\s*.+?(?=\n\s*(?:WorkOrder\s*:|User-facing\s+result\s*:|Verification evidence\s*:|$))",
    re.DOTALL,
)
_RE_WORK_ORDER_BLOCK = re.compile(
    r"(?im)^\s*WorkOrder\s*:\s*.+?(?=\n\s*(?:tool\s+input\s*:|Reasoning(?:\s+note)?\s*:|User-facing\s+result\s*:|Verification evidence\s*:|$))",
    re.DOTALL,
)
_RE_TOOL_INPUT_BLOCK = re.compile(
    r"(?im)^\s*tool\s+input\s*:\s*.+?(?=\n\s*(?:Reasoning(?:\s+note)?\s*:|WorkOrder\s*:|User-facing\s+result\s*:|Verification evidence\s*:|$))",
    re.DOTALL,
)
_RE_OBSERVATION_LINE = re.compile(r"(?im)^\s*Verification evidence\s*:\s*.+$", re.MULTILINE)
_RE_USER_FACING_PREFIX = re.compile(r"(?im)^\s*User-facing\s+result\s*:\s*", re.MULTILINE)
_LEADING_REASONING_TAG = re.compile(r"(?is)^\s*Reasoning(?:\s+note)?\s*:\s*")


def strip_leading_reasoning_tag(text: str) -> str:
    """去掉答复开头的 ``Reasoning note:`` 标签，保留其后正文（不删 User-facing result: 段）。"""
    s = (text or "").strip()
    if not s:
        return text or ""
    return _LEADING_REASONING_TAG.sub("", s, count=1).strip()

# 连续重复同一短句（口吃复读），保留一遍
_RE_STUTTER = re.compile(r"(.{8,120})(\s*\1\s*){2,}", re.DOTALL)


def expose_raw_reasoning_to_ws() -> bool:
    return (os.environ.get("JACHIN_WS_EXPOSE_REASONING") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


_REASONING_PLACEHOLDER_ZH: Final = "正在为您处理请求，请稍候…"


def sanitize_reasoning_step_for_ui(raw: str) -> str:
    """reasoning 步骤：默认不向前端输出模型内部冗长推理，避免习得性无助类措辞刺激用户。"""
    if expose_raw_reasoning_to_ws():
        return _strip_negative_phrasing((raw or "").strip()) or _REASONING_PLACEHOLDER_ZH
    t = (raw or "").strip()
    if not t:
        return _REASONING_PLACEHOLDER_ZH
    return _REASONING_PLACEHOLDER_ZH


def _strip_negative_phrasing(text: str) -> str:
    """弱化明显消极、否定尝试价值的表述（仍可能出现在 dev 模式）。"""
    if not text:
        return text
    subs = [
        (r"没有意义", "需调整策略"),
        (r"没有意\s*义", "需调整策略"),
        (r"重复尝试[^\n。]{0,20}没", "可重试或换路径；"),
        (r"再试也没", "可先检查权限与版本发布再试；"),
        (r"懒得", "建议"),
        (r"不想", "建议"),
    ]
    out = text
    for pat, rep in subs:
        out = re.sub(pat, rep, out, count=0)
    return out


def sanitize_stream_chunk_for_ui(chunk: str) -> str:
    """流式 chunk：去掉误混入的 Reasoning/WorkOrder/Verification evidence 行与口吃重复。"""
    if not chunk:
        return chunk
    s = chunk
    s = _RE_REASONING_LINE.sub("", s)
    s = _RE_WORK_ORDER_BLOCK.sub("", s)
    s = _RE_TOOL_INPUT_BLOCK.sub("", s)
    s = _RE_OBSERVATION_LINE.sub("", s)
    s = _collapse_stutter(s)
    return s


def sanitize_final_answer_for_ui(text: str) -> str:
    """最终回复：剥离 RoleExecutionAgent 脚手架，保留 User-facing result 后正文。"""
    if not (text or "").strip():
        return text
    s = text.strip()
    # 先去掉误当整段答案的 Reasoning note: 行首标签（再处理 User-facing result:）
    s = strip_leading_reasoning_tag(s)
    s = _RE_USER_FACING_PREFIX.sub("", s, count=1)
    s = _RE_REASONING_LINE.sub("", s)
    s = _RE_WORK_ORDER_BLOCK.sub("", s)
    s = _RE_TOOL_INPUT_BLOCK.sub("", s)
    s = _RE_OBSERVATION_LINE.sub("", s)
    s = _collapse_stutter(s.strip())
    try:
        from l3_node.pmo_user_visible_sanitize import sanitize_pmo_confidential_wording

        s = sanitize_pmo_confidential_wording(s)
    except Exception:
        pass
    return s


def sanitize_user_visible_answer(text: str) -> str:
    """所有对用户可见的出口（L3 终端/WS、飞书 IM 等）在 RoleExecutionAgent 净化后统一脱敏 PMO 机密措辞。"""
    return sanitize_final_answer_for_ui(text)


def sanitize_final_answer_for_lark_im(text: str) -> str:
    """
    飞书单聊/群聊 Agent 纯文本回复（``msg_type=text``）推送前净化。

    Lark 文本消息不渲染 Markdown，模型输出的 ``**加粗**`` 会以字面量 ``**`` 展示，影响阅读。
    """
    s = sanitize_final_answer_for_ui(text)
    if not s:
        return s
    return s.replace("**", "")


def _collapse_stutter(s: str) -> str:
    if len(s) < 24:
        return s
    try:
        return _RE_STUTTER.sub(r"\1", s)
    except re.error:
        return s
