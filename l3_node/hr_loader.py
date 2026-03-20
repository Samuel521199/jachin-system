"""
HR 招聘模块动态加载器

当 HR 包 (com.jachin.hr.recruitment) 存在时，从 l3_mcp_cache 或 skills_repo 加载
recruitment_scheduler、recruitment_task、hr_analysis_persist。exe 不包含这些模块，完全通过订阅下载使用。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _get_hr_recruitment_plugin_root() -> Path | None:
    """HR 招聘 MCP 包根目录：l3_mcp_cache 优先（支持 UUID 目录名），其次 skills_repo（开发）。"""
    import json
    import os

    jachin = Path(os.environ.get("JACHIN_HOME", str(Path.home() / ".jachin")))
    cache_base = jachin / "l3_mcp_cache"
    target_id = "com.jachin.hr.recruitment"

    # 1. 精确路径（L2 用 plugin id 作 item_id 时）
    cache_dir = cache_base / target_id
    if cache_dir.exists() and (cache_dir / "plugin.json").exists():
        return cache_dir

    # 2. 扫描 l3_mcp_cache：L2 可能用 UUID 作 item_id，需按 plugin.json 的 id 匹配
    if cache_base.exists():
        for sub in cache_base.iterdir():
            if not sub.is_dir():
                continue
            plugin_path = sub / "plugin.json"
            if not plugin_path.exists():
                continue
            try:
                data = json.loads(plugin_path.read_text(encoding="utf-8"))
                if data.get("id") == target_id:
                    return sub
            except Exception:
                pass

    # 3. 开发模式：skills_repo
    try:
        from l3_node.paths import get_app_root
        proj = get_app_root()
        dev_dir = proj / "skills_repo" / "plugin" / target_id
        if dev_dir.exists() and (dev_dir / "plugin.json").exists():
            return dev_dir
    except Exception:
        pass
    return None


def _load_hr_module(name: str):
    """从 HR 包动态加载模块。name 为 recruitment_scheduler、recruitment_task、hr_analysis_persist 之一。"""
    hr_root = _get_hr_recruitment_plugin_root()
    if not hr_root or not hr_root.exists():
        raise ImportError(
            "HR 招聘 MCP 包未找到。请从 L1 订阅 com.jachin.hr.recruitment 并下载到 l3_mcp_cache，"
            "或确保 skills_repo/plugin/com.jachin.hr.recruitment 存在。"
        )
    cache_str = str(hr_root.resolve())
    prev = sys.path.copy()
    try:
        if cache_str not in sys.path:
            sys.path.insert(0, cache_str)
        return __import__(name, fromlist=[name])
    finally:
        sys.path = prev


def get_recruitment_scheduler():
    """动态加载 recruitment_scheduler 模块，HR 包不存在时返回 None。"""
    try:
        return _load_hr_module("recruitment_scheduler")
    except ImportError as e:
        logger.debug("[HR Loader] recruitment_scheduler 未加载: %s", e)
        return None


def get_recruitment_task():
    """动态加载 recruitment_task 模块，HR 包不存在时返回 None。"""
    try:
        return _load_hr_module("recruitment_task")
    except ImportError as e:
        logger.debug("[HR Loader] recruitment_task 未加载: %s", e)
        return None


def get_hr_analysis_persist():
    """动态加载 hr_analysis_persist 模块，HR 包不存在时返回 None。"""
    try:
        return _load_hr_module("hr_analysis_persist")
    except ImportError as e:
        logger.debug("[HR Loader] hr_analysis_persist 未加载: %s", e)
        return None


def is_hr_package_available() -> bool:
    """HR 包是否可用。"""
    return _get_hr_recruitment_plugin_root() is not None
