"""
原子 Tool: local_archiver
仅负责将 PDF 附件保存到项目 data/pending/ 目录。
按职位分文件夹：data/pending/<职位名>/ 与 data/processed/<职位名>/
"""
import hashlib
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# 项目根目录 (plugin/)
_ROOT = Path(__file__).resolve().parent.parent.parent
PENDING_DIR = _ROOT / "data" / "pending"
PROCESSED_DIR = _ROOT / "data" / "processed"
DEFAULT_JOB_FOLDER = "未分类"


def _sanitize_filename(s: str, max_len: int = 80) -> str:
    """将字符串转为安全文件名，去掉非法字符"""
    illegal = r'\/:*?"<>|'
    for c in illegal:
        s = s.replace(c, "_")
    s = "".join(c if c.isalnum() or c in " _-（）【】" else "_" for c in s)
    return s.strip("_")[:max_len]


def _extract_job_folder(file_label: str = "", job_folder: str = "") -> str:
    """从 file_label 的【】内或 job_folder 提取职位文件夹名"""
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
) -> dict:
    """
    将 PDF 保存到 pending 目录，按职位分文件夹。
    Args:
        pdf_path: 本地 PDF 文件路径（二选一）
        pdf_bytes: PDF 二进制内容（二选一，如从消息附件下载）
        candidate_name: 候选人标识，用于生成文件名（file_label 为空时使用）
        file_label: 自定义文件名（不含.pdf），如「【Java_杭州 3-4K】张俊 26年应届生」
        job_folder: 职位文件夹名，用于分类。不传则从 file_label 的【】内提取
        target_dir: 覆盖保存根目录（如 ~/.jachin/volumes/xxx），不传则用 PENDING_DIR
    Returns:
        {"success": bool, "saved_path": str, "error": str}
    """
    subdir = _extract_job_folder(file_label, job_folder)
    if target_dir is not None:
        base = Path(target_dir) if isinstance(target_dir, str) else target_dir
        target_dir_resolved = base / subdir
    else:
        target_dir_resolved = PENDING_DIR / subdir
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
        logger.error(f"local_archiver failed: {e}", exc_info=True)
        return {"success": False, "saved_path": "", "error": str(e)}
