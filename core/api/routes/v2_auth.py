"""
Jachin Nexus V2 - L2 控制面认证与密钥 API

POST /api/v2/auth/sync: L3 携带设备指纹和公钥注册
GET /api/v2/keys: 根据 sub_account_id 返回使用请求者公钥加密的 API Key 列表
POST /api/v2/auth/check: L3 执行前校验子账号权限
L2 不代理大模型推理请求。
"""
from __future__ import annotations

import json
import logging
import secrets
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Header, Request

from core.db import get_connection
from core.errors import (
    ERR_AUTH_001,
    ERR_AUTH_003,
    ERR_AUTH_004,
    ERR_BAD_REQUEST_001,
    ERR_BAD_REQUEST_002,
    ERR_BAD_REQUEST_003,
    ERR_INTERNAL_001,
    ERR_NOT_FOUND_001,
    ERR_NOT_FOUND_002,
    api_error,
)
from core.permissions import (
    ACTION_COORDINATE,
    ACTION_KEYS_READ,
    ACTION_MEMORY_READ,
    ACTION_MEMORY_WRITE,
    get_permissions,
    normalize_permissions_for_l3,
    verify_permissions,
)
from core.security.crypto_manager import decrypt_from_storage, encrypt_for_l3

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2", tags=["v2-auth"])


def _get_sub_account_from_request(request: Request) -> Optional[str]:
    """从请求头或 Cookie 获取 sub_account_id（简化实现，实际可接入 JWT）"""
    auth = request.headers.get("Authorization") or request.headers.get("X-Sub-Account-Id")
    if auth and auth.startswith("Bearer "):
        # 简化：Bearer 后直接为 sub_account_id，生产应解析 JWT
        return auth[7:].strip() or None
    if auth:
        return auth.strip() or None
    return request.headers.get("X-Sub-Account-Id")


