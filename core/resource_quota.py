"""
Jachin Nexus V2 - 子账号资源配额校验

resource_quota JSON: max_memory_gb, monthly_task_limit
在 coordinate/task 等 API 中拦截校验。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)


def get_resource_quota(conn, sub_account_id: str) -> dict[str, Any]:
    """从 sub_accounts 读取 resource_quota。"""
    row = conn.execute(
        "SELECT resource_quota FROM sub_accounts WHERE id = ?",
        (sub_account_id,),
    ).fetchone()
    if not row or not row.get("resource_quota"):
        return {}
    try:
        return json.loads(row["resource_quota"] or "{}")
    except json.JSONDecodeError:
        return {}


def check_memory_quota(
    conn,
    sub_account_id: str,
    additional_mb: float = 0,
) -> tuple[bool, str]:
    """
    校验记忆存储配额。max_memory_gb 为 0 表示不限制。
    Returns:
        (allowed, message)
    """
    quota = get_resource_quota(conn, sub_account_id)
    max_gb = quota.get("max_memory_gb")
    if max_gb is None or float(max_gb) <= 0:
        return True, ""
    # 简化：查询 memory_fragments 总条数估算（实际可按 content 长度汇总）
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM memory_fragments WHERE sub_account_id = ?",
        (sub_account_id,),
    ).fetchone()
    cnt = row["cnt"] if row else 0
    # 粗略估算：每条约 2KB
    estimated_mb = cnt * 0.002 + additional_mb
    if estimated_mb > float(max_gb) * 1024:
        return False, f"存储配额超限：已用约 {estimated_mb:.1f}MB，上限 {float(max_gb)*1024:.0f}MB"
    return True, ""


def check_task_quota(conn, sub_account_id: str) -> tuple[bool, str]:
    """
    校验月度任务配额。monthly_task_limit 为 0 表示不限制。
    """
    quota = get_resource_quota(conn, sub_account_id)
    limit = quota.get("monthly_task_limit")
    if limit is None or int(limit) <= 0:
        return True, ""
    now = time.time()
    month_start = now - 30 * 86400
    row = conn.execute(
        """
        SELECT COUNT(*) as cnt FROM coordinate_tasks
        WHERE sub_account_id = ? AND created_at >= ?
        """,
        (sub_account_id, month_start),
    ).fetchone()
    cnt = row["cnt"] if row else 0
    if cnt >= int(limit):
        return False, f"月度任务配额已用尽：{cnt}/{limit}"
    return True, ""
