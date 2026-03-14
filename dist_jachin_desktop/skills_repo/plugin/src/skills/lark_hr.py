"""Lark 多维表单 - 面试流程 Track"""
import logging
from pathlib import Path
from typing import Dict, Any

logger = logging.getLogger(__name__)

# 冲突策略：同步到 Lark 时，新数据优先，以更新覆盖为主（upsert/overwrite）
LARK_CONFLICT_POLICY = "new_wins"


async def sync_interview_track(
    sheet_token: str = "",
    passed_candidates: list = None,
    conflict_policy: str = "new_wins",
) -> Dict[str, Any]:
    """
    将筛选通过的候选人同步到 Lark 多维表单，用于面试流程跟踪。
    需配置 Lark API 凭证。

    冲突策略：默认 new_wins，即新数据优先，有冲突时以本次同步数据覆盖 Lark 中已有记录。
    """
    if not sheet_token:
        return {"success": False, "error": "sheet_token 为空"}
    # 雏形：实际需调用 Lark bitable API；实现时按 conflict_policy 做 upsert（新覆盖旧）
    policy = conflict_policy or LARK_CONFLICT_POLICY
    return {
        "success": True,
        "message": "[雏形] 需配置 Lark app_id/app_secret 后接入多维表格 API",
        "passed_count": len(passed_candidates or []),
        "conflict_policy": policy,
    }


async def sync_summary_md_to_lark(
    summary_md_path: str | Path = "",
    sheet_token: str = "",
    job_name: str = "",
    conflict_policy: str = "new_wins",
) -> Dict[str, Any]:
    """
    将岗位排行榜 Summary MD 同步到 Lark 多维表。
    每个职位在 Lark 中对应一条或一组记录，每次同步以新数据覆盖旧数据（conflict_policy=new_wins）。
    """
    if not sheet_token:
        return {"success": False, "error": "sheet_token 为空"}
    path = Path(summary_md_path) if summary_md_path else None
    if path and path.exists():
        content = path.read_text(encoding="utf-8")
    else:
        content = ""
    # 雏形：调用 bitable API 写入/更新；新建时插入，已存在则按 job_name 更新（新覆盖旧）
    policy = conflict_policy or LARK_CONFLICT_POLICY
    return {
        "success": True,
        "message": "[雏形] 需接入 Lark bitable API，按 new_wins 策略更新岗位排行榜",
        "job_name": job_name,
        "content_len": len(content),
        "conflict_policy": policy,
    }
