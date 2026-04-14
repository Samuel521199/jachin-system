"""
WebSocket / 终端 UI 推送前的回复净化：减少 Thought、ReAct 标签与口吃式重复泄漏到用户可见区。

环境变量：
- ``JACHIN_WS_EXPOSE_THOUGHT``：设为 ``1`` / ``true`` 时仍向前端推送原始 ``thought`` 步骤内容（默认不暴露全文）。
"""
from __future__ import annotations

import os
import re
from typing import Final

_RE_THOUGHT_LINE = re.compile(
    r"(?im)^\s*Thought\s*:\s*.+?(?=\n\s*(?:Action\s*:|Final\s+Answer\s*:|Observation\s*:|$))",
    re.DOTALL,
)
_RE_ACTION_BLOCK = re.compile(
    r"(?im)^\s*Action\s*:\s*.+?(?=\n\s*(?:Action\s+Input\s*:|Thought\s*:|Final\s+Answer\s*:|Observation\s*:|$))",
    re.DOTALL,
)
_RE_ACTION_INPUT_BLOCK = re.compile(
    r"(?im)^\s*Action\s+Input\s*:\s*.+?(?=\n\s*(?:Thought\s*:|Action\s*:|Final\s+Answer\s*:|Observation\s*:|$))",
    re.DOTALL,
)
_RE_OBSERVATION_LINE = re.compile(r"(?im)^\s*Observation\s*:\s*.+$", re.MULTILINE)
_RE_FINAL_ANSWER_PREFIX = re.compile(r"(?im)^\s*Final\s+Answer\s*:\s*", re.MULTILINE)

# 连续重复同一短句（口吃复读），保留一遍
_RE_STUTTER = re.compile(r"(.{8,120})(\s*\1\s*){2,}", re.DOTALL)


def expose_raw_thought_to_ws() -> bool:
    return (os.environ.get("JACHIN_WS_EXPOSE_THOUGHT") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


_THOUGHT_PLACEHOLDER_ZH: Final = "正在为您处理请求，请稍候…"


def sanitize_thought_step_for_ui(raw: str) -> str:
    """thought 步骤：默认不向前端输出模型内部冗长推理，避免习得性无助类措辞刺激用户。"""
    if expose_raw_thought_to_ws():
        return _strip_negative_phrasing((raw or "").strip()) or _THOUGHT_PLACEHOLDER_ZH
    t = (raw or "").strip()
    if not t:
        return _THOUGHT_PLACEHOLDER_ZH
    return _THOUGHT_PLACEHOLDER_ZH


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
    """流式 chunk：去掉误混入的 Thought/Action/Observation 行与口吃重复。"""
    if not chunk:
        return chunk
    s = chunk
    s = _RE_THOUGHT_LINE.sub("", s)
    s = _RE_ACTION_BLOCK.sub("", s)
    s = _RE_ACTION_INPUT_BLOCK.sub("", s)
    s = _RE_OBSERVATION_LINE.sub("", s)
    s = _collapse_stutter(s)
    return s


def sanitize_final_answer_for_ui(text: str) -> str:
    """最终回复：剥离 ReAct 脚手架，保留 Final Answer 后正文。"""
    if not (text or "").strip():
        return text
    s = text.strip()
    # 若整段以 Final Answer: 开头，去掉标签
    s = _RE_FINAL_ANSWER_PREFIX.sub("", s, count=1)
    s = _RE_THOUGHT_LINE.sub("", s)
    s = _RE_ACTION_BLOCK.sub("", s)
    s = _RE_ACTION_INPUT_BLOCK.sub("", s)
    s = _RE_OBSERVATION_LINE.sub("", s)
    s = _collapse_stutter(s.strip())
    return s


def _collapse_stutter(s: str) -> str:
    if len(s) < 24:
        return s
    try:
        return _RE_STUTTER.sub(r"\1", s)
    except re.error:
        return s
