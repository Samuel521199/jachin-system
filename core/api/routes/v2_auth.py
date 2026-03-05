"""
Jachin Nexus V2 - L2 控制面认证与密钥 API

POST /api/v2/auth/sync: L3 携带设备指纹和公钥注册
GET /api/v2/keys: 根据 sub_account_id 返回使用请求者公钥加密的 API Key 列表
L2 不代理大模型推理请求。
"""
from __future__ import annotations

import json
import logging
import secrets
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Header, Request

from core.db import get_connection
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
        raise HTTPException(status_code=400, detail="Invalid JSON")

    device_fingerprint = body.get("device_fingerprint") or ""
    public_key_pem = body.get("public_key_pem")
    capabilities = body.get("capabilities") or []

    if not public_key_pem or not isinstance(public_key_pem, str):
        raise HTTPException(status_code=400, detail="public_key_pem is required")

    node_id = body.get("node_id") or f"l3-{secrets.token_hex(8)}"
    caps_json = json.dumps(capabilities) if isinstance(capabilities, list) else "{}"

    # 安全：sub_account_id 仅能由管理员通过 POST /admin/nodes/assign 设置
    # 客户端提供的 sub_account_id 一律忽略，防止未审批即可使用
    sub_account_id = None

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO l3_nodes (id, device_fingerprint, public_key_pem, sub_account_id, capabilities_json, last_seen_at)
            VALUES (?, ?, ?, ?, ?, strftime('%s', 'now'))
            ON CONFLICT(id) DO UPDATE SET
                device_fingerprint = excluded.device_fingerprint,
                public_key_pem = excluded.public_key_pem,
                capabilities_json = excluded.capabilities_json,
                last_seen_at = strftime('%s', 'now')
            """,
            (node_id, device_fingerprint, public_key_pem, sub_account_id, caps_json),
        )
        conn.commit()
    except Exception as e:
        logger.exception("[v2/auth/sync] DB error: %s", e)
        raise HTTPException(status_code=500, detail="Registration failed")
    finally:
        conn.close()

    return {
        "node_id": node_id,
        "status": "registered",
        "message": "L3 node registered. Use GET /api/v2/keys to fetch encrypted API keys.",
    }


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
        raise HTTPException(status_code=401, detail="X-Sub-Account-Id or Authorization required")

    if not node_id:
        raise HTTPException(status_code=400, detail="node_id is required")

    conn = get_connection()
    try:
        # 获取 L3 节点公钥
        row = conn.execute(
            "SELECT public_key_pem, sub_account_id FROM l3_nodes WHERE id = ?",
            (node_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="L3 node not found")

        l3_sub = row["sub_account_id"]
        if l3_sub and l3_sub != sub_account_id:
            raise HTTPException(status_code=403, detail="Node not assigned to this sub-account")

        public_key_pem = row["public_key_pem"]

        # 获取 sub_account 权限
        perm_row = conn.execute(
            "SELECT permissions_json FROM sub_accounts WHERE id = ?",
            (sub_account_id,),
        ).fetchone()
        if not perm_row:
            raise HTTPException(status_code=404, detail="Sub-account not found")

        perms = json.loads(perm_row["permissions_json"] or "{}")

        # 获取该 sub_account 的 API Key（L2 存储为 Master Key 加密）
        rows = conn.execute(
            "SELECT id, provider, encrypted_key FROM api_keys_vault WHERE sub_account_id = ?",
            (sub_account_id,),
        ).fetchall()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[v2/keys] DB error: %s", e)
        raise HTTPException(status_code=500, detail="Failed to fetch keys")
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

    return {
        "encrypted_api_keys": encrypted_keys,
        "sub_account_id": sub_account_id,
        "node_id": node_id,
    }


@router.get("/auth/poll")
async def auth_poll(node_id: Optional[str] = None) -> dict[str, Any]:
    """
    L3 节点轮询审批状态（无需 sub_account_id）。
    待审批时返回 status=pending；管理员分配子账号后返回 status=approved 及加密 Key。
    """
    if not node_id:
        raise HTTPException(status_code=400, detail="node_id is required")

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT public_key_pem, sub_account_id FROM l3_nodes WHERE id = ?",
            (node_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="L3 node not found")

        public_key_pem = row["public_key_pem"]
        sub_account_id = row["sub_account_id"]

        if not sub_account_id:
            return {"status": "pending", "message": "Waiting for L2 admin to assign sub-account"}

        # 已分配，获取该 sub_account 的 API Key 并用 L3 公钥加密
        rows = conn.execute(
            "SELECT id, provider, encrypted_key FROM api_keys_vault WHERE sub_account_id = ?",
            (sub_account_id,),
        ).fetchall()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[v2/auth/poll] DB error: %s", e)
        raise HTTPException(status_code=500, detail="Failed to fetch keys")
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

    return {
        "status": "approved",
        "node_id": node_id,
        "sub_account_id": sub_account_id,
        "encrypted_api_keys": encrypted_keys,
    }
