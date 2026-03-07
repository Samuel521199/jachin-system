"""
Jachin Nexus V2 - L2 控制面管理 API（内部/Admin）

用于创建子账号、向保险箱写入 API Key。
废弃 JACHIN_L2_ADMIN_TOKEN，改为 username/password + JWT 登录。
"""
from __future__ import annotations

import json
import logging
import secrets
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from core.admin_auth import (
    create_admin_token,
    get_current_admin,
    verify_password,
)
from core.db import get_connection
from core.errors import (
    ERR_AUTH_005,
    ERR_BAD_REQUEST_001,
    ERR_BAD_REQUEST_002,
    ERR_INTERNAL_001,
    ERR_NOT_FOUND_001,
    ERR_NOT_FOUND_002,
    api_error,
)
from core.security.crypto_manager import encrypt_for_storage, hash_key_for_audit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/admin", tags=["v2-admin"])


def _load_nexus_config() -> dict:
    """读取 nexus_config.json（L1 配对透传）"""
    from pathlib import Path
    path = Path.home() / ".jachin" / "nexus_config.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


@router.post("/login-with-l1")
async def admin_login_with_l1(request: Request) -> dict[str, Any]:
    """
    L1 授权登录：使用配对时 L1 下发的凭证，换取 L2 JWT。
    需已配对（nexus_config 含 access_token、l1_user_id）。
    实现「云端一套账号，统御所有边缘机房」。
    """
    cfg = _load_nexus_config()
    access_token = cfg.get("access_token") or ""
    l1_user_id = cfg.get("l1_user_id") or cfg.get("instance_id") or ""
    if not access_token or not l1_user_id:
        raise api_error(
            401,
            ERR_AUTH_005,
            "未完成 L1 配对，请先执行 python -m core.cli pair",
        )
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, username, main_user_id, role FROM gateway_admins WHERE main_user_id = ? LIMIT 1",
            (l1_user_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise api_error(
            401,
            ERR_AUTH_005,
            "网关管理员未绑定 L1 用户，请使用本地账号登录",
        )
    admin_id, uname, main_user_id, role = row[0], row[1], row[2], row[3]
    token = create_admin_token(admin_id, uname or "admin", main_user_id, role or "admin")
    import os
    expires_hours = int(os.environ.get("JWT_EXPIRATION_HOURS", "24"))
    return {
        "token": token,
        "expires_in_hours": expires_hours,
        "admin": {
            "id": admin_id,
            "username": uname or "admin",
            "main_user_id": main_user_id,
            "role": role or "admin",
        },
        "source": "l1_pairing",
    }


@router.post("/login")
async def admin_login(request: Request) -> dict[str, Any]:
    """
    网关管理员登录。body: { username, password }
    成功返回 { token, expires_in_hours, admin: { id, username, main_user_id } }
    """
    try:
        body = await request.json()
    except Exception:
        raise api_error(400, ERR_BAD_REQUEST_001, "Invalid JSON")

    username = (body.get("username") or "").strip()
    password = body.get("password") or ""

    if not username or not password:
        raise api_error(400, ERR_BAD_REQUEST_002, "username and password are required")

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, username, password_hash, main_user_id, role FROM gateway_admins WHERE username = ?",
            (username.lower(),),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        raise api_error(401, ERR_AUTH_005, "用户名或密码错误")

    admin_id, uname, pw_hash, main_user_id, role = row[0], row[1], row[2], row[3], row[4]
    if not verify_password(password, pw_hash):
        raise api_error(401, ERR_AUTH_005, "用户名或密码错误")

    token = create_admin_token(admin_id, uname, main_user_id, role or "admin")
    import os
    expires_hours = int(os.environ.get("JWT_EXPIRATION_HOURS", "24"))

    return {
        "token": token,
        "expires_in_hours": expires_hours,
        "admin": {
            "id": admin_id,
            "username": uname,
            "main_user_id": main_user_id,
            "role": role or "admin",
        },
    }


@router.get("/me")
async def admin_me(admin: dict = Depends(get_current_admin)) -> dict[str, Any]:
    """获取当前登录管理员信息（用于前端校验 token 有效性）"""
    return {"admin": admin}


@router.get("/sub-accounts")
async def list_sub_accounts(
    request: Request,
    admin: dict = Depends(get_current_admin),
) -> list[dict[str, Any]]:
    """返回当前管理员 main_user_id 下的子账号列表（租户隔离）"""
    main_user_id = admin.get("main_user_id") or ""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT id, main_user_id, name, role, permissions_json, l1_pairing_code, created_at
            FROM sub_accounts WHERE main_user_id = ? ORDER BY created_at DESC
            """,
            (main_user_id,),
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


_OFFLINE_THRESHOLD_SEC = 300  # 5 分钟内无心跳视为离线


@router.get("/nodes")
async def list_nodes(
    request: Request,
    admin: dict = Depends(get_current_admin),
) -> list[dict[str, Any]]:
    """
    返回 L3 节点列表。
    待审批：仅展示 last_seen_at 在 5 分钟内的节点（超时视为离线，不展示）。
    已分配：展示全部，含 last_seen_at 与 is_online。
    """
    import time
    main_user_id = admin.get("main_user_id") or ""
    now = time.time()
    cutoff = now - _OFFLINE_THRESHOLD_SEC

    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT n.id, n.device_fingerprint, n.sub_account_id, n.capabilities_json, n.model_endpoints, n.last_seen_at, n.created_at, n.display_name
            FROM l3_nodes n
            LEFT JOIN sub_accounts s ON n.sub_account_id = s.id
            WHERE (n.sub_account_id IS NULL AND n.last_seen_at > ?)
               OR (s.main_user_id = ?)
            ORDER BY n.created_at DESC
            """,
            (cutoff, main_user_id),
        ).fetchall()
        result = []
        for r in rows:
            me = r[4] if len(r) > 4 else "{}"
            try:
                model_endpoints = json.loads(me) if me else {}
            except json.JSONDecodeError:
                model_endpoints = {}
            last_seen = r[5]
            last_seen_float = float(last_seen) if last_seen is not None else 0
            is_online = last_seen_float > cutoff
            result.append({
                "id": r[0],
                "device_fingerprint": r[1],
                "sub_account_id": r[2],
                "capabilities_json": r[3],
                "model_endpoints": model_endpoints,
                "last_seen_at": last_seen,
                "created_at": r[6],
                "display_name": r[7] if len(r) > 7 else "",
                "is_online": is_online,
            })
        return result
    finally:
        conn.close()


