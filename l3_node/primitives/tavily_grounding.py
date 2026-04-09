"""
Tavily REST 直连：主 ReAct 前「实时知识预取」注入（与 npm `tavily-mcp` 进程内 MCP 解耦）。
失败静默，不向上抛异常。
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_TAVILY_SEARCH_URL = "https://api.tavily.com/search"
# 硬性上限：与产品约束一致，避免阻塞主循环
_FETCH_TIMEOUT_SEC = 3.0


def _truncate(s: str, max_chars: int) -> str:
    if max_chars <= 0 or len(s) <= max_chars:
        return s
    return s[: max_chars - 1].rstrip() + "…"


async def fetch_tavily_context(query: str, max_tokens: int = 1500) -> str:
    """
    异步调用 Tavily Search API，将前 3 条结果拼为极简 Markdown。
    - 无 Key / 超时 / 网络或解析错误 → 返回空字符串。
    - Tavily HTTP 请求超时固定 3s（防雪崩）。
    - max_tokens：输出文本字符预算（近似 token 控制）。
    """
    q = (query or "").strip()
    if not q:
        return ""
    key = (os.environ.get("TAVILY_API_KEY") or "").strip()
    if not key:
        return ""

    body: dict[str, Any] = {
        "query": q[:2000],
        "search_depth": "basic",
        "max_results": 3,
    }

    try:
        import httpx
    except ImportError:
        logger.debug("[tavily_grounding] httpx 不可用，跳过")
        return ""

    try:
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT_SEC) as client:
            resp = await client.post(
                _TAVILY_SEARCH_URL,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {key}",
                },
                json=body,
            )
            if resp.status_code >= 400:
                logger.debug(
                    "[tavily_grounding] HTTP %s body_prefix=%s",
                    resp.status_code,
                    (resp.text or "")[:120],
                )
                return ""
            data = resp.json()
    except Exception as e:
        logger.debug("[tavily_grounding] 请求失败: %s", e)
        return ""

    results = data.get("results")
    if not isinstance(results, list) or not results:
        return ""

    lines: list[str] = []
    for i, r in enumerate(results[:3], 1):
        if not isinstance(r, dict):
            continue
        url = str(r.get("url") or "").strip()
        title = str(r.get("title") or "").strip()
        content = str(r.get("content") or "").strip()
        one = f"[{i}] [Source: {url}]\nTitle: {title}\nContent: {content}"
        lines.append(one)

    if not lines:
        return ""

    out = "\n\n---\n\n".join(lines)
    # max_tokens 作为总字符预算
    try:
        budget = int(max_tokens)
    except (TypeError, ValueError):
        budget = 1500
    return _truncate(out, max(256, min(budget, 32000)))
