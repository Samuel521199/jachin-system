"""
Native **core:local_memory_append**：写入 Memory Nexus（User_Persona / Learned_Skills）。

与 core:local_memory_search / L1 注入同属 Memory Nexus（SQLite）底座；禁止模型幻觉写入 MEMORY.md。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def get_local_memory_append_timeout_sec() -> float:
    """供 ``agent_core`` 外层 ``wait_for`` 余量。"""
    return _local_memory_append_timeout_sec()


def _local_memory_append_timeout_sec() -> float:
    raw = (os.environ.get("JACHIN_LOCAL_MEMORY_APPEND_TIMEOUT_SEC") or "20").strip()
    try:
        v = float(raw)
    except ValueError:
        v = 20.0
    return max(1.0, min(v, 120.0))


def _run_local_memory_append_sync(*, content: str, tags: list[str] | None = None) -> dict[str, Any]:
    """在工作线程中执行 ``add_local_memory`` → ``commit_drawer``。"""
    text = (content or "").strip()
    if not text:
        return {"ok": False, "error": "empty_content", "message": "content 不能为空。"}

    raw = tags if isinstance(tags, list) else []
    norm_tags = [str(t).strip() for t in raw if str(t).strip()]
    primary = norm_tags[0] if norm_tags else "preference"

    try:
        from l3_node.local_memory import add_local_memory

        ok = add_local_memory(
            primary,
            text,
            source="core:local_memory_append",
            tags_list=norm_tags if norm_tags else None,
        )
    except Exception as e:
        msg = f"Memory Nexus 写入失败，底层真实报错: {e}"
        logger.warning("[local_memory_append] %s", msg, exc_info=True)
        return {
            "ok": False,
            "error": "nexus_commit_failed",
            "message": msg,
            "tag": primary,
            "tags": norm_tags,
        }

    if not ok:
        return {
            "ok": False,
            "error": "invalid_memory_params",
            "message": "Memory Nexus 未写入：content 或 tag 无效（空字符串）。",
            "tag": primary,
            "tags": norm_tags,
        }

    return {
        "ok": True,
        "message": "成功追加 1 条记忆至 Memory Nexus（User_Persona / Learned_Skills）",
        "tag": primary,
        "tags": norm_tags,
    }


async def async_run_local_memory_append(
    *,
    content: str,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """异步写入：``to_thread`` + ``wait_for``，避免 Memory Nexus 阻塞或假死挂死协程。"""
    _tmo = _local_memory_append_timeout_sec()
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_run_local_memory_append_sync, content=content, tags=tags),
            timeout=_tmo,
        )
    except asyncio.TimeoutError:
        logger.warning("[local_memory_append] 硬超时（%.1fs）", _tmo)
        return {
            "ok": False,
            "error": "timeout",
            "message": "[系统提示] 本地记忆写入超时，请稍后再试。",
        }
    except Exception as e:
        logger.warning("[local_memory_append] async 异常: %s", e, exc_info=True)
        return {"ok": False, "error": "async_failed", "message": str(e)}


def run_local_memory_append(*, content: str, tags: list[str] | None = None) -> dict[str, Any]:
    """
    同步入口：已在工作线程或非 asyncio 场景时使用。
    主循环路径请使用 ``async_run_local_memory_append``。
    """
    return _run_local_memory_append_sync(content=content, tags=tags)


def parse_core_local_memory_append_work_order_input(work_order_input: str) -> tuple[str, list[str] | None]:
    """解析 tool input → (content, tags)。"""
    inp = (work_order_input or "").strip()
    body = ""
    tags: list[str] | None = None
    if inp.startswith("{"):
        try:
            o = json.loads(inp)
            if isinstance(o, dict):
                body = str(o.get("content") or o.get("body") or o.get("text") or "").strip()
                t = o.get("tags")
                if isinstance(t, str):
                    tags = [x.strip() for x in t.split(",") if x.strip()]
                elif isinstance(t, list):
                    tags = [str(x).strip() for x in t if str(x).strip()]
        except json.JSONDecodeError:
            body = inp
    else:
        body = inp
    return body, tags
