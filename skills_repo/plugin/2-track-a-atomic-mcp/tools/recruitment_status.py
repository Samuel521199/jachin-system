"""
招聘状态看板 - recruitment_status.json
维护于 ~/.jachin/workspace/，供双触发引擎读取。
pending/processed 目录位于项目 data/ 下。
"""
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

WORKSPACE = Path.home() / ".jachin" / "workspace"
STATUS_FILE = WORKSPACE / "recruitment_status.json"
# 项目 data 目录：待审 PDF -> pending，已审 -> processed
_DATA_ROOT = Path(__file__).resolve().parent.parent.parent / "data"
PENDING_DIR = _DATA_ROOT / "pending"
PROCESSED_DIR = _DATA_ROOT / "processed"


def _ensure_workspace() -> None:
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    _DATA_ROOT.mkdir(parents=True, exist_ok=True)
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)


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


def refresh_unprocessed_count() -> int:
    """根据 pending 目录实际 PDF 数量刷新 unprocessed_pdfs（含子目录）"""
    _ensure_workspace()
    count = len(list(PENDING_DIR.rglob("*.pdf")))
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


def get_pending_pdfs() -> list[Path]:
    """获取 pending 目录下所有 PDF 路径（含子目录，按职位分）"""
    _ensure_workspace()
    return sorted(PENDING_DIR.rglob("*.pdf"))


def update_status(**kwargs: Any) -> bool:
    """更新状态字段"""
    data = load_status()
    data.update(kwargs)
    return save_status(data)


def move_to_processed(pdf_path: Path) -> Path:
    """将 PDF 从 pending 移至 processed，保留职位子目录结构"""
    _ensure_workspace()
    try:
        rel = pdf_path.relative_to(PENDING_DIR)
        job_subdir = rel.parent
        dest_dir = PROCESSED_DIR / job_subdir
    except ValueError:
        dest_dir = PROCESSED_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / pdf_path.name
    if pdf_path.exists():
        import shutil
        shutil.move(str(pdf_path), str(dest))
    return dest
