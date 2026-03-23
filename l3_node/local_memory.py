"""
L3 本地记忆持久化 — 智能化升级 P0

L3 独立运行或 L2 不可用时，本地存储核心记忆，供 run_agent 检索注入。
与 L2 同步时 merge，断网可用。
设计: docs/JACHIN_VS_OPENCLAW_INTELLIGENCE_ANALYSIS.md
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_JACHIN_ROOT = Path.home() / ".jachin"
_MEMORY_DIR = _JACHIN_ROOT / "memory"
_LOCAL_DB = _MEMORY_DIR / "l3_local.json"
_MAX_ENTRIES = 200  # 最多保留条数


def _ensure_dir() -> None:
    _MEMORY_DIR.mkdir(parents=True, exist_ok=True)


def _load_raw() -> list[dict]:
    """加载本地记忆 JSON"""
    _ensure_dir()
    if not _LOCAL_DB.exists():
        return []
    try:
        data = json.loads(_LOCAL_DB.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.debug("[L3 LocalMemory] 加载失败: %s", e)
        return []


def _save_raw(entries: list[dict]) -> None:
    """保存本地记忆"""
    _ensure_dir()
    try:
        _LOCAL_DB.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("[L3 LocalMemory] 保存失败: %s", e)


def add_local_memory(tag: str, content: str, *, source: str = "agent") -> None:
    """
    写入一条 L3 本地核心记忆。
    tag: preference | user_habit | config_hint | fact 等
    """
    content = (content or "").strip()
    tag = (tag or "general").strip()
    if not content or not tag:
        return
    entries = _load_raw()
    entries.append({
        "tag": tag,
        "content": content,
        "source": source,
        "timestamp": time.time(),
    })
    # 按时间倒序，保留最近 MAX
    entries.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
    if len(entries) > _MAX_ENTRIES:
        entries = entries[:_MAX_ENTRIES]
    _save_raw(entries)
    logger.debug("[L3 LocalMemory] 已写入 tag=%s", tag)


def get_local_memory_for_prompt(limit: int = 15) -> str:
    """
    获取最近 N 条本地记忆，格式化为 System Prompt 片段。
    供 run_agent 在 L2 recall 不可用时注入。
    """
    entries = _load_raw()
    if not entries:
        return ""
    # P2：修正类记忆优先展示，再按时间倒序
    entries = sorted(
        entries,
        key=lambda e: (0 if str(e.get("tag", "")).lower() == "correction" else 1, -float(e.get("timestamp", 0) or 0)),
    )[:limit]
    lines = ["【本地记忆】"]
    for e in entries:
        tag = e.get("tag", "general")
        content = (e.get("content") or "")[:300]
        if content:
            lines.append(f"- [{tag}] {content}")
    return "\n".join(lines) + "\n" if lines else ""


def merge_from_l2(items: list[dict]) -> None:
    """
    从 L2 同步合并记忆。L2 检索结果写入本地，避免断网时丢失。
    items: [{"content": "...", "tag": "?"}, ...]
    """
    if not items:
        return
    entries = _load_raw()
    seen = {e.get("content", "")[:100] for e in entries}
    now = time.time()
    for it in items:
        c = (it.get("content") or "").strip()
        if not c or c[:100] in seen:
            continue
        seen.add(c[:100])
        entries.append({
            "tag": it.get("tag", "l2_sync"),
            "content": c,
            "source": "l2_sync",
            "timestamp": now,
        })
    entries.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
    if len(entries) > _MAX_ENTRIES:
        entries = entries[:_MAX_ENTRIES]
    _save_raw(entries)
    logger.debug("[L3 LocalMemory] 已合并 %d 条 L2 记忆", len(items))


# -----------------------------------------------------------------------------
# DAG Workflow 状态持久（供 core/workflow_engine 断点续传）
# -----------------------------------------------------------------------------

_WORKFLOW_STATES = _MEMORY_DIR / "workflow_states.json"


def save_workflow_state(workflow_id: str, state: dict) -> None:
    """保存 DAG Workflow 状态，供断点续传。"""
    _ensure_dir()
    try:
        all_states: dict = {}
        if _WORKFLOW_STATES.exists():
            all_states = json.loads(_WORKFLOW_STATES.read_text(encoding="utf-8"))
            if not isinstance(all_states, dict):
                all_states = {}
        all_states[workflow_id] = {**state, "workflow_id": workflow_id}
        _WORKFLOW_STATES.write_text(json.dumps(all_states, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.debug("[L3 LocalMemory] 已保存 workflow 状态: %s", workflow_id)
    except Exception as e:
        logger.warning("[L3 LocalMemory] 保存 workflow 状态失败: %s", e)


def load_workflow_state(workflow_id: str) -> dict | None:
    """加载 DAG Workflow 状态，不存在返回 None。"""
    if not _WORKFLOW_STATES.exists():
        return None
    try:
        all_states = json.loads(_WORKFLOW_STATES.read_text(encoding="utf-8"))
        if not isinstance(all_states, dict):
            return None
        return all_states.get(workflow_id)
    except Exception as e:
        logger.debug("[L3 LocalMemory] 加载 workflow 状态失败: %s", e)
        return None


def delete_workflow_state(workflow_id: str) -> bool:
    """删除指定 workflow 状态（完成后清理）。"""
    if not _WORKFLOW_STATES.exists():
        return True
    try:
        all_states = json.loads(_WORKFLOW_STATES.read_text(encoding="utf-8"))
        if isinstance(all_states, dict) and workflow_id in all_states:
            del all_states[workflow_id]
            _WORKFLOW_STATES.write_text(json.dumps(all_states, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception as e:
        logger.warning("[L3 LocalMemory] 删除 workflow 状态失败: %s", e)
        return False


def list_workflow_state_ids() -> list[str]:
    """列出已持久化的 workflow_id（供 Lark 分析指令选择目标）。"""
    if not _WORKFLOW_STATES.exists():
        return []
    try:
        all_states = json.loads(_WORKFLOW_STATES.read_text(encoding="utf-8"))
        if not isinstance(all_states, dict):
            return []
        return list(all_states.keys())
    except Exception as e:
        logger.debug("[L3 LocalMemory] 列举 workflow 失败: %s", e)
        return []


# -----------------------------------------------------------------------------
# HR 招聘：当前 DAG / 收网任务指针（供 Lark 停止、分析指令解析 workflow_id）
# -----------------------------------------------------------------------------

_HR_WF_POINTER = _MEMORY_DIR / "hr_recruitment_workflow_pointer.json"


def set_hr_recruitment_workflow_pointer(
    workflow_id: str,
    *,
    job_name: str = "",
    jd_config_path: str = "",
    resume_pending_dir: str = "",
) -> None:
    """记录当前 HR 招聘 DAG 或收网任务关联信息。"""
    _ensure_dir()
    try:
        data = {
            "workflow_id": (workflow_id or "").strip(),
            "job_name": (job_name or "").strip(),
            "jd_config_path": (jd_config_path or "").strip(),
            "resume_pending_dir": (resume_pending_dir or "").strip(),
            "updated_at": time.time(),
        }
        _HR_WF_POINTER.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.debug("[L3 LocalMemory] HR workflow 指针已更新: %s", data["workflow_id"])
    except Exception as e:
        logger.warning("[L3 LocalMemory] 写入 HR workflow 指针失败: %s", e)


def get_hr_recruitment_workflow_pointer() -> dict:
    """读取 HR 招聘 workflow 指针；不存在返回空 dict。"""
    if not _HR_WF_POINTER.exists():
        return {}
    try:
        data = json.loads(_HR_WF_POINTER.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.debug("[L3 LocalMemory] 读取 HR workflow 指针失败: %s", e)
        return {}


def get_hr_recruitment_active_workflow_id() -> str | None:
    """当前 Lark/调度应操作的 hr_recruitment workflow_id。"""
    ptr = get_hr_recruitment_workflow_pointer()
    wid = (ptr.get("workflow_id") or "").strip()
    return wid or None
