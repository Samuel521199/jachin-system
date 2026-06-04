"""
Worker B：各视图字段启动对齐 + 按 source_view 查询前提示（SSOT）。

FanOut 启动 Worker B 时从 pmo_views_meta 读取 columns_json，生成【字段对齐·B-x】块注入任务体；
core:db_query 对 B 相关视图在 Observation hints 中附带同视图对齐摘要，减少模型「猜字段」轮次。
"""
from __future__ import annotations

import json
import re
from typing import Any

# (step_id, view_id, schema_kind)
WORKER_B_STEPS: tuple[tuple[str, str, str], ...] = (
    ("B-S1", "vewCz1FFJi", "dev_personnel"),
    ("B-4", "vewCz1FFJi", "dev_personnel"),
    ("B-SUP", "vewpI8lyYw", "dev"),
)

WORKER_B_VIEW_IDS: frozenset[str] = frozenset(v for _, v, _ in WORKER_B_STEPS)

_REQUIRED_BY_KIND: dict[str, tuple[str, ...]] = {
    "product": ("需求简述", "优先级", "Sprint", "责任人", "需求状态", "开发状态"),
    "dev": (
        "Requirement",
        "priority",
        "Sprint",
        "Person in charge/Participant",
        "状态",
        "Progress",
    ),
    "dev_personnel": (
        "Requirement",
        "priority",
        "Sprint",
        "Person in charge/Participant",
        "状态",
        "Version Goal",
        "Progress",
        "任务编号",
    ),
}

_EXTRACT_CHEATSHEET: dict[str, str] = {
    "product": (
        "任务=需求简述；优先级=优先级；负责人=责任人→[0].text；"
        "需求状态/开发状态=plain string（禁止 [0].text）"
    ),
    "dev": (
        "任务=Requirement；优先级=priority；Sprint=纯字符串；"
        "B-SUP：trim(Person) plain；状态=plain string；Sprint IN recent_sprints；禁止 json_each/C-2 Epic WHERE"
    ),
    "dev_personnel": (
        "B-S1：近三周 Sprint（replace 斜杠）；B-4：Person 常为 plain string（typeof+NOT GLOB），"
        "数组行用 UNION+json_each；须任务编号+Sprint IN；department=父记录 COALESCE；"
        "状态=plain string（禁止状态 [0].text）；禁止单独 json_each 全表"
    ),
}

_SOURCE_VIEW_RE = re.compile(
    r"source_view\s*=\s*['\"]([A-Za-z0-9]+)['\"]",
    re.IGNORECASE,
)

_align_cache: dict[str, dict[str, Any]] | None = None


def _parse_columns_json(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw if x]
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return []
        try:
            parsed = json.loads(s)
        except json.JSONDecodeError:
            return [s]
        if isinstance(parsed, list):
            return [str(x) for x in parsed if x]
    return []


def _load_views_meta() -> dict[str, dict[str, Any]]:
    """从 PMO SQLite 读取 Worker B 涉及视图的 meta；失败则返回空 dict。"""
    try:
        from l3_node.tools.pmo_db_tools import _connect, ensure_pmo_schema

        ids = sorted(WORKER_B_VIEW_IDS)
        placeholders = ",".join("?" for _ in ids)
        conn = _connect()
        try:
            ensure_pmo_schema(conn)
            cur = conn.execute(
                f"SELECT view_id, view_name, record_count, columns_json "
                f"FROM pmo_views_meta WHERE view_id IN ({placeholders})",
                ids,
            )
            out: dict[str, dict[str, Any]] = {}
            for row in cur.fetchall():
                d = dict(row)
                vid = str(d.get("view_id") or "")
                if vid:
                    out[vid] = d
            return out
        finally:
            conn.close()
    except Exception:
        return {}


def _step_rows_for_view(view_id: str) -> list[tuple[str, str]]:
    return [(step, kind) for step, vid, kind in WORKER_B_STEPS if vid == view_id]


def _build_view_align_entry(
    view_id: str,
    meta_row: dict[str, Any] | None,
) -> dict[str, Any]:
    steps = _step_rows_for_view(view_id)
    kinds = {k for _, k in steps}
    kind = "dev_personnel" if "dev_personnel" in kinds else (
        "product" if kinds == {"product"} else "dev"
    )
    cols = _parse_columns_json((meta_row or {}).get("columns_json"))
    required = _REQUIRED_BY_KIND.get(kind, ())
    missing = [k for k in required if k not in cols]
    present_required = [k for k in required if k in cols]
    return {
        "view_id": view_id,
        "view_name": (meta_row or {}).get("view_name") or "",
        "record_count": (meta_row or {}).get("record_count"),
        "columns": cols,
        "steps": [s for s, _ in steps],
        "kind": kind,
        "required": required,
        "present_required": present_required,
        "missing": missing,
        "cheatsheet": _EXTRACT_CHEATSHEET.get(kind, ""),
    }


