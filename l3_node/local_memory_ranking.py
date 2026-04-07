"""本地记忆条目排序：correction 优先 + 时间倒序（被动注入与检索共用）。"""
from __future__ import annotations

from typing import Any


def sort_entries_by_agent_priority(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """与 get_local_memory_for_prompt / local_memory_search 对齐的排序键。"""
    return sorted(
        entries,
        key=lambda e: (
            0 if str(e.get("tag", "")).lower() == "correction" else 1,
            -float(e.get("timestamp", 0) or 0),
        ),
    )
