"""
HR 插件统一数据路径 - 按职位分目录存储

业务数据根目录: ~/.jachin/workspace/hr_recruitment/（或 JACHIN_HR_DATA_ROOT），**不写入仓库**。
JD 模板: 仍从 skills_repo/plugin/data/jd_to_publish.example.json 读取（只读，不落业务数据）。

结构: hr_recruitment/{职位文件夹}/
  - pending/      刚抓取未对比的简历 PDF
  - processed/    对比完成的简历
  - result/       每个简历的 AI 分析报告 (*_analysis.md)
  - jd.json       专属该职位的 JD 配置（从 jd_to_publish.example.json 模板复制后填写）
  - 排行榜_Summary.md  专属该职位的输出 MD 文档
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# skills_repo/plugin（含 data/jd_to_publish.example.json 模板，仅供复制）
_PLUGIN_PARENT = Path(__file__).resolve().parents[2]


def _jachin_root() -> Path:
    h = os.environ.get("JACHIN_HOME", "").strip()
    if h:
        return Path(h).expanduser().resolve()
    return Path.home() / ".jachin"


def _hr_recruitment_data_root() -> Path:
    root = _jachin_root()
    custom = os.environ.get("JACHIN_HR_DATA_ROOT", "").strip()
    if custom:
        p = Path(custom).expanduser().resolve()
        return p if p.is_absolute() else (root / p)
    return root / "workspace" / "hr_recruitment"


PLUGIN_DATA_ROOT = _hr_recruitment_data_root()

# 全局 JD 模板（仓库内只读文件，不存放候选人 PDF）
JD_TEMPLATE_PATH = _PLUGIN_PARENT / "data" / "jd_to_publish.example.json"


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
    HR 确认后自动执行：复制 jd_to_publish.example.json 到 hr_recruitment/{职位}/jd.json，用 overrides 覆盖填写并保存。
    同时创建 pending、processed、result 及 排行榜_Summary.md 占位。
    Returns:
        写入后的 jd.json 路径
    """
    import shutil

    base = get_job_dir(job_name)
    base.mkdir(parents=True, exist_ok=True)
    (base / "pending").mkdir(parents=True, exist_ok=True)
    (base / "processed").mkdir(parents=True, exist_ok=True)
    (base / "result").mkdir(parents=True, exist_ok=True)
    jd_path = base / "jd.json"
    template = JD_TEMPLATE_PATH
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
