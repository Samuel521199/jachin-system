"""
L3 任务规划与跨会话持久 — 智能化升级 P0

task_plan.md / progress.md 工作记忆，支持跨会话续接。
设计: docs/JACHIN_VS_OPENCLAW_INTELLIGENCE_ANALYSIS.md

HR 招聘专用：`~/.jachin/workspace/hr_recruitment/task_plan.md` 与 `progress.md`
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_JACHIN_ROOT = Path.home() / ".jachin"
_WORKSPACE = _JACHIN_ROOT / "workspace"
_TASK_PLAN = _WORKSPACE / "task_plan.md"
_PROGRESS = _WORKSPACE / "progress.md"
_FINDINGS = _WORKSPACE / "findings.md"


def _ensure_workspace() -> Path:
    _WORKSPACE.mkdir(parents=True, exist_ok=True)
    return _WORKSPACE


def get_task_plan_path() -> Path:
    return _TASK_PLAN


def get_progress_path() -> Path:
    return _PROGRESS


def get_findings_path() -> Path:
    return _FINDINGS


def read_task_plan() -> str:
    """读取当前任务计划"""
    if not _TASK_PLAN.exists():
        return ""
    try:
        return _TASK_PLAN.read_text(encoding="utf-8").strip()
    except Exception as e:
        logger.debug("[TaskPlanning] read_task_plan 失败: %s", e)
        return ""


def task_plan_is_substantial(*, min_chars: int = 40) -> bool:
    """task_plan.md 是否存在且达到最小有效长度（门禁用）。"""
    return len(read_task_plan()) >= max(20, int(min_chars))


def write_task_plan(content: str) -> bool:
    """写入任务计划"""
    try:
        _ensure_workspace()
        _TASK_PLAN.write_text((content or "").strip(), encoding="utf-8")
        return True
    except Exception as e:
        logger.warning("[TaskPlanning] write_task_plan 失败: %s", e)
        return False


def read_progress() -> str:
    """读取进度"""
    if not _PROGRESS.exists():
        return ""
    try:
        return _PROGRESS.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def progress_has_open_checkboxes() -> bool:
    """progress.md 是否含未完成项（阶段 B：compaction checkpoint 加权）。"""
    t = read_progress()
    if not t:
        return False
    if "- [ ]" in t or "* [ ]" in t or "☐" in t:
        return True
    return bool(re.search(r"^(\*|-)\s+\[\s*\]", t, re.MULTILINE))


def append_progress(line: str) -> bool:
    """追加进度"""
    try:
        _ensure_workspace()
        with _PROGRESS.open("a", encoding="utf-8") as f:
            f.write((line or "").strip() + "\n")
        return True
    except Exception as e:
        logger.warning("[TaskPlanning] append_progress 失败: %s", e)
        return False


def read_findings() -> str:
    """读取发现"""
    if not _FINDINGS.exists():
        return ""
    try:
        return _FINDINGS.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def write_findings(content: str) -> bool:
    """写入发现"""
    try:
        _ensure_workspace()
        _FINDINGS.write_text((content or "").strip(), encoding="utf-8")
        return True
    except Exception as e:
        logger.warning("[TaskPlanning] write_findings 失败: %s", e)
        return False


def clear_task_context() -> None:
    """清空任务上下文（新任务开始）"""
    try:
        for p in (_TASK_PLAN, _PROGRESS, _FINDINGS):
            if p.exists():
                p.write_text("", encoding="utf-8")
    except Exception as e:
        logger.debug("[TaskPlanning] clear 失败: %s", e)


# ---------------------------------------------------------------------------
# HR 招聘 — 物理路径绑定（与 hr_recruitment_dag 协同）
# ---------------------------------------------------------------------------

_HR_RECRUITMENT = _WORKSPACE / "hr_recruitment"


def get_hr_recruitment_dir() -> Path:
    _HR_RECRUITMENT.mkdir(parents=True, exist_ok=True)
    return _HR_RECRUITMENT


def get_hr_recruitment_task_plan_path() -> Path:
    return get_hr_recruitment_dir() / "task_plan.md"


def get_hr_recruitment_progress_path() -> Path:
    return get_hr_recruitment_dir() / "progress.md"


def write_hr_recruitment_task_plan(content: str) -> bool:
    """覆写 HR 宏图（DAG 起点节点）。"""
    try:
        get_hr_recruitment_dir()
        get_hr_recruitment_task_plan_path().write_text((content or "").strip() + "\n", encoding="utf-8")
        return True
    except Exception as e:
        logger.warning("[TaskPlanning] write_hr_recruitment_task_plan 失败: %s", e)
        return False


def read_hr_recruitment_progress() -> str:
    p = get_hr_recruitment_progress_path()
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def append_hr_recruitment_progress(line: str) -> bool:
    """追加 HR 战况行（HarvestLoop / 中断 / HITL）。"""
    try:
        get_hr_recruitment_dir()
        with get_hr_recruitment_progress_path().open("a", encoding="utf-8") as f:
            f.write((line or "").rstrip() + "\n")
        return True
    except Exception as e:
        logger.warning("[TaskPlanning] append_hr_recruitment_progress 失败: %s", e)
        return False


def extract_hr_progress_for_workflow(full_text: str, workflow_id: str) -> str:
    """
    仅解析当前 workflow 会话段落（由 append_hr_session_header 写入的分隔块）。
    若无匹配块则回退为全文，兼容旧 progress。
    """
    wid = (workflow_id or "").strip()
    if not wid or not full_text:
        return full_text or ""
    marker = f"## Session `{wid}`"
    idx = full_text.rfind(marker)
    if idx < 0:
        return full_text
    return full_text[idx:]


# 成功打招呼 5 人。当前总进度：（15 / 80） — 半角/全角括号
_RE_HR_GREET_LINE = re.compile(
    r"(?:成功)?\s*打招呼\s*(\d+)\s*人.*?总进度[：:]\s*[\(（](\d+)\s*/\s*(\d+)[\)）]",
    re.IGNORECASE | re.DOTALL,
)
_RE_HR_RESUME_LINE = re.compile(
    r"(?:成功)?\s*(抓取|下载)\s*(\d+)\s*份简历.*?总进度[：:]\s*[\(（](\d+)\s*/\s*(\d+)[\)）]",
    re.IGNORECASE | re.DOTALL,
)


def parse_hr_recruitment_progress_last_counts(section_text: str) -> tuple[int | None, int | None]:
    """
    从 progress 段落中解析「最后一次」汇报的已沟通人数、已抓取简历数（取捕获组中的当前累计）。
    返回 (greeted_total, resume_total)。
    """
    t = section_text or ""
    last_g: int | None = None
    last_r: int | None = None
    for m in _RE_HR_GREET_LINE.finditer(t):
        try:
            last_g = int(m.group(2))
        except ValueError:
            pass
    for m in _RE_HR_RESUME_LINE.finditer(t):
        try:
            last_r = int(m.group(3))
        except ValueError:
            pass
    return last_g, last_r


def append_hr_session_header(workflow_id: str, job_label: str, ts: str) -> bool:
    """新宏图或新会话时写入 progress 分隔头，便于按 workflow 解析恢复计数。"""
    wid = (workflow_id or "").strip() or "unknown"
    job = (job_label or "").strip() or "（未命名岗位）"
    block = f"\n---\n## Session `{wid}` · {job} · {ts}\n"
    return append_hr_recruitment_progress(block)


def get_planning_context_for_prompt() -> str:
    """
    获取任务规划上下文，供 Agent System Prompt 注入。
    若存在 task_plan 或 progress，则注入「继续执行计划」提示。
    """
    plan = read_task_plan()
    progress = read_progress()
    if not plan and not progress:
        return ""
    parts = []
    if plan:
        parts.append(f"【当前任务计划】\n{plan[:800]}{'...' if len(plan) > 800 else ''}")
    if progress:
        parts.append(f"【已执行进度】\n{progress[-600:]}{'...' if len(progress) > 600 else ''}")
    if not parts:
        return ""
    return "\n\n".join(parts) + "\n\n请根据上述计划继续执行，或更新 progress。\n"