@router.post("/sub-accounts")
async def create_sub_account(
    request: Request,
    admin: dict = Depends(get_current_admin),
) -> dict[str, Any]:
    """创建子账号。main_user_id 强制为当前登录管理员的 main_user_id。"""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    main_user_id = admin.get("main_user_id") or ""
    if not main_user_id:
        raise api_error(403, ERR_AUTH_005, "管理员无 main_user_id")

    name = body.get("name")
    if not name or not str(name).strip():
        raise api_error(400, ERR_BAD_REQUEST_002, "name is required")

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
        raise api_error(500, ERR_INTERNAL_001, "Failed to create sub-account", detail=str(e))
    finally:
        conn.close()

    return {"id": sub_id, "main_user_id": main_user_id, "name": name, "role": role}


@router.post("/keys")
async def add_api_key(
    request: Request,
    admin: dict = Depends(get_current_admin),
) -> dict[str, Any]:
    """
    向保险箱添加 API Key。
    body: sub_account_id, provider (openai|qwen|...), api_key (明文)。
    仅允许为当前 main_user_id 下的子账号添加。
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    sub_account_id = body.get("sub_account_id")
    provider = body.get("provider")
    api_key = body.get("api_key")
    if not sub_account_id or not provider or not api_key:
        raise api_error(400, ERR_BAD_REQUEST_002, "sub_account_id, provider, api_key are required")

    main_user_id = admin.get("main_user_id") or ""
    conn = get_connection()
    try:
        sub_row = conn.execute(
            "SELECT id, main_user_id FROM sub_accounts WHERE id = ?",
            (sub_account_id,),
        ).fetchone()
        if not sub_row:
            raise api_error(404, ERR_NOT_FOUND_001, "Sub-account not found")
        if sub_row[1] != main_user_id:
            raise api_error(403, ERR_AUTH_005, "无权为该子账号添加 Key")

        key_hash = hash_key_for_audit(api_key)
        encrypted = encrypt_for_storage(api_key)
        key_id = body.get("id") or f"key-{secrets.token_hex(8)}"

        conn.execute(
            """
            INSERT INTO api_keys_vault (id, sub_account_id, provider, encrypted_key, key_hash)
            VALUES (?, ?, ?, ?, ?)
            """,
            (key_id, sub_account_id, provider, encrypted, key_hash),
        )
        conn.commit()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[v2/admin] add key: %s", e)
        raise api_error(500, ERR_INTERNAL_001, "Failed to add API key", detail=str(e))
    finally:
        conn.close()

    return {"id": key_id, "sub_account_id": sub_account_id, "provider": provider}


@router.post("/nodes/assign")
async def assign_node_to_sub_account(
    request: Request,
    admin: dict = Depends(get_current_admin),
) -> dict[str, Any]:
    """
    将 L3 节点分配给子账号。
    body: { node_id, sub_account_id }
    仅允许分配给当前 main_user_id 下的子账号。
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    node_id = body.get("node_id")
    sub_account_id = body.get("sub_account_id")
    model_endpoints = body.get("model_endpoints")
    if not node_id or not sub_account_id:
        raise api_error(400, ERR_BAD_REQUEST_002, "node_id and sub_account_id are required")

    main_user_id = admin.get("main_user_id") or ""
    conn = get_connection()
    try:
        sub_row = conn.execute(
            "SELECT id, main_user_id FROM sub_accounts WHERE id = ?",
            (sub_account_id,),
        ).fetchone()
        if not sub_row:
            raise api_error(404, ERR_NOT_FOUND_001, "Sub-account not found")
        if sub_row[1] != main_user_id:
            raise api_error(403, ERR_AUTH_005, "无权分配至该子账号")

        node_row = conn.execute(
            "SELECT id FROM l3_nodes WHERE id = ?",
            (node_id,),
        ).fetchone()
        if not node_row:
            raise api_error(404, ERR_NOT_FOUND_002, "L3 node not found")

        if model_endpoints is not None:
            me_json = json.dumps(model_endpoints) if isinstance(model_endpoints, dict) else str(model_endpoints or "{}")
            conn.execute(
                "UPDATE l3_nodes SET sub_account_id = ?, model_endpoints = ?, last_seen_at = strftime('%s', 'now') WHERE id = ?",
                (sub_account_id, me_json, node_id),
            )
        else:
            conn.execute(
                "UPDATE l3_nodes SET sub_account_id = ?, last_seen_at = strftime('%s', 'now') WHERE id = ?",
                (sub_account_id, node_id),
            )

        # 若目标子账号无 Key，从同 main_user 下其他子账号复制，确保 L3 能分到 Key
        target_has_keys = conn.execute(
            "SELECT 1 FROM api_keys_vault WHERE sub_account_id = ? LIMIT 1",
            (sub_account_id,),
        ).fetchone()
        logger.info("[v2/admin] nodes/assign L2 分配节点 node_id=%s sub_account_id=%s target_has_keys=%s",
            node_id, sub_account_id[:16] + ("..." if len(sub_account_id) > 16 else ""), bool(target_has_keys))
        if not target_has_keys:
            from core.security.crypto_manager import decrypt_from_storage, encrypt_for_storage

            siblings = conn.execute(
                """
                SELECT k.id, k.provider, k.encrypted_key, k.key_hash FROM api_keys_vault k
                JOIN sub_accounts s ON k.sub_account_id = s.id
                WHERE s.main_user_id = ?
                """,
                (main_user_id,),
            ).fetchall()
            seen_providers = set()
            copied = 0
            for r in siblings:
                if r["provider"] in seen_providers:
                    continue
                seen_providers.add(r["provider"])
                try:
                    plain = decrypt_from_storage(r["encrypted_key"])
                    key_id = f"copy-{r['provider']}-{secrets.token_hex(4)}"
                    enc = encrypt_for_storage(plain)
                    conn.execute(
                        "INSERT INTO api_keys_vault (id, sub_account_id, provider, encrypted_key, key_hash) VALUES (?, ?, ?, ?, ?)",
                        (key_id, sub_account_id, r["provider"], enc, r["key_hash"]),
                    )
                    copied += 1
                except Exception as e:
                    logger.warning("[v2/admin] 复制 Key %s 到子账号 %s 失败: %s", r["id"], sub_account_id, e)
            if copied:
                logger.info("[v2/admin] 子账号 %s 无 Key，已从同 main_user 复制 %d 个 providers=%s", sub_account_id, copied, list(seen_providers))

        conn.commit()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[v2/admin] assign node: %s", e)
        raise api_error(500, ERR_INTERNAL_001, "Failed to assign node", detail=str(e))
    finally:
        conn.close()

    return {
        "node_id": node_id,
        "sub_account_id": sub_account_id,
        "message": "Node assigned to sub-account. L3 can now poll GET /api/v2/auth/poll for keys.",
    }


