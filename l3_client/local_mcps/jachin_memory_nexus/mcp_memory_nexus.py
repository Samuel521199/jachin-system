#!/usr/bin/env python3
"""
Jachin 记忆宫殿 MCP — stdio / FastMCP

持久化：``~/.jachin/palace_db/memory_nexus.sqlite3``（SQLite + FastEmbed 向量）。

运行：
  python -m l3_client.local_mcps.jachin_memory_nexus.mcp_memory_nexus
  或
  python l3_client/local_mcps/jachin_memory_nexus/mcp_memory_nexus.py

依赖：``pip install -r requirements.txt``（mcp、numpy、fastembed）。
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

# 以脚本路径直接运行也能 import l3_client
_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("mcp_memory_nexus")

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    logger.error("请安装 mcp: pip install mcp")
    sys.exit(1)

from l3_client.local_mcps.jachin_memory_nexus.memory_backend import (
    commit_drawer,
    deep_search,
    recall_room,
)

mcp = FastMCP("jachin-memory-nexus")


@mcp.tool(name="tool_commit_memory")
def tool_commit_memory(text: str, wing: str, room: str) -> str:
    """
    将重要原文逐字归档到记忆宫殿指定 Wing/Room（Verbatim）。

    :param text: 完整正文（如巡检报告、摘要）
    :param wing: 翼区标识，用于物理隔离（metadata）
    :param room: 房间标识，与 wing 组合过滤
    """
    try:
        drawer_id = commit_drawer(text=text, wing=wing, room=room, extra_meta=None)
        return json.dumps(
            {"ok": True, "drawer_id": drawer_id},
            ensure_ascii=False,
        )
    except Exception as e:
        logger.exception("tool_commit_memory failed")
        return json.dumps({"ok": False, "error": repr(e)}, ensure_ascii=False)


@mcp.tool(name="tool_recall_room")
def tool_recall_room(wing: str, room: str, limit: int = 5) -> str:
    """
    L2 On-Demand：按 wing + room 拉取近期抽屉原文（按时间倒序）。

    :param wing: 翼区
    :param room: 房间
    :param limit: 最大条数（默认 5）
    """
    try:
        out = recall_room(wing=wing, room=room, limit=limit)
        return json.dumps(out, ensure_ascii=False)
    except Exception as e:
        logger.exception("tool_recall_room failed")
        return json.dumps({"ok": False, "error": repr(e), "drawers": []}, ensure_ascii=False)


@mcp.tool(name="tool_deep_search")
def tool_deep_search(query: str, wing: str | None = None, limit: int = 5) -> str:
    """
    L3 Deep Search：向量语义检索；可选限定 wing。

    :param query: 自然语言查询
    :param wing: 可选，仅检索该翼区
    :param limit: 返回条数上限
    """
    try:
        out = deep_search(query=query, wing=wing, limit=limit)
        return json.dumps(out, ensure_ascii=False)
    except Exception as e:
        logger.exception("tool_deep_search failed")
        return json.dumps({"ok": False, "error": repr(e), "matches": []}, ensure_ascii=False)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
