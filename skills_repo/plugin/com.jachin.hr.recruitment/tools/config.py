"""
HR 招聘 MCP 包 — 路径配置

从 ~/.jachin/config/ 或环境变量读取，支持订阅后目标机可移植。
"""
from __future__ import annotations

import os
from pathlib import Path


def _get_jachin_root() -> Path:
    """~/.jachin 或 JACHIN_HOME"""
    home = os.environ.get("JACHIN_HOME")
    if home:
        return Path(home).expanduser().resolve()
    return Path.home() / ".jachin"


def get_data_root() -> Path:
    """
    招聘数据根目录：data/{岗位}/pending|processed|result
    默认 ~/.jachin/workspace/hr_recruitment/
    """
    root = _get_jachin_root()
    custom = os.environ.get("JACHIN_HR_DATA_ROOT", "").strip()
    if custom:
        p = Path(custom).expanduser().resolve()
        if p.is_absolute():
            return p
        return root / custom
    return root / "workspace" / "hr_recruitment"


def get_resume_root() -> Path:
    """简历存储根目录，供 read_file 等访问。默认 ~/.jachin/workspace/hr_resumes"""
    root = _get_jachin_root()
    custom = os.environ.get("JACHIN_HR_RESUME_ROOT", "").strip()
    if custom:
        p = Path(custom).expanduser().resolve()
        if p.is_absolute():
            return p
        return root / custom
    return root / "workspace" / "hr_resumes"


def get_jd_config_root() -> Path:
    """JD 配置目录，默认 ~/.jachin/config/hr_jds"""
    root = _get_jachin_root()
    return root / "config" / "hr_jds"


def get_hr_analysis_output_root() -> Path:
    """HR 透析镜分析输出根目录，默认 ~/.jachin/workspace/hr_analysis"""
    root = _get_jachin_root()
    custom = os.environ.get("JACHIN_HR_ANALYSIS_OUTPUT", "").strip()
    if custom:
        p = Path(custom).expanduser().resolve()
        if p.is_absolute():
            return p
        return root / custom
    return root / "workspace" / "hr_analysis"


def get_scheduler_state_dir() -> Path:
    """调度器状态文件目录，默认 ~/.jachin/workspace/hr_recruitment/hr_analysis"""
    return get_data_root() / "hr_analysis"


def get_resolve_base() -> Path:
    """
    路径解析基准：订阅/目标机优先 ~/.jachin，开发模式可回退项目根。
    供 _resolve_safe_dir 等使用，确保 output_dir 等可移植。
    """
    return _get_jachin_root()


def get_plugin_package_root() -> Path:
    """
    当前 MCP 包根目录（用于定位 jd_to_publish.example.json 等）。
    从 __file__ 推导，兼容 l3_mcp_cache 加载。
    """
    return Path(__file__).resolve().parent.parent
