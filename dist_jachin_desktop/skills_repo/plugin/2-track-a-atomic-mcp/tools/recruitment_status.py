"""
招聘状态看板 - recruitment_status.json
维护于 ~/.jachin/workspace/，供双触发引擎读取。
数据按职位存于 plugin/data/{职位}/pending、processed、result/
"""
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from .hr_data_paths import PLUGIN_DATA_ROOT

WORKSPACE = Path.home() / ".jachin" / "workspace"
STATUS_FILE = WORKSPACE / "recruitment_status.json"


def _ensure_workspace() -> None:
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    PLUGIN_DATA_ROOT.mkdir(parents=True, exist_ok=True)


def load_status() -> dict:
    """加载 recruitment_status.json"""
    _ensure_workspace()
    if not STATUS_FILE.exists():
        return _default_status()
    try:
        data = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        return {**_default_status(), **data}
    except Exception as e:
        logger.warning(f"load_status failed: {e}")
        return _default_status()


def save_status(data: dict) -> bool:
    """保存 recruitment_status.json"""
    _ensure_workspace()
    try:
        STATUS_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return True
    except Exception as e:
        logger.error(f"save_status failed: {e}")
        return False


def _default_status() -> dict:
    return {
        "job_title": "Java开发",
        "status": "hunting",
        "batch_limit": 50,
        "cron_trigger_time": "08:30",
        "unprocessed_pdfs": 0,
        "total_processed": 0,
        "hr_criteria": "",
        "jd_full": "",
        "scanned_online_count": 0,
        "greeted_count": 0,
        "last_milestone_notified": 0,
        "last_progress_notify_time": "",
    }


def refresh_unprocessed_count(job_name: str = "") -> int:
    """根据 data/{职位}/pending 实际 PDF 数量刷新。job_name 为空则统计所有职位"""
    _ensure_workspace()
    if job_name:
        from .hr_data_paths import get_job_pending_dir
        pending = get_job_pending_dir(job_name)
        count = len(list(pending.rglob("*.pdf")))
    else:
        count = sum(len(list((PLUGIN_DATA_ROOT / d / "pending").rglob("*.pdf"))) for d in PLUGIN_DATA_ROOT.iterdir() if (PLUGIN_DATA_ROOT / d / "pending").is_dir())
    data = load_status()
    data["unprocessed_pdfs"] = count
    save_status(data)
    return count


def should_trigger_final_judgment(now_time: str = "") -> tuple[bool, str]:
    """
    双触发引擎：是否应执行终局审判。
    Returns:
        (should_trigger, reason)
    """
    data = load_status()
    count = refresh_unprocessed_count()
    batch_limit = int(data.get("batch_limit", 50))
    cron_time = data.get("cron_trigger_time", "08:30")

    # 触发 1: 满载溢出
    if count >= batch_limit:
        return True, f"unprocessed_pdfs({count}) >= batch_limit({batch_limit})"

    # 触发 2: 每日早报时间（由调用方传入当前时间 "HH:MM"）
    if now_time and now_time == cron_time:
        return True, f"cron_trigger_time reached ({cron_time})"

    return False, ""


def get_pending_pdfs(job_name: str = "") -> list[Path]:
    """获取 data/{职位}/pending 下所有 PDF。job_name 为空则返回所有职位"""
    _ensure_workspace()
    if job_name:
        from .hr_data_paths import get_job_pending_dir
        return sorted(get_job_pending_dir(job_name).rglob("*.pdf"))
    out = []
    for d in PLUGIN_DATA_ROOT.iterdir():
        if d.is_dir():
            pend = d / "pending"
            if pend.is_dir():
                out.extend(pend.rglob("*.pdf"))
    return sorted(out)


def update_status(**kwargs: Any) -> bool:
    """更新状态字段"""
    data = load_status()
    data.update(kwargs)
    return save_status(data)


def move_to_processed(pdf_path: Path, job_name: str = "") -> Path:
    """将 PDF 从 pending 移至 processed。pdf_path 应在 data/{职位}/pending 下"""
    _ensure_workspace()
    from .hr_data_paths import get_job_processed_dir
    try:
        # pdf_path 形如 .../data/Java工程师/pending/xxx.pdf -> dest = .../data/Java工程师/processed/xxx.pdf
        parts = pdf_path.parts
        if "pending" in parts:
            idx = list(parts).index("pending")
            job_folder = parts[idx - 1] if idx > 0 else ""
            dest_dir = PLUGIN_DATA_ROOT / job_folder / "processed"
        else:
            dest_dir = get_job_processed_dir(job_name) if job_name else PLUGIN_DATA_ROOT / "未分类" / "processed"
    except Exception:
        dest_dir = PLUGIN_DATA_ROOT / "未分类" / "processed"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / pdf_path.name
    if pdf_path.exists():
        import shutil
        shutil.move(str(pdf_path), str(dest))
    return dest
