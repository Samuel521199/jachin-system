"""Memory tool compatibility helpers for the Cognitive Kernel."""

from __future__ import annotations

import asyncio
import json


async def recall_memory_search(query: str) -> str:
    """Search Memory Nexus for the legacy ``recall_memory`` pseudo-action."""

    from l3_node.tool_call_cache import store_if_cacheable, try_get_cached

    qn = (query or "").strip()
    cache_inp = json.dumps({"q": qn, "backend": "memory_nexus"}, sort_keys=True, ensure_ascii=False)
    hit = try_get_cached("recall_memory", cache_inp)
    if hit is not None:
        return hit

    try:
        from l3_node.local_memory_search import (
            async_search_local_memories,
            get_local_memory_search_timeout_sec,
        )

        slack = max(2.0, get_local_memory_search_timeout_sec() * 0.1 + 1.0)
        res = await asyncio.wait_for(
            async_search_local_memories(qn, top_k=10, candidate_pool=48),
            timeout=get_local_memory_search_timeout_sec() + slack,
        )
        if not res.get("ok"):
            out = f"[记忆检索失败: {res.get('error') or 'unknown'}]"
        else:
            text = (res.get("formatted_text") or "").strip()
            if not text or "[memory_nexus] 未找到相关记忆" in text:
                out = "[未找到相关记忆]"
            else:
                out = text
        return store_if_cacheable("recall_memory", cache_inp, out)
    except asyncio.TimeoutError:
        return store_if_cacheable("recall_memory", cache_inp, "[记忆检索失败: timeout]")
    except Exception as exc:
        return store_if_cacheable("recall_memory", cache_inp, f"[记忆检索失败: {exc}]")
