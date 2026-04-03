"""
Jachin Nexus V2 - L2 控制面认证与密钥 API（L2↔L3 零信任）

POST /api/v2/auth/sync: L3 注册；已配对 L2 时 organization_id（或 slug）须落在 sync_tenant_ids。
仅配置**一个**同步租户时，L3 可省略 organization，L2 默认使用该租户（降低首次配对摩擦）。
多租户时 L3 必须显式提供 organization_id 或 organization_slug。配对仅发生在 L2↔L3，不经 L1。
GET /api/v2/keys / auth/poll / auth/check: L3 运行时密钥与权限。
L2 不代理大模型推理请求。
"""
from __future__ import annotations

import json
import logging
import re
import secrets
from typing import Any, Optional

import httpx

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
from core.nexus_config_store import load_nexus_config, normalize_sync_tenant_ids

logger = logging.getLogger(__name__)


def _validate_l3_public_key(pem: Optional[str]) -> bool:
    """校验 L3 公钥是否为有效 PEM 格式，供加密前快速失败。"""
    if not pem or not isinstance(pem, str):
        return False
    s = pem.strip()
    return s.startswith("-----BEGIN ") and "-----END " in s and "PUBLIC KEY" in s

router = APIRouter(prefix="/api/v2", tags=["v2-auth"])


_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_ORG_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.I,
)


async def _fetch_l1_org_by_id(org_id: str) -> Optional[str]:
    """
    GET /api/v1/l2-gateway/resolve-org?organization_id=…（X-L2-Gateway-Secret）。
    用于按主键校验组织存在并返回 canonical org_id。
    """
    import os

    oid = org_id.strip()
    if not oid or not _ORG_UUID_RE.fullmatch(oid):
        return None

    cfg = load_nexus_config()
    base = (cfg.get("nexus_base_url") or "").strip().rstrip("/")
    if not base:
        from core.config import settings

        base = (settings.NEXUS_BASE_URL or "").strip().rstrip("/")
    if not base:
        return None

    secret = (os.environ.get("NEXUS_L2_LOGIN_SECRET") or "").strip()
    if not secret:
        return None
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(
                f"{base}/api/v1/l2-gateway/resolve-org",
                params={"organization_id": oid},
                headers={"X-L2-Gateway-Secret": secret},
            )
        if r.status_code == 200:
            try:
                data = r.json()
            except Exception:
                return None
            if data.get("success"):
                d = data.get("data") or {}
                out = (d.get("org_id") or "").strip()
                if out:
                    return out
    except httpx.RequestError as e:
        logger.warning("[v2/auth] l2-gateway/resolve-org-by-id 请求失败: %s", e)
    return None


