"""
日志与广播用的「深截断」工具：递归缩短 dict/list/JSON 字符串中的长 str，避免同步 I/O / WS 撑爆。

**不得**用于改写实际工具入参或执行结果，仅用于 logger / WebSocket / SSE 等观测路径。
"""
from __future__ import annotations

import json
from typing import Any

_MAX_RECURSE_DEPTH = 48


def truncate_large_strings_for_log(data: Any, max_len: int = 500, _depth: int = 0) -> Any:
    """
    递归遍历 dict、list，或对「整段 JSON 文本」先 parse 再处理。
    任意 str 超过 max_len 时截断并追加 ``... [已截断，原长度: {n} 字符]``。

    解析失败、类型异常或非 JSON 的 str 仅按长度截断，不因异常向上抛出。
    """
    if _depth > _MAX_RECURSE_DEPTH:
        return "<…[深度过大已省略]>"

    try:
        if isinstance(data, str):
            s = data
            if len(s) <= max_len:
                return s
            try:
                parsed = json.loads(s)
            except (json.JSONDecodeError, TypeError, ValueError, RecursionError):
                return s[:max_len] + f"... [已截断，原长度: {len(s)} 字符]"
            inner = truncate_large_strings_for_log(parsed, max_len, _depth + 1)
            try:
                return json.dumps(inner, ensure_ascii=False, default=str)
            except Exception:
                return s[:max_len] + f"... [已截断，原长度: {len(s)} 字符]"

        if isinstance(data, dict):
            return {k: truncate_large_strings_for_log(v, max_len, _depth + 1) for k, v in data.items()}

        if isinstance(data, list):
            return [truncate_large_strings_for_log(item, max_len, _depth + 1) for item in data]

        if isinstance(data, tuple):
            return tuple(truncate_large_strings_for_log(item, max_len, _depth + 1) for item in data)

        if isinstance(data, (int, float, bool)) or data is None:
            return data

        if isinstance(data, bytes):
            try:
                dec = data.decode("utf-8", errors="replace")
            except Exception:
                dec = str(data)
            if len(dec) <= max_len:
                return dec
            return dec[:max_len] + f"... [已截断，原长度: {len(dec)} 字符]"

        # 其他可序列化对象：尽量变成 str 再按长度截断，避免 repr 过大
        try:
            text = str(data)
        except Exception:
            return "<…[不可表示]>"
        if len(text) <= max_len:
            return text
        return text[:max_len] + f"... [已截断，原长度: {len(text)} 字符]"
    except Exception:
        try:
            return str(data)[:max_len] + f"... [已截断，原长度: {len(str(data))} 字符]"
        except Exception:
            return "<…[截断异常]>"

def truncate_jsonish_text_for_ws_or_log(
    content: str,
    *,
    max_field_len: int = 500,
    max_total: int = 120_000,
) -> str:
    """
    将可能为 JSON 的文本做字段级截断；非 JSON 则按总长截断。
    用于 WebSocket step content / 广播消息等观测路径。
    """
    if not content:
        return content
    stripped = content.strip()
    if stripped[:1] in "{[":
        try:
            parsed = json.loads(content)
            inner = truncate_large_strings_for_log(parsed, max_len=max_field_len)
            out = json.dumps(inner, ensure_ascii=False, default=str)
            if len(out) <= max_total:
                return out
            return out[:max_total] + f"\n... [已截断，JSON 总长: {len(out)} 字符]"
        except Exception:
            pass
    if len(content) > max_total:
        return content[:max_total] + f"\n... [已截断，原长度: {len(content)} 字符]"
    if len(content) > max_field_len:
        return content[:max_field_len] + f"... [已截断，原长度: {len(content)} 字符]"
    return content
