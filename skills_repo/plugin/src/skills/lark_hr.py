"""Lark 多维表单 - 面试流程 Track"""
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)


async def sync_interview_track(
    sheet_token: str = "",
    passed_candidates: list = None,
) -> Dict[str, Any]:
    """
    将筛选通过的候选人同步到 Lark 多维表单，用于面试流程跟踪。
    需配置 Lark API 凭证。
    """
    if not sheet_token:
        return {"success": False, "error": "sheet_token 为空"}
    # 雏形：实际需调用 Lark bitable API
    return {
        "success": True,
        "message": "[雏形] 需配置 Lark app_id/app_secret 后接入多维表格 API",
        "passed_count": len(passed_candidates or []),
    }
