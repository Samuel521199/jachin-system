"""
Native **core:local_memory_append**：向 ~/.jachin/memory/l3_local.json 追加一条结构化记忆。

与 core:local_memory_search / 被动注入共用同一存储；禁止模型幻觉写入 MEMORY.md。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def run_local_memory_append(*, content: str, tags: list[str] | None = None) -> dict[str, Any]:
    """
    将一条事实/偏好写入 l3_local.json（数组 append，带时间戳）。

    - tags：可选；第一个标签作为主 `tag` 字段（缺省为 preference），完整列表写入条目的 `tags`。
    """
    text = (content or "").strip()
    if not text:
        return {"ok": False, "error": "empty_content", "message": "content 不能为空。"}

    raw = tags if isinstance(tags, list) else []
    norm_tags = [str(t).strip() for t in raw if str(t).strip()]
    primary = norm_tags[0] if norm_tags else "preference"

    try:
        from l3_node.local_memory import add_local_memory

        add_local_memory(
            primary,
            text,
            source="core:local_memory_append",
            tags_list=norm_tags if norm_tags else None,
        )
    except Exception as e:
        logger.warning("[local_memory_append] write failed: %s", e)
        return {"ok": False, "error": "write_failed", "message": str(e)}

    return {
        "ok": True,
        "message": "成功追加 1 条记忆至 l3_local.json",
        "tag": primary,
        "tags": norm_tags,
    }
