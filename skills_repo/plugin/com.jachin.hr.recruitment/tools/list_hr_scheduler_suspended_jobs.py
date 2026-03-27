"""
MCP：列出因换岗抢占而挂起、可恢复的招聘无人值守目录键（scheduler_state.json）。
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


def list_hr_scheduler_suspended_jobs() -> str:
    try:
        from recruitment_scheduler import list_scheduler_suspended_jobs

        items = list_scheduler_suspended_jobs()
        return json.dumps({"ok": True, "items": items, "count": len(items)}, ensure_ascii=False)
    except Exception as e:
        logger.warning("list_hr_scheduler_suspended_jobs 失败: %s", e)
        return json.dumps({"ok": False, "error": str(e), "items": []}, ensure_ascii=False)
