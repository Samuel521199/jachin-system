"""
HR 插件统一数据路径 - 按职位分目录存储（全部在 ~/.jachin/workspace/hr_recruitment 下，不写项目目录）

根目录: config.get_data_root()，默认 ~/.jachin/workspace/hr_recruitment/
结构: {职位文件夹}/
  - pending/      刚抓取未对比的简历 PDF
  - processed/    对比完成的简历
  - result/       每个简历的 AI 分析报告 (*_analysis.md)
  - jd.json       专属该职位的 JD 配置
  - 排行榜_Summary.md
"""
from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from .config import get_data_root, get_plugin_package_root

logger = logging.getLogger(__name__)

PLUGIN_DATA_ROOT = get_data_root()

# 透析镜/分析列举简历时子目录顺序：pending → processed → 副本（pending 无文件时读备份目录）
HR_RESUME_SCAN_SUBDIRS: tuple[str, ...] = ("pending", "processed", "副本")

DEFAULT_RESUME_ANALYZE_EXTS: frozenset[str] = frozenset({".pdf", ".md", ".docx", ".txt"})

# JD 模板：包内或 data 根下
_JD_TEMPLATE_CANDIDATES = [
    get_plugin_package_root() / "data" / "jd_to_publish.example.json",
    PLUGIN_DATA_ROOT / "jd_to_publish.example.json",
]


def _get_jd_template_path() -> Path:
    for p in _JD_TEMPLATE_CANDIDATES:
        if p.exists():
            return p
    return _JD_TEMPLATE_CANDIDATES[0]


def resolve_hr_job_root_from_path(path: Path) -> Path | None:
    """
    从 pending/processed/副本 或其子路径反推「职位根」目录（含 pending 子目录的那一层）。
    非标准布局（如扁平 hr_resumes）返回 None。
    """
    try:
        p = path.resolve()
    except OSError:
        return None
    if p.is_file():
        p = p.parent
    cur: Path = p
    for _ in range(12):
        if cur.name != "pending" and (cur / "pending").is_dir():
            return cur
        if cur.name in HR_RESUME_SCAN_SUBDIRS:
            par = cur.parent
            if par == cur:
                break
            for x in HR_RESUME_SCAN_SUBDIRS:
                if (par / x).is_dir():
                    return par
        nxt = cur.parent
        if nxt == cur:
            break
        cur = nxt
    return None


def collect_resume_paths_for_analysis(
    *,
    primary_dir: Path,
    max_files: int = 50,
    extensions: frozenset[str] | None = None,
) -> tuple[list[Path], Path]:
    """
    按 pending → processed → 副本 顺序 rglob 简历文件（去重），避免仅盯 pending 导致「找不到简历」。

    Returns:
        (绝对路径列表, target_dir 建议值) — 建议 target_dir 为职位根，便于与绝对路径 _hr_files 搭配。
    """
    exts = extensions or DEFAULT_RESUME_ANALYZE_EXTS
    try:
        primary = primary_dir.resolve()
    except OSError:
        return [], primary_dir

    job_root = resolve_hr_job_root_from_path(primary)

    scan_dirs: list[Path] = []
    seen_dir: set[str] = set()

    def add_scan(d: Path) -> None:
        try:
            dr = d.resolve()
        except OSError:
            return
        if not dr.is_dir():
            return
        k = str(dr)
        if k in seen_dir:
            return
        seen_dir.add(k)
        scan_dirs.append(dr)

    if job_root:
        jr = job_root.resolve()
        for name in HR_RESUME_SCAN_SUBDIRS:
            add_scan(jr / name)
    else:
        add_scan(primary)

    pending_dir = (job_root / "pending").resolve() if job_root else primary
    seen_file: set[str] = set()
    files: list[Path] = []

    for d in scan_dirs:
        if not d.is_dir():
            continue
        try:
            for f in sorted(d.rglob("*")):
                if len(files) >= max_files:
                    break
                if not f.is_file():
                    continue
                if f.suffix.lower() not in exts:
                    continue
                k = str(f.resolve())
                if k in seen_file:
                    continue
                seen_file.add(k)
                files.append(Path(k))
        except OSError as e:
            logger.debug("[HR] 扫描简历目录跳过 %s: %s", d, e)
        if len(files) >= max_files:
            break

    if files and job_root:
        n_under_pending = 0
        for f in files:
            try:
                f.resolve().relative_to(pending_dir)
                n_under_pending += 1
            except ValueError:
                pass
        if n_under_pending == 0:
            logger.info(
                "[HR] pending 中无可用简历，已从 processed/副本 等目录检索 %d 份用于分析",
                len(files),
            )
        elif n_under_pending < len(files):
            logger.info(
                "[HR] 已合并 pending 与其它目录简历，共 %d 份（其中 pending 内 %d 份）",
                len(files),
                n_under_pending,
            )

    anchor = job_root.resolve() if job_root else primary
    return files, anchor


