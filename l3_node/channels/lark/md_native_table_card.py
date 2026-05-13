"""
将 Markdown 中的 GFM 表格解析为飞书 **卡片 JSON 2.0** 的 ``tag: table`` 原生组件。

与单块 ``lark_md`` 相比，客户端渲染为带表头样式、分页的表格，更接近「综合冒烟」战报的观感。
非表格段落保留为 ``tag: markdown``，顺序与原文一致。

参见：https://open.feishu.cn/document/feishu-cards/card-json-v2-components/content-components/table
"""
from __future__ import annotations

import re
from typing import Any

_TABLE_LINE = re.compile(r"^\s*\|.+\|\s*$")
_TABLE_SEP = re.compile(r"^\s*\|[\s\-:|]+\|\s*$")


def _split_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _is_table_start(lines: list[str], i: int) -> bool:
    if i + 1 >= len(lines):
        return False
    return bool(_TABLE_LINE.match(lines[i]) and _TABLE_SEP.match(lines[i + 1]))


def _consume_table(lines: list[str], start: int) -> tuple[list[list[str]], int]:
    """首行为表头；跳过 GFM 分隔行 ``| --- |``；其后为数据行。"""
    rows: list[list[str]] = []
    i = start
    rows.append(_split_row(lines[i]))
    i += 1
    if i >= len(lines) or not _TABLE_SEP.match(lines[i]):
        return [], start
    i += 1
    while i < len(lines) and _TABLE_LINE.match(lines[i]):
        if _TABLE_SEP.match(lines[i]):
            i += 1
            continue
        rows.append(_split_row(lines[i]))
        i += 1
    return rows, i


def segment_markdown_tables(markdown_content: str) -> list[tuple[str, Any]]:
    """
    将正文拆成有序段落：
    - ``("markdown", str)`` — 非表格文本；
    - ``("table", list[list[str]])`` — 一张表（首行为表头）。
    """
    text = (markdown_content or "").strip()
    if not text:
        return []
    lines = text.splitlines()
    segments: list[tuple[str, Any]] = []
    buf: list[str] = []
    i = 0
    while i < len(lines):
        if _is_table_start(lines, i):
            if buf:
                block = "\n".join(buf).strip()
                if block:
                    segments.append(("markdown", block))
                buf = []
            table, j = _consume_table(lines, i)
            if len(table) >= 2:
                segments.append(("table", table))
                i = j
            else:
                buf.append(lines[i])
                i += 1
        else:
            buf.append(lines[i])
            i += 1
    if buf:
        block = "\n".join(buf).strip()
        if block:
            segments.append(("markdown", block))
    return segments


def _col_key(i: int, header: str) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "_", (header or "").strip())[:24]
    s = re.sub(r"_+", "_", s).strip("_") or f"col_{i}"
    if s[0].isdigit():
        s = f"c_{s}"
    return s[:28]


def _unique_col_keys(headers: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for i, h in enumerate(headers):
        base = _col_key(i, h)[:24]
        candidate = base
        n = 0
        while candidate in seen:
            n += 1
            candidate = f"{base}_{n}"[:28]
        seen.add(candidate)
        out.append(candidate)
    return out


def _table_element(
    rows_matrix: list[list[str]], *, element_id: str, page_size: int = 10
) -> dict[str, Any]:
    if not rows_matrix or len(rows_matrix) < 2:
        raise ValueError("table needs header + ≥1 data row")
    header_cells = rows_matrix[0]
    data_rows = rows_matrix[1:]
    keys = _unique_col_keys(header_cells)
    columns: list[dict[str, Any]] = []
    for ci, display in enumerate(header_cells):
        key = keys[ci]
        columns.append(
            {
                "name": key,
                "display_name": display or f"列{ci + 1}",
                "width": "auto",
                "data_type": "lark_md",
                "vertical_align": "top",
                "horizontal_align": "left",
            }
        )
    feishu_rows: list[dict[str, Any]] = []
    for dr in data_rows:
        row_obj: dict[str, Any] = {}
        for ci, col in enumerate(columns):
            cell = dr[ci] if ci < len(dr) else ""
            row_obj[col["name"]] = str(cell or "").strip() or "—"
        feishu_rows.append(row_obj)
    return {
        "tag": "table",
        "element_id": element_id[:20] if len(element_id) > 20 else element_id,
        "page_size": max(1, min(int(page_size), 10)),
        "row_height": "middle",
        "header_style": {
            "text_align": "left",
            "bold": True,
            "lines": 1,
        },
        "columns": columns,
        "rows": feishu_rows,
    }


def build_schema_v2_card_from_markdown(
    markdown_content: str,
    title: str | None = None,
    *,
    max_tables: int = 5,
    table_page_size: int = 10,
) -> dict[str, Any] | None:
    """
    若正文含至少一张 GFM 表，则返回完整 ``schema: 2.0`` 卡片 dict；否则返回 ``None``（调用方降级 lark_md）。
    """
    segs = segment_markdown_tables(markdown_content)
    tables = [s for s in segs if s[0] == "table"]
    if not tables:
        return None

    elements: list[dict[str, Any]] = []
    table_idx = 0
    for kind, payload in segs:
        if kind == "markdown":
            elements.append({"tag": "markdown", "content": str(payload)})
        elif kind == "table":
            if table_idx >= max_tables:
                elements.append(
                    {
                        "tag": "markdown",
                        "content": "（下文表格数量已达上限，余下见 Observation / Wiki）",
                    }
                )
                break
            matrix: list[list[str]] = payload
            if len(matrix) < 2:
                continue
            try:
                elements.append(
                    _table_element(
                        matrix,
                        element_id=f"pmo_tbl_{table_idx}",
                        page_size=table_page_size,
                    )
                )
            except ValueError:
                continue
            table_idx += 1
    if not any(e.get("tag") == "table" for e in elements):
        return None

    card: dict[str, Any] = {
        "schema": "2.0",
        "config": {"wide_screen_mode": True, "enable_forward": True},
        "body": {"elements": elements},
    }
    t = (title or "").strip()[:100]
    if t:
        card["header"] = {
            "title": {"tag": "plain_text", "content": t},
            "template": "blue",
        }
    return card
