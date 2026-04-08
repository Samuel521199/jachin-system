"""
LiteLLM / DashScope / Qwen 思考模型流式 delta 统一解析。

Qwen3.5 等：流式阶段可见字常落在 reasoning_content，content 为 []；
结束前 content 可能为 [{'text': '...'}]，不能假定 content 是 str。
"""
from __future__ import annotations

from typing import Any


def coerce_content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                t = item.get("text")
                if isinstance(t, str):
                    parts.append(t)
            else:
                parts.append(str(item))
        return "".join(parts)
    if isinstance(content, dict):
        t = content.get("text")
        if isinstance(t, str):
            return t
        return str(content.get("content") or "")
    return str(content)


def stream_delta_text(d: Any) -> str:
    """从 OpenAI 风格 delta（或等价的 message dict）取出本段应展示的文本。"""
    if d is None:
        return ""

    def _pair(c: Any, r: Any) -> str:
        tc = ""
        if c is not None and c != []:
            tc = coerce_content_to_text(c)
        tr = ""
        if isinstance(r, str):
            tr = r
        elif r is not None:
            tr = str(r)
        if tc:
            return tc
        if tr:
            return tr
        return ""

    if isinstance(d, dict):
        return _pair(d.get("content"), d.get("reasoning_content"))
    c = getattr(d, "content", None)
    r = getattr(d, "reasoning_content", None)
    return _pair(c, r)
