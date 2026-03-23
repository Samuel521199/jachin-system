"""
原子 Tool: local_archiver
将 PDF 附件保存到 ~/.jachin/workspace/hr_recruitment/{职位}/pending/（不写仓库目录）。
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

from .hr_data_paths import PLUGIN_DATA_ROOT, get_job_pending_dir

logger = logging.getLogger(__name__)

DEFAULT_JOB_FOLDER = "未分类"


def _is_under_app_repository(path: Path) -> bool:
    """HR 收网简历禁止落在项目/仓库内。"""
    try:
        resolved = path.expanduser().resolve()
        from l3_node.paths import get_app_root

        root = get_app_root()
        if not root:
            return False
        resolved.relative_to(root.resolve())
        return True
    except (ImportError, ValueError, TypeError, OSError, AttributeError):
        return False


def _sanitize_filename(s: str, max_len: int = 80) -> str:
    illegal = r'\/:*?"<>|'
    for c in illegal:
        s = s.replace(c, "_")
    s = "".join(c if c.isalnum() or c in " _-（）【】" else "_" for c in s)
    return s.strip("_")[:max_len]


def _extract_job_folder(file_label: str = "", job_folder: str = "") -> str:
    if job_folder and job_folder.strip():
        return _sanitize_filename(job_folder.strip(), max_len=60)
    if file_label:
        m = re.search(r"【([^】]+)】", file_label)
        if m:
            return _sanitize_filename(m.group(1).strip(), max_len=60)
    return DEFAULT_JOB_FOLDER


def local_archiver(
    pdf_path: str = "",
    pdf_bytes: bytes = b"",
    candidate_name: str = "",
    file_label: str = "",
    job_folder: str = "",
    target_dir: str | Path | None = None,
    use_flat_dir: bool = False,
) -> dict:
    """
    将 PDF 保存到 ~/.jachin/workspace/hr_recruitment/{职位}/pending/。
    target_dir 若指向项目仓库则忽略，改写到 hr_recruitment。
    """
    target_dir_resolved: Path
    if target_dir is not None:
        base = Path(target_dir) if isinstance(target_dir, str) else target_dir
        if _is_under_app_repository(base):
            logger.warning(
                "[local_archiver] target_dir 落在项目仓库内，已忽略并改用 hr_recruitment 目录: %s",
                base,
            )
            target_dir = None
        elif use_flat_dir:
            target_dir_resolved = base
        else:
            subdir = _extract_job_folder(file_label, job_folder)
            target_dir_resolved = base / subdir if subdir != DEFAULT_JOB_FOLDER else base

    if target_dir is None:
        if job_folder and job_folder.strip():
            target_dir_resolved = get_job_pending_dir(job_folder)
        else:
            subdir = _extract_job_folder(file_label, job_folder)
            target_dir_resolved = PLUGIN_DATA_ROOT / DEFAULT_JOB_FOLDER / "pending" / subdir

    target_dir_resolved.mkdir(parents=True, exist_ok=True)
    try:
        if pdf_path:
            src = Path(pdf_path).resolve()
            if not src.exists():
                return {"success": False, "saved_path": "", "error": f"文件不存在: {pdf_path}"}
            if src.suffix.lower() != ".pdf":
                return {"success": False, "saved_path": "", "error": "仅支持 PDF 文件"}
            content = src.read_bytes()
        elif pdf_bytes:
            content = pdf_bytes
        else:
            return {"success": False, "saved_path": "", "error": "需提供 pdf_path 或 pdf_bytes"}

        h = hashlib.sha256(content[:1024]).hexdigest()[:8]
        if file_label:
            base = _sanitize_filename(file_label)
            fname = f"{base}_{h}.pdf" if base else f"resume_{h}.pdf"
        else:
            safe_name = _sanitize_filename(candidate_name or "candidate", max_len=30)
            fname = f"{safe_name}_{h}.pdf" if safe_name else f"resume_{h}.pdf"
        dst = target_dir_resolved / fname
        dst.write_bytes(content)
        return {"success": True, "saved_path": str(dst), "error": ""}
    except Exception as e:
        logger.error("local_archiver failed: %s", e, exc_info=True)
        return {"success": False, "saved_path": "", "error": str(e)}
