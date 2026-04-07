"""
§11.4 分类面硬截断：用于网关 / L2 特征，不截断执行面完整 user 消息。
粗略按字符估算 token（~4 字符/token），避免强依赖 tiktoken。
"""
from __future__ import annotations


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


def truncate_for_gateway_classification(
    text: str,
    *,
    max_tokens: int = 2000,
    head_tokens: int = 1000,
    tail_tokens: int = 1000,
) -> tuple[str, bool]:
    """
    若估算超过 max_tokens，则保留「首 head + 尾 tail」拼接（中间省略）。
    返回 (截断后文本, 是否发生过截断)。
    """
    t = text or ""
    if _estimate_tokens(t) <= max_tokens:
        return t, False
    # 字符预算与 token 估算对齐
    head_chars = max(0, head_tokens * 4)
    tail_chars = max(0, tail_tokens * 4)
    if head_chars + tail_chars >= len(t):
        return t[: max_tokens * 4], True
    sep = "\n\n...[gateway_truncation_omitted]...\n\n"
    out = t[:head_chars] + sep + t[-tail_chars:]
    return out, True
