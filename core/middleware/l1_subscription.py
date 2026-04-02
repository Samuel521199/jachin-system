"""
Jachin Nexus V2 - L1 订阅状态鉴权中间件

当 L1 下发欠费状态时，挂起该网关下所有 L3 节点请求，返回 402 Payment Required。
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


# 欠费时仍允许访问的路径（健康检查、管理后台、文档）
_ALLOWED_WHEN_EXPIRED = (
    "/health",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/",
    "/favicon.ico",
    "/test",
    "/routes",
    "/static",
)
_ALLOWED_PREFIX = "/api/v2/admin"  # 管理后台可继续访问


class L1SubscriptionMiddleware(BaseHTTPMiddleware):
    """L1 订阅欠费时拦截 L3 请求"""

    async def dispatch(self, request: Request, call_next):
        from core.l1_policy import is_subscription_expired

        if not is_subscription_expired():
            return await call_next(request)

        path = request.url.path
        if path in _ALLOWED_WHEN_EXPIRED or path.startswith(_ALLOWED_PREFIX):
            return await call_next(request)

        return JSONResponse(
            status_code=402,
            content={
                "detail": "您的 L1 平台订阅已过期",
                "code": "subscription_expired",
            },
        )
