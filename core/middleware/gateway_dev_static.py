"""
L2 /gateway 静态面板：开发时避免 Starlette StaticFiles 对 index.html 返回 304，
刷新始终拉最新（否则日志里常见 304，且强刷偶发仍用缓存）。
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class GatewayStaticNo304Middleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if path.startswith("/gateway"):
            scope = request.scope
            raw = list(scope.get("headers") or [])
            filtered = [
                (n, v)
                for (n, v) in raw
                if n.lower() not in (b"if-none-match", b"if-modified-since")
            ]
            scope["headers"] = filtered

        response = await call_next(request)

        if path.startswith("/gateway"):
            response.headers["Cache-Control"] = (
                "no-store, no-cache, must-revalidate, max-age=0"
            )
            response.headers["Pragma"] = "no-cache"

        return response
