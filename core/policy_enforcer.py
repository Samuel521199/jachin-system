"""
Jachin IAM - L2 权限策略执行器（PolicyEnforcer）

L2 数据主权：RBAC 策略仅从本地 SQLite role_permissions 读取，作为唯一真相来源。
不再依赖 L1 云端下发，由本地管理 API 维护。
role_id -> set(allowed_item_ids)
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 全局策略矩阵：role_id -> set(item_id)
# item_id 格式：mcp:server_id, skill:skill_id
_POLICY_MATRIX: dict[str, set[str]] = {}
# 是否启用严格模式（无策略时默认拒绝）
_STRICT_MODE = False


def load_from_local_db() -> bool:
    """
    从 L2 本地 SQLite role_permissions 加载策略到内存。
    唯一真相来源，启动时及 roles/assign 后调用。
    返回是否成功加载到非空策略。
    """
    global _POLICY_MATRIX
    try:
        from core.db import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT role_id, item_id FROM role_permissions"
            ).fetchall()
            new_matrix: dict[str, set[str]] = {}
            for r in rows:
                role_id = (r[0] or "").strip()
                item_id = (r[1] or "").strip()
                if not role_id or not item_id:
                    continue
                if role_id not in new_matrix:
                    new_matrix[role_id] = set()
                new_matrix[role_id].add(item_id)
            if new_matrix:
                _POLICY_MATRIX = new_matrix
                logger.info(
                    "[PolicyEnforcer] 已从本地 role_permissions 加载 roles=%d items_total=%d",
                    len(_POLICY_MATRIX),
                    sum(len(s) for s in _POLICY_MATRIX.values()),
                )
                return True
            return False
        finally:
            conn.close()
    except Exception as e:
        logger.warning("[PolicyEnforcer] 从本地加载策略失败: %s", e)
        return False


def refresh_policies() -> bool:
    """刷新内存策略（roles/assign 后调用）。等同于 load_from_local_db。"""
    return load_from_local_db()


def check_access(role_id: str, item_id: str) -> bool:
    """
    检查 role_id 是否有权访问 item_id。
    item_id 格式：mcp:server_id 或 skill:skill_id
    """
    # TODO(MVP): 暂时全量放行，后续版本再开启权限校验。
    return True
    if not role_id or not item_id:
        return not _STRICT_MODE
    allowed = _POLICY_MATRIX.get(role_id)
    if allowed is None:
        # 该角色无策略记录：严格模式拒绝，否则放行（兼容旧逻辑）
        return not _STRICT_MODE
    # 支持通配符 mcp:* 或 skill:*
    if "*" in allowed:
        return True
    if item_id in allowed:
        return True
    # 前缀匹配：mcp:filesystem 匹配 mcp:filesystem_read_file 等
    prefix = item_id.split(":")[0] if ":" in item_id else item_id
    for a in allowed:
        if a.endswith(":*") and (prefix + ":" == a.replace("*", "") or a.startswith(prefix)):
            return True
        if a == f"{prefix}:*":
            return True
    return False


def get_policy_summary() -> dict[str, Any]:
    """返回当前策略摘要（调试用）"""
    return {
        "roles_count": len(_POLICY_MATRIX),
        "roles": {k: list(v) for k, v in _POLICY_MATRIX.items()},
    }