@router.get("/nodes/stale")
async def list_stale_nodes(
    admin: dict = Depends(get_current_admin),
) -> dict[str, Any]:
    """
    返回未审批的历史节点（sub_account_id 为空），供清理前预览。
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT id, device_fingerprint, last_seen_at, created_at, display_name
            FROM l3_nodes
            WHERE sub_account_id IS NULL
            ORDER BY last_seen_at DESC
            """,
        ).fetchall()
        nodes = []
        for r in rows:
            nodes.append({
                "id": r[0],
                "device_fingerprint": r[1],
                "last_seen_at": r[2],
                "created_at": r[3],
                "display_name": (r[4] or "").strip() if len(r) > 4 else "",
            })
        return {"count": len(nodes), "nodes": nodes}
    finally:
        conn.close()


@router.post("/nodes/cleanup")
async def cleanup_stale_nodes(
    admin: dict = Depends(get_current_admin),
) -> dict[str, Any]:
    """
    清理未审批的历史节点（sub_account_id 为空）。
    返回删除数量。
    """
    conn = get_connection()
    try:
        cur = conn.execute(
            "DELETE FROM l3_nodes WHERE sub_account_id IS NULL",
        )
        deleted = cur.rowcount
        conn.commit()
        logger.info("[v2/admin] cleanup: deleted %d stale nodes", deleted)
        return {"deleted": deleted, "message": f"已清理 {deleted} 个历史节点"}
    finally:
        conn.close()


@router.delete("/nodes/{node_id}")
async def delete_node(
    node_id: str,
    admin: dict = Depends(get_current_admin),
) -> dict[str, Any]:
    """
    删除单个 L3 节点（需管理员权限）。
    """
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM l3_nodes WHERE id = ?", (node_id,))
        if cur.rowcount == 0:
            raise api_error(404, ERR_NOT_FOUND_002, "L3 node not found")
        conn.commit()
        logger.info("[v2/admin] deleted node %s", node_id)
        return {"deleted": node_id, "message": "节点已删除"}
    except HTTPException:
        raise
    finally:
        conn.close()