@router.post("/auth/sync")
async def auth_sync(request: Request) -> dict[str, Any]:
    """
    L3 节点注册/同步。
    携带 device_fingerprint 和 public_key_pem，L2 登记或更新 L3 节点。
    """
    try:
        body = await request.json()
    except Exception:
        raise api_error(400, ERR_BAD_REQUEST_001, "Invalid JSON")

    device_fingerprint = body.get("device_fingerprint") or ""
    public_key_pem = body.get("public_key_pem")
    capabilities = body.get("capabilities") or []
    trust_zone = body.get("trust_zone") or ""
    display_name = (body.get("display_name") or "").strip()[:64]  # 用户自定义设备名，便于 L2 审批识别

    if not public_key_pem or not isinstance(public_key_pem, str):
        raise api_error(400, ERR_BAD_REQUEST_002, "public_key_pem is required")

    node_id = body.get("node_id") or f"l3-{secrets.token_hex(8)}"
    caps = capabilities if isinstance(capabilities, dict) else {}
    if isinstance(capabilities, list):
        caps = {"skills": capabilities}
    if trust_zone and "trust_zone" not in caps:
        caps["trust_zone"] = trust_zone
    caps_json = json.dumps(caps, ensure_ascii=False)

    # 安全：sub_account_id 仅能由管理员通过 POST /admin/nodes/assign 设置
    # 客户端提供的 sub_account_id 一律忽略，防止未审批即可使用
    sub_account_id = None

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO l3_nodes (id, device_fingerprint, public_key_pem, sub_account_id, capabilities_json, trust_zone, display_name, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, strftime('%s', 'now'))
            ON CONFLICT(id) DO UPDATE SET
                device_fingerprint = excluded.device_fingerprint,
                public_key_pem = excluded.public_key_pem,
                capabilities_json = excluded.capabilities_json,
                trust_zone = CASE WHEN excluded.trust_zone != '' THEN excluded.trust_zone ELSE trust_zone END,
                display_name = CASE WHEN excluded.display_name != '' THEN excluded.display_name ELSE display_name END,
                last_seen_at = strftime('%s', 'now')
            """,
            (node_id, device_fingerprint, public_key_pem, sub_account_id, caps_json, trust_zone, display_name),
        )
        conn.commit()
    except Exception as e:
        logger.exception("[v2/auth/sync] DB error: %s", e)
        raise api_error(500, ERR_INTERNAL_001, "Registration failed", detail=str(e))
    finally:
        conn.close()

    return {
        "node_id": node_id,
        "status": "registered",
        "message": "L3 node registered. Use GET /api/v2/keys to fetch encrypted API keys.",
    }


@router.post("/auth/check")
async def auth_check(request: Request) -> dict[str, Any]:
    """
    L3 执行前校验子账号权限。
    body: { "action": "memory:read" | "memory:write" | "coordinate:task" | "keys:read", "node_id": "可选" }
    返回: { "allowed": true|false, "message": "拒绝原因" }
    """
    sub_account_id = _get_sub_account_from_request(request)
    if not sub_account_id:
        raise api_error(401, ERR_AUTH_001, "X-Sub-Account-Id or Authorization required")

    try:
        body = await request.json() or {}
    except Exception:
        raise api_error(400, ERR_BAD_REQUEST_001, "Invalid JSON")

    action = body.get("action") or ""
    node_id = body.get("node_id")

    if not action:
        raise api_error(400, ERR_BAD_REQUEST_002, "action is required")

    valid_actions = (ACTION_MEMORY_READ, ACTION_MEMORY_WRITE, ACTION_COORDINATE, ACTION_KEYS_READ)
    if action not in valid_actions:
        raise api_error(400, ERR_BAD_REQUEST_003, f"action must be one of: {valid_actions}")

    conn = get_connection()
    try:
        perm_row = conn.execute(
            "SELECT permissions_json FROM sub_accounts WHERE id = ?",
            (sub_account_id,),
        ).fetchone()
        if not perm_row:
            raise api_error(404, ERR_NOT_FOUND_001, "Sub-account not found")
        perms = get_permissions(conn, sub_account_id)
    finally:
        conn.close()

    allowed, message = verify_permissions(perms, action, node_id=node_id)
    return {"allowed": allowed, "message": message or ""}


@router.get("/keys")
async def get_keys(
    request: Request,
    node_id: Optional[str] = None,
) -> dict[str, Any]:
    """
    根据 sub_account_id 和 node_id，返回使用该 L3 节点公钥加密的 API Key 列表。
    请求需携带 X-Sub-Account-Id 或 Authorization: Bearer <sub_account_id>。
    """
    sub_account_id = _get_sub_account_from_request(request)
    if not sub_account_id:
        raise api_error(401, ERR_AUTH_001, "X-Sub-Account-Id or Authorization required")

    if not node_id:
        raise api_error(400, ERR_BAD_REQUEST_002, "node_id is required")

    conn = get_connection()
    try:
        # 获取 L3 节点公钥与单节点配置（api_key_ids 为空则返回子账号全部 Key）
        row = conn.execute(
            "SELECT public_key_pem, sub_account_id, model_endpoints, api_key_ids FROM l3_nodes WHERE id = ?",
            (node_id,),
        ).fetchone()
        if not row:
            raise api_error(404, ERR_NOT_FOUND_002, "L3 node not found")

        l3_sub = row["sub_account_id"]
        model_endpoints_raw = row["model_endpoints"] or "{}"
        if l3_sub and l3_sub != sub_account_id:
            raise api_error(403, ERR_AUTH_004, "Node not assigned to this sub-account")

        public_key_pem = row["public_key_pem"]

        # 获取 sub_account 权限并校验 keys:read
        perm_row = conn.execute(
            "SELECT id FROM sub_accounts WHERE id = ?",
            (sub_account_id,),
        ).fetchone()
        if not perm_row:
            raise api_error(404, ERR_NOT_FOUND_001, "Sub-account not found")
        perms = get_permissions(conn, sub_account_id)
        allowed, msg = verify_permissions(perms, ACTION_KEYS_READ, node_id=node_id)
        if not allowed:
            raise api_error(403, ERR_AUTH_003, msg or "无 API Key 读取权限")

        # 获取该 sub_account 的 API Key；若节点有 api_key_ids 则仅返回该节点专属 Key
        api_key_ids_raw = row["api_key_ids"] or "[]"
        try:
            node_key_ids = json.loads(api_key_ids_raw)
            if isinstance(node_key_ids, list) and len(node_key_ids) > 0:
                placeholders = ",".join("?" * len(node_key_ids))
                rows = conn.execute(
                    f"SELECT id, provider, encrypted_key FROM api_keys_vault WHERE sub_account_id = ? AND id IN ({placeholders})",
                    (sub_account_id, *node_key_ids),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, provider, encrypted_key FROM api_keys_vault WHERE sub_account_id = ?",
                    (sub_account_id,),
                ).fetchall()
        except (json.JSONDecodeError, TypeError):
            rows = conn.execute(
                "SELECT id, provider, encrypted_key FROM api_keys_vault WHERE sub_account_id = ?",
                (sub_account_id,),
            ).fetchall()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[v2/keys] DB error: %s", e)
        raise api_error(500, ERR_INTERNAL_001, "Failed to fetch keys", detail=str(e))
    finally:
        conn.close()

    encrypted_keys = []
    for r in rows:
        try:
            plain = decrypt_from_storage(r["encrypted_key"])
            enc_for_l3 = encrypt_for_l3(plain, public_key_pem)
            encrypted_keys.append(
                {
                    "id": r["id"],
                    "provider": r["provider"],
                    "encrypted_key": enc_for_l3,
                }
            )
        except Exception as e:
            logger.warning("[v2/keys] Failed to encrypt key %s for L3: %s", r["id"], e)
            continue

    perms_l3 = normalize_permissions_for_l3(perms)
    try:
        model_endpoints = json.loads(model_endpoints_raw) if model_endpoints_raw else {}
    except json.JSONDecodeError:
        model_endpoints = {}
    return {
        "encrypted_api_keys": encrypted_keys,
        "sub_account_id": sub_account_id,
        "node_id": node_id,
        "permissions_snapshot": perms_l3,
        "model_endpoints": model_endpoints,
    }


@router.get("/auth/poll")
async def auth_poll(node_id: Optional[str] = None) -> dict[str, Any]:
    """
    L3 节点轮询审批状态（无需 sub_account_id）。
    待审批时返回 status=pending；管理员分配子账号后返回 status=approved 及加密 Key。
    """
    if not node_id:
        raise api_error(400, ERR_BAD_REQUEST_002, "node_id is required")

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT public_key_pem, sub_account_id, model_endpoints FROM l3_nodes WHERE id = ?",
            (node_id,),
        ).fetchone()
        if not row:
            raise api_error(404, ERR_NOT_FOUND_002, "L3 node not found")

        public_key_pem = row["public_key_pem"]
        sub_account_id = row["sub_account_id"]
        model_endpoints_raw = row["model_endpoints"] or "{}"

        if not sub_account_id:
            conn.execute(
                "UPDATE l3_nodes SET last_seen_at = strftime('%s', 'now') WHERE id = ?",
                (node_id,),
            )
            conn.commit()
            return {"status": "pending", "message": "Waiting for L2 admin to assign sub-account"}

        # 已分配，更新心跳并获取 permissions_json
        conn.execute(
            "UPDATE l3_nodes SET last_seen_at = strftime('%s', 'now') WHERE id = ?",
            (node_id,),
        )
        conn.commit()

        # 规范化为 L3 零信任快照
        perms_raw = get_permissions(conn, sub_account_id)
        permissions_snapshot = normalize_permissions_for_l3(perms_raw)

        # 获取该 sub_account 的 API Key 并用 L3 公钥加密
        rows = conn.execute(
            "SELECT id, provider, encrypted_key FROM api_keys_vault WHERE sub_account_id = ?",
            (sub_account_id,),
        ).fetchall()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[v2/auth/poll] DB error: %s", e)
        raise api_error(500, ERR_INTERNAL_001, "Failed to fetch keys", detail=str(e))
    finally:
        conn.close()

    encrypted_keys = []
    for r in rows:
        try:
            plain = decrypt_from_storage(r["encrypted_key"])
            enc_for_l3 = encrypt_for_l3(plain, public_key_pem)
            encrypted_keys.append(
                {"id": r["id"], "provider": r["provider"], "encrypted_key": enc_for_l3}
            )
        except Exception as e:
            logger.warning("[v2/auth/poll] Failed to encrypt key %s for L3: %s", r["id"], e)
            continue

    try:
        model_endpoints = json.loads(model_endpoints_raw) if model_endpoints_raw else {}
    except json.JSONDecodeError:
        model_endpoints = {}
    return {
        "status": "approved",
        "node_id": node_id,
        "sub_account_id": sub_account_id,
        "encrypted_api_keys": encrypted_keys,
        "permissions_snapshot": permissions_snapshot,
        "model_endpoints": model_endpoints,
    }
