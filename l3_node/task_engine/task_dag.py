"""
轻量 TaskDAG：供 ~/.jachin/workspace/task_dags/active.json 驱动 prompt 进度摘要。
与 docs/AGI_OPTIMIZATION_ROADMAP.md §3.2 对齐；完整调度器后续再接。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def task_dags_dir() -> Path:
    root = Path(os.environ.get("JACHIN_HOME") or Path.home() / ".jachin").expanduser()
    d = root / "workspace" / "task_dags"
    d.mkdir(parents=True, exist_ok=True)
    return d


def active_task_dag_path() -> Path:
    return task_dags_dir() / "active.json"


def format_active_task_dag_prompt_suffix(*, max_nodes: int = 24, max_chars: int = 1600) -> str:
    """
    若存在 active.json 则返回一段低优先级 prompt 后缀；格式示例：
    {"title":"...","nodes":[{"node_id":"1","title":"...","status":"pending"}, ...]}
    """
    p = active_task_dag_path()
    if not p.is_file():
        return ""
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    title = str(data.get("title") or data.get("dag_title") or "TaskDAG").strip() or "TaskDAG"
    nodes = data.get("nodes")
    if not isinstance(nodes, list):
        return f"【TaskDAG·{title}】active.json 中 nodes 非列表，请检查格式。\n"
    lines: list[str] = [f"【TaskDAG·{title}】当前结构化任务图（`workspace/task_dags/active.json`）："]
    shown = 0
    for n in nodes:
        if shown >= max(1, min(64, max_nodes)):
            break
        if not isinstance(n, dict):
            continue
        nid = str(n.get("node_id") or n.get("id") or "")[:32]
        st = str(n.get("status") or "pending")[:16]
        tl = str(n.get("title") or n.get("description") or "")[:120]
        lines.append(f"- [{st}] {nid}: {tl}".strip())
        shown += 1
    if len(nodes) > shown:
        lines.append(f"- … 共 {len(nodes)} 个节点，仅展示前 {shown} 个。")
    lines.append(
        "说明：由工作流或模型维护 JSON；完成后可删除或清空 active.json 以关闭本段注入。"
    )
    out = "\n".join(lines)
    if len(out) > max_chars:
        return out[: max_chars - 3] + "…"
    return out


def load_task_dag_dict() -> dict[str, Any] | None:
    p = active_task_dag_path()
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def save_active_task_dag_dict(data: dict[str, Any]) -> bool:
    """
    将 TaskDAG 写回 active.json（原子替换）。供工具链/工作流更新进度，无需完整调度器。
    """
    if not isinstance(data, dict):
        return False
    p = active_task_dag_path()
    try:
        task_dags_dir()
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
        return True
    except OSError:
        return False
