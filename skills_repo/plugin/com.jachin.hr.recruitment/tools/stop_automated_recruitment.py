"""
MCP 工具：停止无人值守招聘流程。
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


def stop_automated_recruitment(job_name: str = "") -> str:
    """
    停止无人值守招聘。job_name 为空则停止所有岗位。
    委托 l3_node.recruitment_scheduler。
    """
    try:
        from recruitment_scheduler import remove_scheduled_job, list_scheduled_jobs, set_recruitment_stopped

        jn = (job_name or "").strip()
        if jn:
            result = remove_scheduled_job(jn)
            return json.dumps(result, ensure_ascii=False)
        set_recruitment_stopped(True)
        jobs = list_scheduled_jobs()
        removed = []
        for j in jobs:
            folder = (j.get("job_folder") or "").strip()
            if folder:
                r = remove_scheduled_job(folder)
                if r.get("ok"):
                    removed.extend(r.get("removed", []))
        return json.dumps({"ok": True, "message": "已停止所有无人值守招聘任务", "removed": removed}, ensure_ascii=False)
    except ImportError as e:
        logger.warning("recruitment_scheduler 未加载: %s", e)
        return json.dumps({"ok": False, "error": f"调度器不可用: {e}"}, ensure_ascii=False)
    except Exception as e:
        logger.warning("stop_automated_recruitment 失败: %s", e)
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
