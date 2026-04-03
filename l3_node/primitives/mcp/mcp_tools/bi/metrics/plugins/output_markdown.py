"""
BI 指标 — Markdown 输出器
"""
from __future__ import annotations

from typing import Any

from l3_node.primitives.mcp.mcp_tools.bi.metrics.plugins.base import Outputter
from l3_node.primitives.mcp.mcp_tools.bi.metrics.plugins.output_console import _safe_float, _fmt_pct, _fmt_value


class MarkdownOutputter(Outputter):
    """Markdown 输出，适合飞书/文档"""

    def format(self, metrics: dict[str, Any], config: dict[str, Any]) -> str:
        if metrics.get("_error"):
            return f"**错误**: {metrics['_error']}"

        lines = ["## BI 核心指标"]
        date_str = metrics.get("date", "")
        if date_str:
            lines.append(f"**日期**: {date_str}\n")

        layout = config.get("layout", [])
        for row in layout:
            parts = []
            for item in row:
                if isinstance(item, str):
                    item = {"key": item, "label": item}
                key = item.get("key")
                label = item.get("label", key)
                fmt = item.get("format", ".0f")
                compare_key = item.get("compare")
                val = metrics.get(key)
                cmp_val = metrics.get(compare_key) if compare_key else None
                s = _fmt_value(val, cmp_val, fmt)
                parts.append(f"- **{label}**: {s}")
            lines.append("\n".join(parts))

        return "\n".join(lines)