def sanitize_job_folder(job_name: str, max_len: int = 60) -> str:
    """将岗位名转为安全文件夹名"""
    illegal = r'\/:*?"<>|'
    for c in illegal:
        job_name = job_name.replace(c, "_")
    s = "".join(c if c.isalnum() or c in " _-（）【】" else "_" for c in job_name)
    return s.strip("_")[:max_len] or "未分类"


def get_job_dir(job_name: str) -> Path:
    """职位根目录 hr_recruitment/{job_folder}/"""
    folder = sanitize_job_folder(job_name)
    return PLUGIN_DATA_ROOT / folder


def get_job_jd_path(job_name: str) -> Path:
    """职位专属 JD 配置 hr_recruitment/{job_folder}/jd.json"""
    return get_job_dir(job_name) / "jd.json"


def get_job_pending_dir(job_name: str) -> Path:
    """待对比简历 hr_recruitment/{job_folder}/pending/"""
    d = get_job_dir(job_name) / "pending"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_job_processed_dir(job_name: str) -> Path:
    """已对比简历 hr_recruitment/{job_folder}/processed/"""
    d = get_job_dir(job_name) / "processed"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_job_result_dir(job_name: str) -> Path:
    """AI 分析报告 hr_recruitment/{job_folder}/result/"""
    d = get_job_dir(job_name) / "result"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_job_summary_md_path(job_name: str) -> Path:
    """职位专属排行榜 MD hr_recruitment/{job_folder}/排行榜_Summary.md"""
    return get_job_dir(job_name) / "排行榜_Summary.md"


def ensure_job_dirs(job_name: str) -> Path:
    """创建职位目录结构，返回职位根目录"""
    base = get_job_dir(job_name)
    (base / "pending").mkdir(parents=True, exist_ok=True)
    (base / "processed").mkdir(parents=True, exist_ok=True)
    (base / "result").mkdir(parents=True, exist_ok=True)
    return base


def init_job_jd_from_template(job_name: str, overrides: dict | None = None) -> Path:
    """
    HR 确认后自动执行：复制模板到 hr_recruitment/{职位}/jd.json，用 overrides 覆盖填写并保存。
    """
    base = get_job_dir(job_name)
    base.mkdir(parents=True, exist_ok=True)
    (base / "pending").mkdir(parents=True, exist_ok=True)
    (base / "processed").mkdir(parents=True, exist_ok=True)
    (base / "result").mkdir(parents=True, exist_ok=True)

    jd_path = base / "jd.json"
    template = _get_jd_template_path()
    if template.exists():
        shutil.copy2(template, jd_path)
        base_cfg = json.loads(jd_path.read_text(encoding="utf-8"))
        if isinstance(base_cfg, dict) and "_comment" in base_cfg:
            base_cfg.pop("_comment", None)
    else:
        base_cfg = {}

    merged = {**base_cfg}
    if overrides:
        for k, v in overrides.items():
            if v is not None and k not in ("_comment",):
                merged[k] = v
    if not merged.get("job_title") and job_name:
        merged["job_title"] = job_name
    if not merged.get("jd_select") and merged.get("job_title"):
        title = (merged.get("job_title") or "").strip()
        city = (merged.get("job_location") or "杭州").strip()
        sal_min, sal_max = merged.get("salary_min"), merged.get("salary_max")
        if sal_min is not None and sal_max is not None:
            merged["jd_select"] = f"{title} _ {city} {int(sal_min)}-{int(sal_max)}K"
        elif sal_min is not None:
            merged["jd_select"] = f"{title} _ {city} {int(sal_min)}K"

    jd_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    summary_md = base / "排行榜_Summary.md"
    if not summary_md.exists():
        summary_md.write_text(
            f"# {job_name} - AI 招聘决断排行榜\n\n待分析完成后更新。\n",
            encoding="utf-8",
        )
    return jd_path
