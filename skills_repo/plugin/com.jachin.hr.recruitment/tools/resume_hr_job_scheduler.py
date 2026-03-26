"""
MCP：按数据目录键（job_folder）恢复被换岗抢占挂起的无人值守；与当前岗互斥。
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


def resume_hr_job_scheduler(job_folder: str = "", job_name: str = "") -> str:
    jf = (job_folder or "").strip()
    jn = (job_name or "").strip()
    if not jf and not jn:
        return json.dumps(
            {"ok": False, "error": "请至少提供 job_folder（目录键）或 job_name"},
            ensure_ascii=False,
        )
    try:
        from recruitment_scheduler import resume_hr_job_scheduler_for_folder

        out = resume_hr_job_scheduler_for_folder(job_folder=jf, job_name=jn)
        return json.dumps(out if isinstance(out, dict) else {"ok": False, "error": str(out)}, ensure_ascii=False)
    except Exception as e:
        logger.warning("resume_hr_job_scheduler 失败: %s", e)
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
