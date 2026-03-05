"""
Jachin Nexus V2 - L2 控制面管理 API（内部/Admin）

用于创建子账号、向保险箱写入 API Key。
受 X-Admin-Token 保护，Token 从环境变量 JACHIN_L2_ADMIN_TOKEN 读取。
"""
from __future__ import annotations

import json
import logging
import os
import secrets
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request

from core.db import get_connection
from core.security.crypto_manager import encrypt_for_storage, hash_key_for_audit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/admin", tags=["v2-admin"])
_ADMIN_TOKEN_ENV = "JACHIN_L2_ADMIN_TOKEN"


def _verify_admin(request: Request) -> None:
    token = os.environ.get(_ADMIN_TOKEN_ENV)
    if not token:
        raise HTTPException(status_code=503, detail="Admin API disabled: JACHIN_L2_ADMIN_TOKEN not set")
    provided = request.headers.get("X-Admin-Token")
    if provided != token:
        raise HTTPException(status_code=403, detail="Invalid admin token")


def _is_localhost(request: Request) -> bool:
    """仅 localhost 可获取 token，避免泄露"""
    client = getattr(request, "client", None)
    if client:
        host = client.host if hasattr(client, "host") else str(client)
        if host in ("127.0.0.1", "localhost", "::1"):
            return True
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded and forwarded.split(",")[0].strip() in ("127.0.0.1", "localhost"):
        return True
    return False


@router.get("/local-token")
async def get_local_token(request: Request) -> dict[str, str]:
    """
    本地开发：仅当请求来自 localhost 时返回 token，供 Admin 面板自动绑定。
    生产环境应禁用或限制此接口。
    """
    token = os.environ.get(_ADMIN_TOKEN_ENV)
    if not token:
        raise HTTPException(status_code=503, detail="Admin API disabled: JACHIN_L2_ADMIN_TOKEN not set")
    if not _is_localhost(request):
        raise HTTPException(status_code=403, detail="Only localhost can access local-token")
    return {"token": token}


@router.get("/sub-accounts")
async def list_sub_accounts(request: Request) -> list[dict[str, Any]]:
    """返回所有子账号列表。"""
    _verify_admin(request)
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, main_user_id, name, role, permissions_json, l1_pairing_code, created_at FROM sub_accounts ORDER BY created_at DESC"
        ).fetchall()
        return [
            {
                "id": r[0],
                "main_user_id": r[1],
                "name": r[2],
                "role": r[3],
                "permissions_json": r[4],
                "l1_pairing_code": r[5],
                "created_at": r[6],
            }
            for r in rows
        ]
    finally:
        conn.close()


@router.get("/nodes")
async def list_nodes(request: Request) -> list[dict[str, Any]]:
    """返回所有 L3 节点列表（含 node_id、status/sub_account_id 等）。"""
    _verify_admin(request)
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT id, device_fingerprint, sub_account_id, capabilities_json, last_seen_at, created_at
            FROM l3_nodes ORDER BY created_at DESC
            """
        ).fetchall()
        return [
            {
                "id": r[0],
                "device_fingerprint": r[1],
                "sub_account_id": r[2],
                "capabilities_json": r[3],
                "last_seen_at": r[4],
                "created_at": r[5],
            }
            for r in rows
        ]
    finally:
        conn.close()


@router.post("/sub-accounts")
async def create_sub_account(request: Request) -> dict[str, Any]:
    """创建子账号。body: main_user_id, name, role?, permissions_json?"""
    _verify_admin(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    main_user_id = body.get("main_user_id")
    name = body.get("name")
    if not main_user_id or not name:
        raise HTTPException(status_code=400, detail="main_user_id and name are required")

    role = body.get("role") or "member"
    perms = body.get("permissions_json")
    if perms is None:
        perms = {}
    if isinstance(perms, dict):
        perms = json.dumps(perms)
    elif isinstance(perms, str):
        pass
    else:
        perms = "{}"

    sub_id = body.get("id") or f"sub-{secrets.token_hex(8)}"

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO sub_accounts (id, main_user_id, name, role, permissions_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (sub_id, main_user_id, name, role, perms),
        )
        conn.commit()
    except Exception as e:
        logger.exception("[v2/admin] create sub_account: %s", e)
        raise HTTPException(status_code=500, detail="Failed to create sub-account")
    finally:
        conn.close()

    return {"id": sub_id, "main_user_id": main_user_id, "name": name, "role": role}


@router.post("/keys")
async def add_api_key(request: Request) -> dict[str, Any]:
    """
    向保险箱添加 API Key。
    body: sub_account_id, provider (openai|qwen|...), api_key (明文)。
    L2 用 Master Key 加密存储，绝不落盘明文。
    """
    _verify_admin(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    sub_account_id = body.get("sub_account_id")
    provider = body.get("provider")
    api_key = body.get("api_key")
    if not sub_account_id or not provider or not api_key:
        raise HTTPException(status_code=400, detail="sub_account_id, provider, api_key are required")

    key_hash = hash_key_for_audit(api_key)
    encrypted = encrypt_for_storage(api_key)
    key_id = body.get("id") or f"key-{secrets.token_hex(8)}"

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO api_keys_vault (id, sub_account_id, provider, encrypted_key, key_hash)
            VALUES (?, ?, ?, ?, ?)
            """,
            (key_id, sub_account_id, provider, encrypted, key_hash),
        )
        conn.commit()
    except Exception as e:
        logger.exception("[v2/admin] add key: %s", e)
        raise HTTPException(status_code=500, detail="Failed to add API key")
    finally:
        conn.close()

    return {"id": key_id, "sub_account_id": sub_account_id, "provider": provider}


@router.post("/nodes/assign")
async def assign_node_to_sub_account(request: Request) -> dict[str, Any]:
    """
    将 L3 节点分配给子账号。
    body: { node_id, sub_account_id }
    L2 管理员在后台审批后调用此接口。
    """
    _verify_admin(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    node_id = body.get("node_id")
    sub_account_id = body.get("sub_account_id")
    if not node_id or not sub_account_id:
        raise HTTPException(status_code=400, detail="node_id and sub_account_id are required")

    conn = get_connection()
    try:
        # 验证 sub_account 存在
        sub_row = conn.execute(
            "SELECT id FROM sub_accounts WHERE id = ?",
            (sub_account_id,),
        ).fetchone()
        if not sub_row:
            raise HTTPException(status_code=404, detail="Sub-account not found")

        # 验证 node 存在
        node_row = conn.execute(
            "SELECT id FROM l3_nodes WHERE id = ?",
            (node_id,),
        ).fetchone()
        if not node_row:
            raise HTTPException(status_code=404, detail="L3 node not found")

        conn.execute(
            "UPDATE l3_nodes SET sub_account_id = ?, last_seen_at = strftime('%s', 'now') WHERE id = ?",
            (sub_account_id, node_id),
        )
        conn.commit()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[v2/admin] assign node: %s", e)
        raise HTTPException(status_code=500, detail="Failed to assign node")
    finally:
        conn.close()

    return {
        "node_id": node_id,
        "sub_account_id": sub_account_id,
        "message": "Node assigned to sub-account. L3 can now poll GET /api/v2/auth/poll for keys.",
    }
