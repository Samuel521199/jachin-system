"""
PMO 人员任务矩阵 · 可读输出（Observation / 探针）。

- 人员标题用【姓名】，不用 Markdown ``####``
- 任务明细用 GFM 表（避免 CJK 与等宽空格错位）
"""
from __future__ import annotations

import re
from typing import Any

# 本周任务主表（Sprint 在区块标题中统一说明时可省略该列）
_TASK_HEADERS: tuple[str, ...] = (
    "序",
    "任务",
    "编号",
    "P",
    "Start",
    "Review",
    "Accept",
    "Expected",
    "Actual",
    "进度/状态",
)

_UNASSIGNED_HEADERS: tuple[str, ...] = (
    "序",
    "任务/占位",
    "编号",
    "部门",
    "进度/状态",
)


def _dash(v: Any) -> str:
    if v is None or v == "" or v == "null":
        return "—"
    s = str(v).strip()
    return s if s else "—"


def _escape_gfm_cell(v: Any) -> str:
    s = _dash(v) if v is not None else "—"
    s = str(s).replace("|", "\\|").replace("\n", " ")
    return s.strip() or "—"


def _date_short(task: dict[str, Any], iso_key: str) -> str:
    iso = task.get(iso_key)
    if not iso:
        return "—"
    s = str(iso).strip()[:10]
    if len(s) >= 10 and s[4] == "-":
        return s[5:]  # MM-DD
    return s


def _sprint_short(sprint: str | None) -> str:
    s = str(sprint or "").strip()
    m = re.match(r"(\d{4})/(\d{2})/(\d{2})-Sprint", s)
    if m:
        return f"{m.group(2)}/{m.group(3)}"
    return s or "—"


def _progress_cell(task: dict[str, Any]) -> str:
    p = task.get("progress")
    st = task.get("status") or task.get("status_text")
    parts: list[str] = []
    if p not in (None, "", "null"):
        parts.append(str(p).strip())
    if st not in (None, "", "null") and str(st).strip() not in parts:
        parts.append(str(st).strip())
    return " / ".join(parts) if parts else "—"


def _gfm_table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    if not rows:
        return "（无任务）"
    sep = "| " + " | ".join("---" for _ in headers) + " |"
    lines = [
        "| " + " | ".join(headers) + " |",
        sep,
    ]
    for row in rows:
        cells = row[: len(headers)]
        if len(cells) < len(headers):
            cells = (*cells, *("—",) * (len(headers) - len(cells)))
        lines.append("| " + " | ".join(_escape_gfm_cell(c) for c in cells) + " |")
    return "\n".join(lines)


def _task_row(i: int, t: dict[str, Any]) -> tuple[str, ...]:
    return (
        str(i),
        str(t.get("task") or "—").strip() or "—",
        _dash(t.get("task_no")),
        _dash(t.get("priority")),
        _date_short(t, "start_date_iso"),
        _date_short(t, "review_date_iso"),
        _date_short(t, "acceptance_date_iso"),
        _date_short(t, "expected_delivery_date_iso"),
        _date_short(t, "actual_delivery_date_iso"),
        _progress_cell(t),
    )


def _unassigned_row(i: int, t: dict[str, Any]) -> tuple[str, ...]:
    task = str(t.get("task") or "").strip() or "—"
    return (
        str(i),
        task,
        _dash(t.get("task_no")),
        _dash(t.get("department")),
        _progress_cell(t),
    )


def format_task_detail_table(tasks: list[dict[str, Any]], *, numbered: bool = True) -> str:
    """GFM 任务表（案例 §6.3 列语义）。"""
    if not tasks:
        return "（无任务）"
    rows: list[tuple[str, ...]] = []
    for i, t in enumerate(tasks, start=1):
        row = _task_row(i, t)
        if not numbered:
            row = row[1:]
        rows.append(row)
    headers = _TASK_HEADERS if numbered else _TASK_HEADERS[1:]
    return _gfm_table(headers, rows)


def format_personnel_report_text(payload: dict[str, Any]) -> str:
    """将 ``run_personnel_report*`` 结果转为可读 Markdown 正文（无 ``####`` 标题）。"""
    parts: list[str] = []
    summ = payload.get("summary") or {}
    cs = payload.get("current_sprint") or "—"
    cs_date = payload.get("current_sprint_date") or "—"
    n_person = summ.get("person_count", len(payload.get("by_person") or {}))
    n_week = summ.get("current_week_task_count", "—")
    n_unassigned = summ.get("unassigned_count", len(payload.get("unassigned_tasks") or []))

    parts.append("**PMO 人员任务矩阵**")
    parts.append(f"- **本周 Sprint**：`{cs}`（`{cs_date}`）")
    parts.append(f"- **执行人**：{n_person} 人 · **本周任务**：{n_week} 条 · **无执行人占位**：{n_unassigned} 条")

    by_person = payload.get("by_person") or {}
    if not by_person:
        tasks = payload.get("personnel_tasks") or []
        if tasks:
            parts.append(f"\n**人员任务明细**（{len(tasks)} 条）\n")
            parts.append(format_task_detail_table(tasks))
        return "\n".join(parts)

    # 人员索引（便于扫读）
    index_bits: list[str] = []
    for person in sorted(by_person.keys(), key=lambda x: (x == "", x.lower())):
        tasks = by_person[person]
        current = [t for t in tasks if t.get("is_current_week") or t.get("sprint") == cs]
        if current:
            label = person or "(无)"
            index_bits.append(f"{label}({len(current)})")
    if index_bits:
        parts.append(f"- **人员索引**：{' · '.join(index_bits)}")

    parts.append(f"\n---\n\n**本周人员任务**（{len(by_person)} 人）\n")

    for person in sorted(by_person.keys(), key=lambda x: (x == "", x.lower())):
        tasks = by_person[person]
        current = [t for t in tasks if t.get("is_current_week") or t.get("sprint") == cs]
        dept = _dash(current[0].get("department")) if current else "—"
        label = person or "(无)"
        parts.append(f"**【{label}】** {len(current)} 条 · 部门：{dept}")
        if not current:
            parts.append("\n_（本周无任务）_\n")
            continue
        parts.append("")
        parts.append(format_task_detail_table(current))
        parts.append("")

    unassigned = payload.get("unassigned_tasks") or []
    if unassigned:
        show = unassigned[:12]
        parts.append("---\n")
        parts.append(f"**无明确执行人**（{len(unassigned)} 条 · Epic/部门占位，非个人任务）\n")
        rows = [_unassigned_row(i, t) for i, t in enumerate(show, start=1)]
        parts.append(_gfm_table(_UNASSIGNED_HEADERS, rows))
        if len(unassigned) > len(show):
            parts.append(f"\n_另有 {len(unassigned) - len(show)} 条见 JSON `unassigned_tasks`_")

    cross = payload.get("cross_week_tasks") or []
    if cross:
        show_c = cross[:8]
        parts.append("\n---\n")
        parts.append(f"**跨周补充**（近三周 · 非 `{cs}` · {len(cross)} 条）\n")
        parts.append(format_task_detail_table(show_c))
        if len(cross) > len(show_c):
            parts.append(f"\n_另有 {len(cross) - len(show_c)} 条见 JSON `cross_week_tasks`_")

    return "\n".join(parts)
