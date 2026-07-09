"""
记忆统一门面：被动 prompt 快照已停用；主循环记忆读取统一走
``MemoryRecallAgent -> RelevantMemoryBundle``。``load_merged_local_entries``
仅 **遗留 JSON** 读取；显式检索走 **deep_search**。
"""
from __future__ import annotations

from typing import Any

from l3_node.local_memory_ranking import sort_entries_by_agent_priority


def load_merged_local_entries() -> list[dict[str, Any]]:
    """当前 shard（若有）下的 l3_local 条目。"""
    from l3_node.local_memory import load_raw_entries

    return load_raw_entries()


def snapshot_for_prompt(
    limit: int = 15,
    *,
    prompt_cycle: int | None = None,
    max_idle_prompt_cycles: int | None = None,
) -> str:
    """Compatibility no-op: passive memory prompt snapshots are disabled."""

    return ""


def search_local_unified(
    query: str,
    *,
    top_k: int = 8,
    mmr_lambda: float = 0.55,
    half_life_days: float = 30.0,
    include_memory_md: bool = True,
    candidate_pool: int = 32,
) -> dict[str, Any]:
    from l3_node.local_memory_search import search_local_memories

    return search_local_memories(
        query,
        top_k=top_k,
        mmr_lambda=mmr_lambda,
        half_life_days=half_life_days,
        include_memory_md=include_memory_md,
        candidate_pool=candidate_pool,
    )


def ranked_preview(entries: list[dict[str, Any]], *, limit: int = 12) -> list[dict[str, Any]]:
    return sort_entries_by_agent_priority(entries)[:limit]
