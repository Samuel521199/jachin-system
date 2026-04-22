"""遗留 JSON 条目排序：correction 优先 + 时间倒序（仅仍读 `l3_local*.json` 的路径使用；L1 主路径为 Memory Nexus）。"""
from __future__ import annotations

from typing import Any


def sort_entries_by_agent_priority(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """与旧版 JSON 列表展示/排序对齐的排序键。"""
    return sorted(
        entries,
        key=lambda e: (
            0 if str(e.get("tag", "")).lower() == "correction" else 1,
            -float(e.get("timestamp", 0) or 0),
        ),
    )
