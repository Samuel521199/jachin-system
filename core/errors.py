"""
Jachin Nexus V2 - L2 网关统一错误码体系（K8s Ready）

便于 Prometheus、统一日志中心采集与告警。
格式: { "code": "ERR_AUTH_001", "message": "人类可读", "detail": "可选补充" }
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException


# -----------------------------------------------------------------------------
# 错误码定义（按模块分组）
# -----------------------------------------------------------------------------
# 认证/授权 (AUTH)
ERR_AUTH_001 = "ERR_AUTH_001"  # 缺少 X-Sub-Account-Id 或 Authorization
ERR_AUTH_002 = "ERR_AUTH_002"  # 子账号不存在
ERR_AUTH_003 = "ERR_AUTH_003"  # 权限不足
ERR_AUTH_004 = "ERR_AUTH_004"  # 节点未分配至该子账号
ERR_AUTH_005 = "ERR_AUTH_005"  # 管理员登录失败

# 调度 (SCHEDULER)
ERR_SCHEDULER_001 = "ERR_SCHEDULER_001"  # 调度失败（无可用节点等）
ERR_SCHEDULER_002 = "ERR_SCHEDULER_002"  # 需要 GPU 但无可用节点
ERR_SCHEDULER_003 = "ERR_SCHEDULER_003"  # 子任务/任务不存在或归属不匹配
ERR_QUOTA_001 = "ERR_QUOTA_001"  # 资源配额超限（存储/任务数）

# 请求格式 (BAD_REQUEST)
ERR_BAD_REQUEST_001 = "ERR_BAD_REQUEST_001"  # Invalid JSON
ERR_BAD_REQUEST_002 = "ERR_BAD_REQUEST_002"  # 缺少必填参数
ERR_BAD_REQUEST_003 = "ERR_BAD_REQUEST_003"  # 参数格式错误

# 资源 (NOT_FOUND)
ERR_NOT_FOUND_001 = "ERR_NOT_FOUND_001"  # 子账号不存在
ERR_NOT_FOUND_002 = "ERR_NOT_FOUND_002"  # L3 节点不存在
ERR_NOT_FOUND_003 = "ERR_NOT_FOUND_003"  # 任务/子任务不存在

# 内部 (INTERNAL)
ERR_INTERNAL_001 = "ERR_INTERNAL_001"  # 数据库/内部错误

# MCP (Model Context Protocol)
ERR_MCP_001 = "ERR_MCP_001"  # 工具未找到
ERR_MCP_002 = "ERR_MCP_002"  # MCP 连接/执行超时或失败


def api_error(
    status_code: int,
    code: str,
    message: str,
    *,
    detail: Optional[str] = None,
) -> HTTPException:
    """构造统一格式的 API 错误响应"""
    body: dict[str, Any] = {"code": code, "message": message}
    if detail:
        body["detail"] = detail
    return HTTPException(status_code=status_code, detail=body)
