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


def _hr_dev_plugin_dir() -> Path | None:
    """仓库内 skills_repo 的 com.jachin.hr.recruitment（开发联调用最新 recruitment_scheduler）。"""
    target_id = "com.jachin.hr.recruitment"
    try:
        from l3_node.paths import get_app_root

        proj = get_app_root()
        dev_dir = proj / "skills_repo" / "plugin" / target_id
        if dev_dir.exists() and (dev_dir / "plugin.json").exists():
            return dev_dir
    except Exception:
        pass
    return None


def _get_hr_recruitment_plugin_root() -> Path | None:
    """
    HR 招聘 MCP 包根目录。

    默认：l3_mcp_cache 优先（与 L2 订阅一致），其次 skills_repo。

    若存在 **陈旧缓存** 盖过本仓库代码，会导致 MCP 日志已是 enable_greet_recommend=False，
    但调度仍走旧逻辑（继续「推荐牛人」）。可通过以下方式强制使用仓库内插件：

    - ``JACHIN_DEV_HR_FIRST=1``：skills_repo 优先于 l3_mcp_cache
    - ``JACHIN_HR_RECRUITMENT_ROOT=<目录>``：显式指定包根（须含 plugin.json）
    """
    import json
    import os

    jachin = Path(os.environ.get("JACHIN_HOME", str(Path.home() / ".jachin")))
    cache_base = jachin / "l3_mcp_cache"
    target_id = "com.jachin.hr.recruitment"

    override = (os.environ.get("JACHIN_HR_RECRUITMENT_ROOT") or "").strip()
    if override:
        p = Path(override)
        if p.is_dir() and (p / "plugin.json").exists():
            logger.info("[HR Loader] 使用 JACHIN_HR_RECRUITMENT_ROOT=%s", p)
            return p
        logger.warning("[HR Loader] JACHIN_HR_RECRUITMENT_ROOT 无效（非目录或无 plugin.json）: %s", override)

    dev_first = (os.environ.get("JACHIN_DEV_HR_FIRST") or "").strip().lower() in ("1", "true", "yes", "on")
    if dev_first:
        dd = _hr_dev_plugin_dir()
        if dd:
            logger.info("[HR Loader] JACHIN_DEV_HR_FIRST=1，优先使用仓库内 HR 包: %s", dd)
            return dd
        logger.warning(
            "[HR Loader] JACHIN_DEV_HR_FIRST=1 但未找到 skills_repo/plugin/%s（请设置 JACHIN_APP_ROOT 指向项目根）",
            target_id,
        )

    # 1. 精确路径（L2 用 plugin id 作 item_id 时）
    cache_dir = cache_base / target_id
    if cache_dir.exists() and (cache_dir / "plugin.json").exists():
        logger.debug("[HR Loader] 使用 l3_mcp_cache 精确路径: %s", cache_dir)
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
                    logger.debug("[HR Loader] 使用 l3_mcp_cache 扫描命中: %s", sub)
                    return sub
            except Exception:
                pass

    # 3. 开发兜底：skills_repo
    dd = _hr_dev_plugin_dir()
    if dd:
        logger.debug("[HR Loader] 使用 skills_repo 兜底: %s", dd)
        return dd
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


def hr_delete_all_hr_recruitment_workspace_dirs() -> tuple[int, list[str]]:
    """删除 hr_recruitment 数据根下所有岗位子目录（与飞书「清除全部岗位记忆」配合）。"""
    hr_root = _get_hr_recruitment_plugin_root()
    if not hr_root or not hr_root.exists():
        return 0, []
    cache_str = str(hr_root.resolve())
    prev = sys.path.copy()
    try:
        if cache_str not in sys.path:
            sys.path.insert(0, cache_str)
        from tools.hr_data_paths import delete_all_hr_position_directories

        return delete_all_hr_position_directories()
    except Exception as e:
        logger.warning("[HR Loader] hr_delete_all_hr_recruitment_workspace_dirs: %s", e)
        return 0, []
    finally:
        sys.path = prev


def hr_set_all_jd_show_in_hr_briefing(show: bool) -> int:
    """扫描 hr_recruitment 下各 jd.json，批量设置 ``show_in_hr_briefing``。返回成功写入的文件数。"""
    hr_root = _get_hr_recruitment_plugin_root()
    if not hr_root or not hr_root.exists():
        return 0
    cache_str = str(hr_root.resolve())
    prev = sys.path.copy()
    try:
        if cache_str not in sys.path:
            sys.path.insert(0, cache_str)
        from tools.hr_data_paths import set_all_jd_show_in_hr_briefing

        return int(set_all_jd_show_in_hr_briefing(show))
    except Exception as e:
        logger.debug("[HR Loader] hr_set_all_jd_show_in_hr_briefing(%s): %s", show, e)
        return 0
    finally:
        sys.path = prev


def hr_set_jd_show_in_hr_briefing_for_folder(job_folder: str, show: bool) -> bool:
    """
    写入 ``hr_recruitment/{folder}/jd.json`` 的 ``show_in_hr_briefing``（飞书简报是否列出该岗）。
    绑定/换岗成功时可传 ``show=True`` 恢复展示。
    """
    hr_root = _get_hr_recruitment_plugin_root()
    if not hr_root or not hr_root.exists():
        return False
    cache_str = str(hr_root.resolve())
    prev = sys.path.copy()
    try:
        if cache_str not in sys.path:
            sys.path.insert(0, cache_str)
        from tools.hr_data_paths import set_jd_show_in_hr_briefing_for_job_folder

        return set_jd_show_in_hr_briefing_for_job_folder(job_folder, show)
    except Exception as e:
        logger.debug("[HR Loader] hr_set_jd_show_in_hr_briefing_for_folder(%r, %s): %s", job_folder, show, e)
        return False
    finally:
        sys.path = prev
