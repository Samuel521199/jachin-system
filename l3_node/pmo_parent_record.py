"""
PMO 镜像表「父记录」字段语义 SSOT。

飞书多维表入库后 ``父记录`` 可能为 plain string、JSON 数组、空串/null，
或空链接 JSON（``text_arr: []``）。大需求（Epic）判定须将空链接视为无父记录。
"""
from __future__ import annotations

import json
from typing import Any


def parent_record_is_empty_link(pr: Any) -> bool:
    """飞书空父链接：null / '' / JSON 对象且 text_arr 为空。"""
    if pr is None:
        return True
    if isinstance(pr, str):
        s = pr.strip()
        if not s:
            return True
        if s.startswith("{"):
            try:
                obj = json.loads(s)
            except json.JSONDecodeError:
                return False
            if isinstance(obj, dict):
                text_arr = obj.get("text_arr")
                if text_arr == []:
                    return True
                if text_arr is None and not str(obj.get("text") or "").strip():
                    return True
        return False
    if isinstance(pr, dict):
        text_arr = pr.get("text_arr")
        if text_arr == []:
            return True
        if text_arr is None and not str(pr.get("text") or "").strip():
            return True
        return False
    if isinstance(pr, list) and pr and isinstance(pr[0], dict):
        first = pr[0]
        text_arr = first.get("text_arr")
        if text_arr == []:
            return True
        if not str(first.get("text") or "").strip() and text_arr is None:
            return True
    return False


def parent_text_from_fields(fields: dict[str, Any]) -> str | None:
    """提取父记录展示文本；空链接返回 None（与 C-2 大需求语义对齐）。"""
    pr = fields.get("父记录")
    if parent_record_is_empty_link(pr):
        return None
    if isinstance(pr, str):
        s = pr.strip()
        return s or None
    if isinstance(pr, list) and pr and isinstance(pr[0], dict):
        s = str(pr[0].get("text") or "").strip()
        return s or None
    if isinstance(pr, dict):
        s = str(pr.get("text") or "").strip()
        return s or None
    return None


def sql_parent_epic_null_clause(fields_expr: str = "fields") -> str:
    """生成 C-2 大需求 WHERE 子句：父记录视为「无父」的条件（含空链接 JSON 字符串）。"""
    col = f"json_extract({fields_expr}, '$.\"父记录\"')"
    col0 = f"json_extract({fields_expr}, '$.\"父记录\"[0].text')"
    empty_link = (
        f"trim({col}) GLOB '*\"text_arr\":[]*' "
        f"OR trim({col}) GLOB '*\"text_arr\": []*'"
    )
    return (
        f"({col} IS NULL "
        f"OR {col} = '' "
        f"OR {col0} IS NULL "
        f"OR {empty_link})"
    )
