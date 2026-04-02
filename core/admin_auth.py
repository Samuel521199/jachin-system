"""
Jachin Nexus V2 - L2 网关管理员 JWT 鉴权

废弃 JACHIN_L2_ADMIN_TOKEN 静态鉴权，改为 username/password + JWT。
"""
from __future__ import annotations

import os
import secrets
import time
from typing import Any, Optional

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_JWT_SECRET_CACHE: Optional[str] = None


def _get_jwt_secret() -> str:
    """JWT 密钥，从环境变量读取，无则自动生成并缓存（同进程内一致）"""
    global _JWT_SECRET_CACHE
    if _JWT_SECRET_CACHE is not None:
        return _JWT_SECRET_CACHE
    secret = os.environ.get("JWT_SECRET") or os.environ.get("JACHIN_JWT_SECRET")
    if secret:
        _JWT_SECRET_CACHE = secret
        return secret
    _JWT_SECRET_CACHE = secrets.token_hex(32)
    return _JWT_SECRET_CACHE


def _get_jwt_expiry_hours() -> int:
    return int(os.environ.get("JWT_EXPIRATION_HOURS", "24"))


def create_admin_token(
    admin_id: str,
    username: str,
    main_user_id: str,
    role: str = "admin",
    *,
    workspace_gateway_access: bool = True,
) -> str:
    """签发 Admin JWT。workspace_gateway_access=False 时 /api/v2/admin/* 将 403（预留）。"""
    try:
        import jwt
    except ImportError:
        raise RuntimeError("请安装 PyJWT: pip install PyJWT")
    payload = {
        "sub": admin_id,
        "username": username,
        "main_user_id": main_user_id,
        "role": role,
        "workspace_gateway_access": workspace_gateway_access,
        "exp": int(time.time()) + _get_jwt_expiry_hours() * 3600,
        "iat": int(time.time()),
    }
    return jwt.encode(payload, _get_jwt_secret(), algorithm="HS256")


def decode_admin_token(token: str) -> dict[str, Any]:
    """解码并校验 JWT，失败抛出 HTTPException"""
    try:
        import jwt
    except ImportError:
        raise HTTPException(status_code=503, detail="JWT 模块未安装")
    try:
        payload = jwt.decode(token, _get_jwt_secret(), algorithms=["HS256"])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token 已过期，请重新登录")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token 无效")


def verify_password(plain: str, hashed: str) -> bool:
    """校验密码（使用 bcrypt，兼容 passlib 生成的哈希）"""
    try:
        import bcrypt
        return bcrypt.checkpw(
            plain.encode("utf-8") if isinstance(plain, str) else plain,
            hashed.encode("utf-8") if isinstance(hashed, str) else hashed,
        )
    except Exception:
        return False


security = HTTPBearer(auto_error=False)


async def get_current_admin(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> dict[str, Any]:
    """
    FastAPI 依赖：拦截 /api/v2/admin/* 路由，要求合法 Bearer JWT。
    返回 { id, username, main_user_id, role }。
    """
    token = None
    if credentials and credentials.scheme == "Bearer":
        token = credentials.credentials
    if not token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="需要登录，请携带 Authorization: Bearer <token>")
    payload = decode_admin_token(token)
    wga = payload.get("workspace_gateway_access")
    if wga is False:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "GATEWAY_ACCESS_DENIED",
                "message": "权限不足：当前令牌无权使用 L2 网关控制台。",
            },
        )
    return {
        "id": payload.get("sub", ""),
        "username": payload.get("username", ""),
        "main_user_id": payload.get("main_user_id", ""),
        "role": payload.get("role", "admin"),
    }
