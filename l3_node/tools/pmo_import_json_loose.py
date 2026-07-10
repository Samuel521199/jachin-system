"""
PMO staging JSON 宽容解析：LLM 输出的 JSON 常有漏逗号、未闭合等问题。
优先 strict json.loads → json_repair → 逐对象 brace 提取（能救多少救多少）。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

logger = logging.getLogger(__name__)

try:
    import json_repair

    _HAS_JSON_REPAIR = True
except ImportError:
    json_repair = None  # type: ignore[assignment,misc]
    _HAS_JSON_REPAIR = False


def loads_json_loose(text: str) -> tuple[Any | None, str | None]:
    """strict → json_repair；返回 (obj, mode) mode 为 strict|repaired|None。"""
    raw = (text or "").strip()
    if not raw:
        return None, None
    try:
        return json.loads(raw), "strict"
    except json.JSONDecodeError:
        pass
    if _HAS_JSON_REPAIR and json_repair is not None:
        try:
            repaired = json_repair.loads(raw)
            return repaired, "repaired"
        except Exception as e:
            logger.debug("[pmo_import_json_loose] json_repair failed: %s", e)
    return None, None


def _loads_object(chunk: str) -> dict[str, Any] | None:
    obj, _ = loads_json_loose(chunk)
    if isinstance(obj, dict):
        return obj
    try:
        parsed = json.loads(chunk)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    return None


def extract_balanced_json_objects(text: str) -> list[dict[str, Any]]:
    """从文本中提取所有平衡 {...} 对象（忽略字符串内的括号）。"""
    out: list[dict[str, Any]] = []
    if not text:
        return out
    i = 0
    n = len(text)
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        depth = 0
        in_string = False
        escape = False
        start = i
        matched = False
        for j in range(i, n):
            c = text[j]
            if escape:
                escape = False
                continue
            if c == "\\" and in_string:
                escape = True
                continue
            if c == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    chunk = text[start : j + 1]
                    obj = _loads_object(chunk)
                    if obj is not None:
                        out.append(obj)
                    i = j + 1
                    matched = True
                    break
        if not matched:
            i += 1
    return out


def _extract_balanced_array_inner(text: str, start: int) -> str:
    """从 start（应指向 '[' 后第一个字符或 '[' 本身）提取平衡 [...] 内部文本。"""
    n = len(text)
    i = start
    if i < n and text[i] == "[":
        i += 1
    depth = 0
    in_string = False
    escape = False
    inner_start = i
    for j in range(i, n):
        c = text[j]
        if escape:
            escape = False
            continue
        if c == "\\" and in_string:
            escape = True
            continue
        if c == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == "[":
            depth += 1
        elif c == "]":
            if depth == 0:
                return text[inner_start:j]
            depth -= 1
    return text[inner_start:]


def _extract_meta_string(text: str, key: str) -> str:
    m = re.search(rf'"{re.escape(key)}"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
    if not m:
        return ""
    try:
        return json.loads(f'"{m.group(1)}"')
    except json.JSONDecodeError:
        return m.group(1).replace('\\"', '"').replace("\\\\", "\\")


def salvage_bundle_tables(
    text: str,
    *,
    writable_tables: frozenset[str],
    coerce_records: Callable[[Any], list[dict[str, Any]]],
) -> tuple[str, str, list[tuple[str, str, list[dict[str, Any]]]], list[str]]:
    """
    bundle JSON 整体解析失败时，按表名扫描 records 数组并逐对象拯救。
    返回 (source_file, source_view, batches, warnings)。
    """
    warnings: list[str] = []
    src_file = _extract_meta_string(text, "source_file")
    src_view = _extract_meta_string(text, "source_view")
    batches: list[tuple[str, str, list[dict[str, Any]]]] = []

    for table in writable_tables:
        pat = rf'"{re.escape(table)}"\s*:\s*\['
        m = re.search(pat, text)
        if not m:
            continue
        inner = _extract_balanced_array_inner(text, m.end() - 1)
        objs = extract_balanced_json_objects(inner)
        if not objs:
            warnings.append(f"salvage:{table}: 未提取到有效 record 对象")
            continue
        batches.append((table, "upsert", objs))
        warnings.append(f"salvage:{table}: 拯救 {len(objs)} 条 record")

    if not batches:
        # 兜底：扫描全文所有 {...}，按字段启发式归类（仅当含 id）
        all_objs = extract_balanced_json_objects(text)
        by_table: dict[str, list[dict[str, Any]]] = {}
        for obj in all_objs:
            if "table" in obj and "records" in obj:
                t = str(obj.get("table") or "").strip()
                if t in writable_tables:
                    by_table.setdefault(t, []).extend(coerce_records(obj.get("records")))
                continue
            if not obj.get("id"):
                continue
            if "requirement_name" in obj:
                t = str(obj.get("_table") or "pmo_dev_requirements")
                if t in writable_tables:
                    by_table.setdefault(t, []).append(obj)
            elif "task_name" in obj or "person_id" in obj:
                if "pmo_personnel_task_progress" in writable_tables:
                    by_table.setdefault("pmo_personnel_task_progress", []).append(obj)
            elif "name" in obj and "dept" in obj:
                if "pmo_people" in writable_tables:
                    by_table.setdefault("pmo_people", []).append(obj)
        for table, recs in by_table.items():
            if recs:
                batches.append((table, "upsert", recs))
                warnings.append(f"salvage_heuristic:{table}: {len(recs)} 条")

    return src_file, src_view, batches, warnings
