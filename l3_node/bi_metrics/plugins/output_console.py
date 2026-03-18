"""
BI 指标 — 控制台输出器
"""
from __future__ import annotations

from typing import Any

from l3_node.bi_metrics.plugins.base import Outputter


def _fmt_pct(v: float) -> str:
    if v >= 0:
        return f"+{v:.2f}%"
    return f"{v:.2f}%"


def _safe_float(v: Any) -> float:
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def _fmt_value(val: Any, compare_pct: float | None, fmt: str = ".0f") -> str:
    v = _safe_float(val)
    s = f"{v:{fmt}}"
    if compare_pct is not None:
        s += f" ——{_fmt_pct(compare_pct)}"
    return s


class ConsoleOutputter(Outputter):
    """控制台输出，适合终端查看"""

    def format(self, metrics: dict[str, Any], config: dict[str, Any]) -> str:
        if metrics.get("_error"):
            return f"错误: {metrics['_error']}"

        lines = []
        date_str = metrics.get("date", "")
        if date_str:
            lines.append(f"【{date_str}】")

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
                parts.append(f"{label}：{s}")
            lines.append("   ".join(parts))

        return "\n".join(lines)