async def _fetch_l1_org_id_for_slug(slug: str) -> Optional[str]:
    """
    将 slug → organizations.id。
    1) 优先 L1 GET /api/v1/l2-gateway/resolve-org（X-L2-Gateway-Secret，与 workspace-members 一致）
    2) 回退 GET /api/v1/edge/resolve-org（Bearer 配对 access_token，须边缘行有效）
    """
    import os

    cfg = load_nexus_config()
    token = (cfg.get("access_token") or "").strip()
    base = (cfg.get("nexus_base_url") or "").strip().rstrip("/")
    if not base:
        from core.config import settings

        base = (settings.NEXUS_BASE_URL or "").strip().rstrip("/")
    if not base:
        return None

    secret = (os.environ.get("NEXUS_L2_LOGIN_SECRET") or "").strip()
    if secret:
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                r = await client.get(
                    f"{base}/api/v1/l2-gateway/resolve-org",
                    params={"slug": slug},
                    headers={"X-L2-Gateway-Secret": secret},
                )
            if r.status_code == 200:
                try:
                    data = r.json()
                except Exception:
                    data = {}
                if data.get("success"):
                    d = data.get("data") or {}
                    oid = (d.get("org_id") or "").strip()
                    if oid:
                        return oid
            elif r.status_code == 404:
                logger.info(
                    "[v2/auth] l2-gateway/resolve-org 未找到 slug=%s（可在 L1 为工作区设 slug，或显示名与参数一致）",
                    slug[:32],
                )
            elif r.status_code == 409:
                try:
                    amb = (r.json().get("message") or "").strip()
                except Exception:
                    amb = ""
                logger.warning(
                    "[v2/auth] l2-gateway/resolve-org 歧义 slug=%s %s",
                    slug[:32],
                    amb or "(多个工作区同名)",
                )
        except httpx.RequestError as e:
            logger.warning("[v2/auth] l2-gateway/resolve-org 请求失败: %s", e)

    if not token:
        return None
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(
                f"{base}/api/v1/edge/resolve-org",
                params={"slug": slug},
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.RequestError:
        return None
    if r.status_code != 200:
        return None
    try:
        data = r.json()
    except Exception:
        return None
    if not data.get("success"):
        return None
    d = data.get("data") or {}
    oid = (d.get("org_id") or "").strip()
    return oid or None


def _get_keys_for_sub_account_with_fallback(conn, sub_account_id: str) -> list:
    """
    获取子账号的 API Key；若该子账号无 Key，则从同 main_user 下其他子账号兜底。
    确保 L3 无论配对还是首次分配，都能分到 API Key。
    """
    rows = conn.execute(
        "SELECT id, provider, encrypted_key FROM api_keys_vault WHERE sub_account_id = ?",
        (sub_account_id,),
    ).fetchall()
    if rows:
        logger.info("[v2/auth] 子账号 %s 直接获取 %d 个 Key providers=%s",
            (sub_account_id[:16] + "..." if len(sub_account_id) > 16 else sub_account_id), len(rows), [r["provider"] for r in rows])
        return list(rows)

    # 子账号无 Key，从同 main_user 下其他子账号兜底
    main_row = conn.execute(
        "SELECT main_user_id FROM sub_accounts WHERE id = ?",
        (sub_account_id,),
    ).fetchone()
    if not main_row:
        return []
    main_user_id = main_row[0]
    fallback = conn.execute(
        """
        SELECT k.id, k.provider, k.encrypted_key FROM api_keys_vault k
        JOIN sub_accounts s ON k.sub_account_id = s.id
        WHERE s.main_user_id = ?
        """,
        (main_user_id,),
    ).fetchall()
    if fallback:
        logger.info(
            "[v2/auth] 子账号 %s 无 Key，从同 main_user 兜底分配 %d 个 providers=%s",
            sub_account_id[:16] + "..." if len(sub_account_id) > 16 else sub_account_id,
            len(fallback), [r["provider"] for r in fallback],
        )
    else:
        logger.warning("[v2/auth] 子账号 %s 无 Key 且同 main_user 无兜底，L3 将无法获取 API Key", sub_account_id[:16] + "..." if len(sub_account_id) > 16 else sub_account_id)
    return list(fallback)


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
    organization_id = (
        body.get("organization_id") or body.get("organizationId") or ""
    )
    if isinstance(organization_id, str):
        organization_id = organization_id.strip()
    else:
        organization_id = ""
    workspace_name = (body.get("workspace_name") or body.get("workspaceName") or "").strip()[:128]

    org_slug_raw = body.get("organization_slug") or body.get("organizationSlug") or ""
    org_slug = org_slug_raw.strip().lower() if isinstance(org_slug_raw, str) else ""
    if org_slug and not _SLUG_RE.match(org_slug):
        raise api_error(
            400,
            ERR_BAD_REQUEST_002,
            "organization_slug 格式无效（小写字母、数字、连字符）",
        )

    if org_slug and organization_id:
        raise api_error(
            400,
            ERR_BAD_REQUEST_002,
            "请勿同时提供 organization_id 与 organization_slug",
        )

    effective_org_id = organization_id
    if org_slug:
        resolved = await _fetch_l1_org_id_for_slug(org_slug)
        if not resolved:
            cfg_fb = load_nexus_config()
            ids_fb = normalize_sync_tenant_ids(cfg_fb)
            if len(ids_fb) == 1:
                only_tid = str(ids_fb[0]).strip()
                if _ORG_UUID_RE.fullmatch(only_tid):
                    verified = await _fetch_l1_org_by_id(only_tid)
                    if verified:
                        logger.warning(
                            "[auth/sync] organization_slug=%r 在 L1 未解析；"
                            "L2 仅配置单一同步租户，已改用 tenant_id=%s",
                            org_slug,
                            only_tid[:20] + ("..." if len(only_tid) > 20 else ""),
                        )
                        resolved = verified
            if not resolved:
                raise api_error(
                    400,
                    ERR_BAD_REQUEST_002,
                    "无法解析 organization_slug：请在 L1 为该工作区设置 slug，"
                    "或使显示名经 trim/小写后与 slug 一致；也可在 L3 改用 organization_id（UUID）。"
                    "若 L2 只同步一个工作区，可去掉错误的 organization_slug 以使用默认租户。",
                )
        effective_org_id = resolved

    cfg = load_nexus_config()
    ids_ordered = normalize_sync_tenant_ids(cfg)
    if ids_ordered:
        allowed_set = set(ids_ordered)
        if not effective_org_id:
            if len(ids_ordered) == 1:
                effective_org_id = ids_ordered[0]
                logger.info(
                    "[auth/sync] 未提供 organization，单同步租户默认使用 tenant_id=%s",
                    effective_org_id[:20] + ("..." if len(effective_org_id) > 20 else ""),
                )
            else:
                raise api_error(
                    403,
                    ERR_AUTH_004,
                    "多工作区已启用：L3 须在请求体中提供 organization_id 或 organization_slug。"
                    "可在 L2 /gateway「多工作区同步」查看活动 tenant_id，"
                    "并写入 L3 的 ~/.jachin/l2_gateway_config.json 或环境变量 "
                    "JACHIN_ORGANIZATION_ID / JACHIN_ORGANIZATION_SLUG。",
                )
        elif effective_org_id not in allowed_set:
            raise api_error(
                403,
                ERR_AUTH_004,
                "organization_id（或 slug 解析结果）须落在当前 L2 的 sync_tenant_ids / tenant_id 内"
                "（见 ~/.jachin/nexus_config.json）。可在 L2 /gateway 的「多工作区同步」中勾选。",
            )

    if not public_key_pem or not isinstance(public_key_pem, str):
        raise api_error(400, ERR_BAD_REQUEST_002, "public_key_pem is required")

    node_id = body.get("node_id")
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
        if not node_id and public_key_pem:
            # 配置丢失时：复用同公钥的已审批节点（公钥来自 l3_identity.json，同设备稳定）
            row = conn.execute(
                "SELECT id FROM l3_nodes WHERE public_key_pem = ? AND sub_account_id IS NOT NULL LIMIT 1",
                (public_key_pem,),
            ).fetchone()
            if row:
                node_id = row["id"]
                logger.info("[auth/sync] Reusing approved node %s for same device (public_key match)", node_id)
        if not node_id:
            node_id = f"l3-{secrets.token_hex(8)}"

        conn.execute(
            """
            INSERT INTO l3_nodes (id, device_fingerprint, public_key_pem, sub_account_id, capabilities_json, trust_zone, display_name, organization_id, workspace_name, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, strftime('%s', 'now'))
            ON CONFLICT(id) DO UPDATE SET
                device_fingerprint = excluded.device_fingerprint,
                public_key_pem = excluded.public_key_pem,
                capabilities_json = excluded.capabilities_json,
                trust_zone = CASE WHEN excluded.trust_zone != '' THEN excluded.trust_zone ELSE trust_zone END,
                display_name = CASE WHEN excluded.display_name != '' THEN excluded.display_name ELSE display_name END,
                organization_id = CASE WHEN excluded.organization_id != '' THEN excluded.organization_id ELSE organization_id END,
                workspace_name = CASE WHEN excluded.workspace_name != '' THEN excluded.workspace_name ELSE workspace_name END,
                last_seen_at = strftime('%s', 'now')
            """,
            (
                node_id,
                device_fingerprint,
                public_key_pem,
                sub_account_id,
                caps_json,
                trust_zone,
                display_name,
                effective_org_id,
                workspace_name,
            ),
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

        # 获取该 sub_account 的 API Key；若节点有 api_key_ids 则仅返回该节点专属 Key；无则兜底
        api_key_ids_raw = row.get("api_key_ids") or "[]"
        try:
            node_key_ids = json.loads(api_key_ids_raw)
            if isinstance(node_key_ids, list) and len(node_key_ids) > 0:
                placeholders = ",".join("?" * len(node_key_ids))
                rows = conn.execute(
                    f"SELECT id, provider, encrypted_key FROM api_keys_vault WHERE sub_account_id = ? AND id IN ({placeholders})",
                    (sub_account_id, *node_key_ids),
                ).fetchall()
            else:
                rows = _get_keys_for_sub_account_with_fallback(conn, sub_account_id)
        except (json.JSONDecodeError, TypeError):
            rows = _get_keys_for_sub_account_with_fallback(conn, sub_account_id)
        logger.info("[v2/keys] L2 向 L3 分配 Key node_id=%s sub_account_id=%s keys_count=%d providers=%s",
            node_id, (sub_account_id or "")[:16] + ("..." if len(sub_account_id or "") > 16 else ""), len(rows), [r["provider"] for r in rows])
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
            logger.warning(
                "[v2/keys] 无法为 L3 加密 Key %s: %s。若 JACHIN_L2_MASTER_KEY 未设置或曾变更，请设置后运行 scripts/fix_l2_keys_after_master_key_reset.py 并重启 L2",
                r["id"], e,
            )
            continue

    perms_l3 = normalize_permissions_for_l3(perms)
    try:
        model_endpoints = json.loads(model_endpoints_raw) if model_endpoints_raw else {}
    except json.JSONDecodeError:
        model_endpoints = {}
    logger.info("[v2/keys] L2 返回 L3 加密 Key 完成 encrypted_keys=%d providers=%s", len(encrypted_keys), [x["provider"] for x in encrypted_keys])
    return {
        "encrypted_api_keys": encrypted_keys,
        "sub_account_id": sub_account_id,
        "node_id": node_id,
        "permissions_snapshot": perms_l3,
        "model_endpoints": model_endpoints,
    }


@router.get("/auth/heartbeat")
async def auth_heartbeat(node_id: Optional[str] = None) -> dict[str, Any]:
    """
    L3 节点心跳：仅更新 last_seen_at，保持在线状态。
    L3 审批通过后应每 2 分钟调用一次，供 JachinLink 等展示在线设备。
    """
    if not node_id:
        raise api_error(400, ERR_BAD_REQUEST_002, "node_id is required")

    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE l3_nodes SET last_seen_at = strftime('%s', 'now') WHERE id = ?",
            (node_id,),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise api_error(404, ERR_NOT_FOUND_002, "L3 node not found")
    except HTTPException:
        raise
    finally:
        conn.close()

    return {"ok": True, "node_id": node_id}


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

        # 获取该 sub_account 的 API Key（无则从同 main_user 兜底），并用 L3 公钥加密
        rows = _get_keys_for_sub_account_with_fallback(conn, sub_account_id)
        # 已配对但无 Key：立即从 env 同步到该子账号，确保 L3 能拿到 Key
        if not rows and sub_account_id:
            try:
                from core.bootstrap import sync_api_keys_for_sub_account
                n = sync_api_keys_for_sub_account(sub_account_id)
                if n > 0:
                    conn2 = get_connection()
                    try:
                        rows = _get_keys_for_sub_account_with_fallback(conn2, sub_account_id)
                    finally:
                        conn2.close()
                    logger.info("[v2/auth/poll] 按需同步 env Key 到子账号 %s，新增 %d 个，重试后 keys_count=%d",
                        sub_account_id[:16] + ("..." if len(sub_account_id) > 16 else ""), n, len(rows))
            except Exception as e:
                logger.warning("[v2/auth/poll] 按需同步 Key 失败: %s", e)
        logger.info("[v2/auth/poll] L2 向 L3 分配 Key node_id=%s sub_account_id=%s keys_count=%d providers=%s",
            node_id, (sub_account_id or "")[:16] + ("..." if len(sub_account_id or "") > 16 else ""), len(rows), [r["provider"] for r in rows])
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[v2/auth/poll] DB error: %s", e)
        raise api_error(500, ERR_INTERNAL_001, "Failed to fetch keys", detail=str(e))
    finally:
        conn.close()

    encrypted_keys = []
    if not _validate_l3_public_key(public_key_pem):
        logger.warning(
            "[v2/auth/poll] L3 公钥格式无效或为空，无法加密 Key。请让 L3 删除 ~/.jachin/identity.json 和 l2_gateway_config.json 后重新连接"
        )
    else:
        for r in rows:
            try:
                plain = decrypt_from_storage(r["encrypted_key"])
                enc_for_l3 = encrypt_for_l3(plain, public_key_pem)
                encrypted_keys.append(
                    {"id": r["id"], "provider": r["provider"], "encrypted_key": enc_for_l3}
                )
            except Exception as e:
                err_type = type(e).__name__
                err_msg = str(e) or "(无详情)"
                # 区分根因：InvalidTag = Master Key 不匹配；ValueError/含 Expected = 公钥格式问题
                if err_type == "InvalidTag":
                    hint = "JACHIN_L2_MASTER_KEY 曾变更或未设置。请设置后运行 scripts/fix_l2_keys_after_master_key_reset.py 并重启 L2"
                elif "Expected" in err_msg or "deserialize" in err_msg.lower() or "PEM" in err_msg:
                    hint = "L3 公钥格式异常。请让 L3 删除 ~/.jachin/identity.json 和 l2_gateway_config.json 后重新连接"
                else:
                    hint = "若 JACHIN_L2_MASTER_KEY 曾变更，请运行 scripts/fix_l2_keys_after_master_key_reset.py 并重启 L2"
                logger.warning(
                    "[v2/auth/poll] 无法为 L3 加密 Key %s: %s (%s)。%s",
                    r["id"], err_type, err_msg[:200], hint,
                )
                continue

    try:
        model_endpoints = json.loads(model_endpoints_raw) if model_endpoints_raw else {}
    except json.JSONDecodeError:
        model_endpoints = {}
    logger.info("[v2/auth/poll] L2 返回 L3 加密 Key 完成 encrypted_keys=%d providers=%s", len(encrypted_keys), [x["provider"] for x in encrypted_keys])
    return {
        "status": "approved",
        "node_id": node_id,
        "sub_account_id": sub_account_id,
        "encrypted_api_keys": encrypted_keys,
        "permissions_snapshot": permissions_snapshot,
        "model_endpoints": model_endpoints,
    }
