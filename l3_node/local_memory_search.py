"""
L3 本地记忆检索：Memory Nexus（Chroma）`deep_search` 全库向量检索。

已废弃 l3_local.json + MMR/半衰 旧实现；`mmr_lambda` / `half_life_days` / `include_memory_md` 仅作 API 兼容字段。

- **async_search_local_memories**：主事件循环路径应使用，带 ``wait_for`` + ``to_thread`` 熔断。
- **search_local_memories**：同步封装（已在 ``asyncio.to_thread`` 内调用时安全）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def get_local_memory_search_timeout_sec() -> float:
    """供 ``agent_core`` 外层 ``wait_for`` 留出余量。"""
    return _local_memory_search_timeout_sec()


def _local_memory_search_timeout_sec() -> float:
    raw = (os.environ.get("JACHIN_LOCAL_MEMORY_SEARCH_TIMEOUT_SEC") or "30").strip()
    try:
        v = float(raw)
    except ValueError:
        v = 30.0
    return max(1.0, min(v, 120.0))


def _search_local_memories_sync(
    query: str,
    *,
    top_k: int = 8,
    mmr_lambda: float = 0.55,
    half_life_days: float = 30.0,
    include_memory_md: bool = True,
    candidate_pool: int = 32,
) -> dict[str, Any]:
    """同步执行 Chroma deep_search + 结果整形（供 to_thread 使用）。"""
    q = (query or "").strip()
    if not q:
        return {"ok": False, "error": "query 为空", "hits": []}

    lim = max(1, min(50, int(top_k or 8)))
    try:
        from l3_client.local_mcps.jachin_memory_nexus.memory_backend import deep_search
        from l3_node.memory_nexus_bridge import format_deep_search_matches_for_agent

        res = deep_search(query=q, wing=None, limit=lim)
        narrative = format_deep_search_matches_for_agent(res)
    except Exception as e:
        logger.warning("[L3Search] deep_search 失败: %s", e, exc_info=True)
        return {"ok": False, "error": str(e), "hits": [], "meta": {}}

    hits: list[dict[str, Any]] = []
    if res.get("ok"):
        for m in res.get("matches") or []:
            meta = m.get("metadata") or {}
            wing = meta.get("wing") or ""
            room = meta.get("room") or ""
            text = (m.get("text") or "").strip()
            dist = m.get("distance")
            score = None
            if dist is not None:
                try:
                    score = max(0.0, 1.0 / (1.0 + float(dist)))
                except (TypeError, ValueError):
                    score = None
            hits.append({
                "id": m.get("id"),
                "tag": f"{wing}/{room}",
                "content": text[:8000],
                "score": score,
                "source": "memory_nexus_chroma",
                "wing": wing,
                "room": room,
                "distance": dist,
            })

    return {
        "ok": bool(res.get("ok")),
        "query": q,
        "hits": hits,
        "formatted_text": narrative,
        "meta": {
            "backend": "memory_nexus_deep_search",
            "limit": lim,
            "legacy_compat": {
                "mmr_lambda": mmr_lambda,
                "half_life_days": half_life_days,
                "include_memory_md": include_memory_md,
                "candidate_pool": candidate_pool,
            },
        },
    }


async def async_search_local_memories(
    query: str,
    *,
    top_k: int = 8,
    mmr_lambda: float = 0.55,
    half_life_days: float = 30.0,
    include_memory_md: bool = True,
    candidate_pool: int = 32,
) -> dict[str, Any]:
    """
    异步检索：``to_thread`` 执行同步 Chroma 路径，外层 ``wait_for`` 防止线程永久挂起。
    超时 / 异常 fail-open 返回可序列化 dict（不向外抛）。
    """
    _tmo = _local_memory_search_timeout_sec()
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(
                _search_local_memories_sync,
                query,
                top_k=top_k,
                mmr_lambda=mmr_lambda,
                half_life_days=half_life_days,
                include_memory_md=include_memory_md,
                candidate_pool=candidate_pool,
            ),
            timeout=_tmo,
        )
    except asyncio.TimeoutError:
        logger.warning("[L3Search] 本地记忆检索硬超时（%.1fs）", _tmo)
        return {
            "ok": False,
            "error": "timeout",
            "hits": [],
            "formatted_text": "[系统提示] 本地记忆检索超时，请稍后再试。",
            "meta": {"backend": "timeout"},
        }
    except Exception as e:
        logger.warning("[L3Search] async 检索异常: %s", e, exc_info=True)
        return {
            "ok": False,
            "error": str(e),
            "hits": [],
            "formatted_text": f"[系统提示] 本地记忆检索失败: {e}",
            "meta": {"backend": "error"},
        }


def search_local_memories(
    query: str,
    *,
    top_k: int = 8,
    mmr_lambda: float = 0.55,
    half_life_days: float = 30.0,
    include_memory_md: bool = True,
    candidate_pool: int = 32,
) -> dict[str, Any]:
    """
    同步 API：在**已处于工作线程**或非 asyncio 上下文时调用。
    若在主事件循环线程调用，请改用 ``async_search_local_memories``。
    """
    return _search_local_memories_sync(
        query,
        top_k=top_k,
        mmr_lambda=mmr_lambda,
        half_life_days=half_life_days,
        include_memory_md=include_memory_md,
        candidate_pool=candidate_pool,
    )


def parse_core_local_memory_search_action_input(action_input: str) -> dict[str, Any]:
    """从 ReAct Action Input 解析 ``core:local_memory_search`` 参数（与 native_tools 行为对齐）。"""
    inp = (action_input or "").strip()
    q = ""
    top_k = 8
    mmr_l = 0.55
    half = 30.0
    inc_md = True
    cand = 32
    if inp.startswith("{"):
        try:
            o = json.loads(inp)
            if isinstance(o, dict):
                q = str(o.get("query") or o.get("q") or "").strip()
                if o.get("top_k") is not None:
                    try:
                        top_k = int(o["top_k"])
                    except (TypeError, ValueError):
                        top_k = 8
                if o.get("mmr_lambda") is not None:
                    try:
                        mmr_l = float(o["mmr_lambda"])
                    except (TypeError, ValueError):
                        mmr_l = 0.55
                if o.get("half_life_days") is not None:
                    try:
                        half = float(o["half_life_days"])
                    except (TypeError, ValueError):
                        half = 30.0
                if "include_memory_md" in o:
                    v = o["include_memory_md"]
                    if isinstance(v, str):
                        inc_md = v.lower() in ("1", "true", "yes")
                    else:
                        inc_md = bool(v)
                if o.get("candidate_pool") is not None:
                    try:
                        cand = int(o["candidate_pool"])
                    except (TypeError, ValueError):
                        cand = 32
        except json.JSONDecodeError:
            q = inp
    else:
        q = inp
    return {
        "query": q,
        "top_k": max(1, min(32, top_k)),
        "mmr_lambda": mmr_l,
        "half_life_days": half,
        "include_memory_md": inc_md,
        "candidate_pool": cand,
    }
