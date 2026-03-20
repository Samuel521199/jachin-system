"""
MCP 工具：将岗位加入无人值守招聘调度引擎。

委托 l3_node.recruitment_scheduler（L3 内置或同包）。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .hr_data_paths import get_job_jd_path, ensure_job_dirs, init_job_jd_from_template

logger = logging.getLogger(__name__)


def add_automated_recruitment_task(
    job_name: str,
    analyze_threshold: int = 4,
    analyze_interval_hours: float = 0.05,
    jd_config_path: str = "",
) -> str:
    """
    将岗位加入无人值守招聘调度引擎。
    委托 l3_node.recruitment_scheduler.add_scheduled_job。
    """
    jd_path = (jd_config_path or "").strip()
    if not jd_path and job_name:
        jd_path = str(get_job_jd_path(job_name))
    if not Path(jd_path).exists() and job_name:
        ensure_job_dirs(job_name)
        init_job_jd_from_template(job_name)
        jd_path = str(get_job_jd_path(job_name))

    try:
        from recruitment_scheduler import add_scheduled_job

        cfg = {
            "job_folder": job_name or Path(jd_path).parent.name if jd_path else "",
            "jd_path": jd_path,
            "analyze_threshold": int(analyze_threshold) if analyze_threshold is not None else 4,
            "analyze_interval_hours": float(analyze_interval_hours) if analyze_interval_hours is not None else 0.05,
            "request_resume": True,
        }
        result = add_scheduled_job(cfg)
        return json.dumps(result, ensure_ascii=False)
    except ImportError as e:
        logger.warning("recruitment_scheduler 未加载: %s", e)
        return json.dumps({"ok": False, "error": f"调度器不可用: {e}"}, ensure_ascii=False)
    except Exception as e:
        logger.warning("add_automated_recruitment_task 失败: %s", e)
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
