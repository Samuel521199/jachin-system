"""
BI 项目知识库同步 — mcp:atom_bi_project_context

将 Lark Wiki 中配置的多维表/文档/表格等拉取到 docs/bi_daily_report/bi_project/。
实现位于 l3_node.mcp_tools.bi.tool_bi_project_context。
"""
from __future__ import annotations

from typing import Any


def atom_bi_project_context(config: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    """MCP 接口：与 L3 sync_bi_project_context 一致；额外键可经 kwargs 合并进 config。"""
    from l3_node.mcp_tools.bi.tool_bi_project_context import sync_bi_project_context

    cfg = dict(config or {})
    for k, v in kwargs.items():
        if v is not None:
            cfg[k] = v
    return sync_bi_project_context(config=cfg)