def load_worker_b_field_alignment(*, force: bool = False) -> dict[str, dict[str, Any]]:
    """按 view_id 缓存字段对齐结构（进程内单例）。"""
    global _align_cache
    if _align_cache is not None and not force:
        return _align_cache
    meta = _load_views_meta()
    cache: dict[str, dict[str, Any]] = {}
    for view_id in WORKER_B_VIEW_IDS:
        cache[view_id] = _build_view_align_entry(view_id, meta.get(view_id))
    _align_cache = cache
    return cache


def build_worker_b_field_alignment_block(*, max_cols_list: int = 28) -> str:
    """
    注入 Worker B 任务体：每步 B-x 对应视图的 columns_json 核对结果 + 提取口诀。
    """
    cache = load_worker_b_field_alignment()
    lines = [
        "**【字段对齐 · 启动时已核对 pmo_views_meta】**",
        "每执行 **B-x** 前：只读下面对应小节，确认「本步必用键」均在镜像列中，"
        "再 **逐字复制** 任务体该步 SQL；**禁止** 为对齐字段再查 columns_json / PRAGMA / 全库地图。\n",
    ]
    seen_steps: set[str] = set()
    for step, view_id, kind in WORKER_B_STEPS:
        if step in seen_steps:
            continue
        entry = cache.get(view_id) or _build_view_align_entry(view_id, None)
        cols = entry.get("columns") or []
        cols_preview = ", ".join(cols[:max_cols_list])
        if len(cols) > max_cols_list:
            cols_preview += f" …（共 {len(cols)} 列）"
        elif not cols_preview:
            cols_preview = "（未读到 columns_json，仍须复制任务体 SQL，勿猜字段名）"
        missing = entry.get("missing") or []
        present = entry.get("present_required") or []
        vn = entry.get("view_name") or view_id
        rc = entry.get("record_count")
        rc_s = f" · {rc} 条" if rc is not None else ""
        lines.append(f"**字段对齐 · {step} · `{view_id}`**（{vn}{rc_s} · {kind}）")
        lines.append(f"- 镜像列：{cols_preview}")
        lines.append(f"- 本步必用键：{', '.join(present) or ', '.join(_REQUIRED_BY_KIND.get(kind, ()))}")
        if missing:
            lines.append(f"- ⚠️ meta 缺列（仍按任务体 SQL 试查）：{', '.join(missing)}")
        lines.append(f"- 提取口诀：{entry.get('cheatsheet') or _EXTRACT_CHEATSHEET.get(kind, '')}")
        lines.append(f"- 下一步：执行下方 **{step}** SQL 块（勿混用其它视图字段名）\n")
        seen_steps.add(step)
    return "\n".join(lines)


def augment_worker_b_task(task_body: str) -> str:
    """在 WORKER_B_TASK 的 SQL 块之前插入动态字段对齐段。"""
    block = build_worker_b_field_alignment_block()
    marker = "**B-S1 · 近三周 Sprint 名（vewCz1FFJi"
    if marker in task_body:
        return task_body.replace(marker, block + "\n" + marker, 1)
    return task_body + "\n\n" + block


def extract_source_view_from_sql(sql: str) -> str | None:
    m = _SOURCE_VIEW_RE.search(sql or "")
    return m.group(1) if m else None


def field_align_hint_for_sql(sql: str) -> str | None:
    """单次 db_query 前/后附加：本 SQL 目标视图的字段对齐一行摘要。"""
    vid = extract_source_view_from_sql(sql)
    if not vid or vid not in WORKER_B_VIEW_IDS:
        return None
    entry = load_worker_b_field_alignment().get(vid)
    if not entry:
        return None
    steps = "/".join(entry.get("steps") or [])
    present = ", ".join(entry.get("present_required") or [])[:120]
    missing = entry.get("missing") or []
    miss_s = f" ⚠️缺:{','.join(missing)}" if missing else ""
    return (
        f"📎 字段对齐·{vid}（步骤 {steps}）：必用键 [{present}]{miss_s}。"
        f"{entry.get('cheatsheet') or ''} "
        "请复制 WORKER_B_TASK 对应 B-x SQL，勿用其它视图字段名。"
    )
