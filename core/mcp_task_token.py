"""
MCP 跨节点委托 Task Token（L2 签发、执行端 L3 校验）。

绑定 task_id + tool_name + executor_node_id + sub_account_id，短期 TTL；
避免在委托载荷中依赖「任意长期用户 JWT」。规格见 docs/ARCHITECTURE_L3_MCP_HOST_AND_L2_TASK_MANAGER.md §3.9。

双方需配置相同密钥：环境变量 ``JACHIN_MCP_TASK_TOKEN_SECRET``（推荐）。
未设置时回退到 ``JWT_SECRET|jachin-mcp-task-v1`` 派生（单机开发）；仍不安全于生产，会打 warning。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

_TOKEN_VERSION = 1
_DEFAULT_TTL_SEC = 120


def _secret_bytes() -> bytes:
    raw = (os.environ.get("JACHIN_MCP_TASK_TOKEN_SECRET") or "").strip()
    if raw:
        return raw.encode("utf-8")
    j = (os.environ.get("JWT_SECRET") or os.environ.get("JACHIN_JWT_SECRET") or "").strip()
    if j and j not in ("your-jwt-secret-change-in-production", "your-secret-key-change-in-production"):
        derived = hashlib.sha256((j + "|jachin-mcp-task-v1").encode("utf-8")).hexdigest()
        return derived.encode("utf-8")
    logger.warning(
        "[McpTaskToken] 使用内置弱密钥；生产环境请设置 JACHIN_MCP_TASK_TOKEN_SECRET"
    )
    return b"JACHIN_DEV_INSECURE_MCP_TASK_TOKEN_V1"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def mint_mcp_delegate_task_token(
    *,
    task_id: str,
    tool_name: str,
    executor_node_id: str,
    sub_account_id: str,
    ttl_sec: int = _DEFAULT_TTL_SEC,
) -> str:
    """签发委托令牌（L2 调用）。"""
    now = int(time.time())
    payload: dict[str, Any] = {
        "v": _TOKEN_VERSION,
        "tid": task_id,
        "tool": (tool_name or "").strip().replace("mcp:", "", 1).strip(),
        "nid": (executor_node_id or "").strip(),
        "sub": (sub_account_id or "").strip(),
        "iat": now,
        "exp": now + max(30, min(ttl_sec, 600)),
    }
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    sig = hmac.new(_secret_bytes(), body, hashlib.sha256).digest()
    return f"{_b64url(body)}.{_b64url(sig)}"


def verify_mcp_delegate_task_token(
    token: str,
    *,
    task_id: str,
    tool_name: str,
    executor_node_id: str,
    sub_account_id: str,
) -> tuple[bool, str]:
    """
    校验令牌。返回 (ok, reason)。
    """
    if not token or not isinstance(token, str):
        return False, "missing_token"
    token = token.strip()
    if "." not in token:
        return False, "malformed"
    p_b64, s_b64 = token.split(".", 1)
    try:
        body = _b64url_decode(p_b64)
        sig = _b64url_decode(s_b64)
    except Exception:
        return False, "decode_error"
    expect = hmac.new(_secret_bytes(), body, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expect):
        return False, "bad_signature"
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        return False, "bad_json"
    if not isinstance(payload, dict):
        return False, "bad_payload"
    if int(payload.get("v") or 0) != _TOKEN_VERSION:
        return False, "bad_version"
    now = int(time.time())
    exp = int(payload.get("exp") or 0)
    if exp < now:
        return False, "expired"
    tool_raw = (tool_name or "").strip().replace("mcp:", "", 1).strip()
    if (payload.get("tid") or "") != task_id:
        return False, "task_mismatch"
    if (payload.get("tool") or "").strip() != tool_raw:
        return False, "tool_mismatch"
    if (payload.get("nid") or "").strip() != (executor_node_id or "").strip():
        return False, "node_mismatch"
    if (payload.get("sub") or "").strip() != (sub_account_id or "").strip():
        return False, "sub_mismatch"
    return True, ""
