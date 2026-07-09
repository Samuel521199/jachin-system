"""
Jachin Nexus V2 - L2 控制面管理 API（内部/Admin）

用于创建子账号、向保险箱写入 API Key。
废弃 JACHIN_L2_ADMIN_TOKEN，改为 username/password + JWT 登录。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
from pathlib import Path
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
from core.nexus_config_store import (
    load_nexus_config,
    save_nexus_config,
    normalize_sync_tenant_ids,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/admin", tags=["v2-admin"])

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", re.IGNORECASE)


def _is_uuid_like(value: str) -> bool:
    s = value.strip()
    if len(s) != 36 or s.count("-") != 4:
        return False
    hx = s.replace("-", "")
    if len(hx) != 32:
        return False
    return all(c in "0123456789abcdefABCDEF" for c in hx)


def _is_l1_email_login(username: str) -> bool:
    u = username.strip()
    return bool(_EMAIL_RE.match(u))


def _schedule_hot_restart_pairing_services(app: Any) -> None:
    """
    nexus_config 已更新后 **后台** 热启 L1 心跳 + CloudSyncDaemon。
    不阻塞登录 HTTP 响应（避免取消旧 CloudSync 或首轮同步拖住「验证中…」）。
    """
    async def _run() -> None:
        try:
            from core.sync_daemon import hot_restart_l1_background_services

            await hot_restart_l1_background_services(app)
        except Exception as e:
            logger.warning(
                "[v2/admin] 配对后热启动 L1 后台服务失败（可重启 L2 进程重试）: %s",
                e,
                exc_info=True,
            )

    try:
        asyncio.get_running_loop().create_task(_run())
    except RuntimeError:
        logger.warning("[v2/admin] 无法调度热启动：当前无运行中的事件循环")


def _persist_l1_pairing_to_l2(
    data: dict[str, Any],
    base_fallback: str,
    *,
    gateway_display_username: str | None = None,
    pairing_code_tag: str = "web",
) -> tuple[str, str, str, str]:
    """
    将 L1 成功载荷写入 ~/.jachin/nexus_config.json，对齐 gateway_admins 与默认子账号。
    返回 (admin_id, username, main_user_id, role) 供签发 Admin JWT。
    """
    from core.bootstrap import ensure_default_sub_account
    from core.db.schema import _ensure_default_gateway_admin

    config_path = Path.home() / ".jachin" / "nexus_config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)

    base = base_fallback.rstrip("/")
    cfg_out: dict[str, Any] = {
        "instance_id": data["instance_id"],
        "access_token": data["access_token"],
        "nexus_base_url": (data.get("nexus_base_url") or base).rstrip("/"),
        "pairing_code": pairing_code_tag,
    }
    if data.get("l1_user_id"):
        cfg_out["l1_user_id"] = data["l1_user_id"]
    tid_pair = (data.get("tenant_id") or "").strip()
    if tid_pair:
        cfg_out["tenant_id"] = tid_pair
        cfg_out["sync_tenant_ids"] = [tid_pair]
    else:
        cfg_out["sync_tenant_ids"] = []

    config_path.write_text(
        json.dumps(cfg_out, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    l1_user_id = cfg_out.get("l1_user_id") or cfg_out.get("instance_id") or ""

    conn = get_connection()
    try:
        _ensure_default_gateway_admin(conn)
        if gateway_display_username and l1_user_id:
            conn.execute(
                "UPDATE gateway_admins SET username = ? WHERE main_user_id = ?",
                (gateway_display_username.strip().lower(), l1_user_id),
            )
            conn.commit()
        row = conn.execute(
            "SELECT id, username, main_user_id, role FROM gateway_admins WHERE main_user_id = ? LIMIT 1",
            (l1_user_id,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        raise api_error(
            500,
            ERR_INTERNAL_001,
            "网关管理员未绑定 L1 用户，请重启 L2 或执行 reset-admin",
        )

    ensure_default_sub_account()

    access_token = cfg_out.get("access_token") or ""
    if not access_token or not l1_user_id:
        raise api_error(500, ERR_INTERNAL_001, "写入配置后校验失败")

    try:
        from core.l2_pairing_diagnostics import log_pairing_diagnostics

        log_pairing_diagnostics(
            logger,
            phase="nexus_config_persisted",
            extra_lines=[
                f"写入来源 pairing_code_tag={pairing_code_tag}",
                f"gateway_display_username={gateway_display_username or '(none)'}",
                f"落盘 nexus_base_url={cfg_out.get('nexus_base_url') or '(none)'}",
            ],
        )
    except Exception as e:
        logger.warning("[v2/admin] 配对诊断输出跳过: %s", e)

    return (row[0], row[1], row[2], row[3])


async def _assert_l1_allows_workspace_gateway(access_token: str) -> None:
    """
    「快速登录」前向 L1 校验：当前 edge access_token 对应用户须为工作区 owner/admin。
    """
    import httpx

    from core.config import settings

    base = (settings.NEXUS_BASE_URL or "").strip().rstrip("/")
    if not base:
        return
    timeout = httpx.Timeout(20.0, connect=10.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(
                f"{base}/api/v1/l2-gateway/gateway-access",
                headers={"Authorization": f"Bearer {access_token}"},
            )
    except httpx.RequestError as e:
        logger.warning("[login-with-l1] L1 gateway-access 不可达: %s", e)
        raise api_error(
            502,
            ERR_INTERNAL_001,
            f"无法连接 L1 校验网关权限：{e}。请确认 NEXUS_BASE_URL 或改用邮箱密码登录。",
        ) from e
    try:
        data = r.json()
    except Exception:
        raise api_error(
            502, ERR_INTERNAL_001, "L1 网关权限接口返回非 JSON"
        )
    if not data.get("allowed"):
        msg = str(
            data.get("message") or "权限不足：仅工作区所有者或管理员可使用 L2 网关"
        )
        raise api_error(403, ERR_AUTH_005, msg)


@router.post("/login-with-l1")
async def admin_login_with_l1(request: Request) -> dict[str, Any]:
    """
    使用 nexus_config 内 L1 下发的凭证换取 L2 Admin JWT（已配对后免填密码）。
    首次配对请走 /gateway：L1 注册邮箱+密码登录，或「Nexus 账号登录」；无 Web 时用 CLI pair。
    """
    cfg = load_nexus_config()
    access_token = cfg.get("access_token") or ""
    l1_user_id = cfg.get("l1_user_id") or cfg.get("instance_id") or ""
    if not access_token or not l1_user_id:
        raise api_error(
            401,
            ERR_AUTH_005,
            "未完成 L1 配对。请在 /gateway 使用 L1 邮箱+密码登录，或「Nexus 账号登录」；"
            "无浏览器环境再执行 python -m core.cli pair。",
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
    await _assert_l1_allows_workspace_gateway(access_token)
    admin_id, uname, main_user_id, role = row[0], row[1], row[2], row[3]
    token = create_admin_token(
        admin_id, uname or "admin", main_user_id, role or "admin"
    )
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


async def _admin_login_via_l1_credentials(email: str, password: str) -> dict[str, Any]:
    """用户名形如邮箱时：由 L2 服务端向 L1 校验邮箱+密码，落盘 nexus_config 并签发 L2 Admin JWT。"""
    import os

    import httpx

    from core.config import settings

    base = settings.NEXUS_BASE_URL.rstrip("/")
    if not base:
        raise api_error(
            500,
            ERR_INTERNAL_001,
            "NEXUS_BASE_URL 未配置，无法用 L1 注册邮箱登录",
        )

    headers: dict[str, str] = {}
    secret = (os.environ.get("NEXUS_L2_LOGIN_SECRET") or "").strip()
    if secret:
        headers["X-L2-Gateway-Secret"] = secret

    timeout = httpx.Timeout(30.0, connect=15.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                f"{base}/api/v1/l2-gateway/verify-credentials",
                json={"email": email.strip().lower(), "password": password},
                headers=headers,
            )
    except httpx.RequestError as e:
        logger.warning("[admin-login-l1-credentials] L1 request error: %s", e)
        raise api_error(
            502,
            ERR_INTERNAL_001,
            f"无法连接 Layer 1（检查 NEXUS_BASE_URL）: {e}",
        )

    try:
        data = r.json()
    except Exception:
        raise api_error(502, ERR_INTERNAL_001, "L1 返回非 JSON")

    if r.status_code != 200 or not data.get("success"):
        msg = data.get("message") or data.get("error") or "邮箱或密码错误"
        raise api_error(401, ERR_AUTH_005, str(msg))

    l1_uid = data.get("l1_user_id")
    if not l1_uid or not data.get("instance_id") or not data.get("access_token"):
        raise api_error(502, ERR_INTERNAL_001, "L1 返回字段不完整")

    pair_payload: dict[str, Any] = {
        "instance_id": data["instance_id"],
        "access_token": data["access_token"],
        "l1_user_id": l1_uid,
        "tenant_id": data.get("tenant_id"),
        "nexus_base_url": data.get("nexus_base_url"),
    }
    admin_id, uname, main_user_id, role = _persist_l1_pairing_to_l2(
        pair_payload,
        base,
        gateway_display_username=email.strip().lower(),
        pairing_code_tag="l1_email",
    )
    token = create_admin_token(
        admin_id, uname or email.strip().lower(), main_user_id, role or "admin"
    )
    expires_hours = int(os.environ.get("JWT_EXPIRATION_HOURS", "24"))
    return {
        "token": token,
        "expires_in_hours": expires_hours,
        "admin": {
            "id": admin_id,
            "username": uname or email.strip().lower(),
            "main_user_id": main_user_id,
            "role": role or "admin",
        },
        "source": "l1_credentials",
    }


@router.post("/login")
async def admin_login(request: Request) -> dict[str, Any]:
    """
    网关管理员登录。body: { username, password }
    若用户名为邮箱格式，则向 L1 校验注册邮箱+密码并自动写入 nexus_config（与 Web Bridge 同效）。
    否则校验本地 gateway_admins（默认 admin / admin123）。
    成功返回 { token, expires_in_hours, admin, source? }
    """
    try:
        body = await request.json()
    except Exception:
        raise api_error(400, ERR_BAD_REQUEST_001, "Invalid JSON")

    username = (body.get("username") or "").strip()
    password = body.get("password") or ""

    if not username or not password:
        raise api_error(400, ERR_BAD_REQUEST_002, "username and password are required")

    if _is_l1_email_login(username):
        out = await _admin_login_via_l1_credentials(username, password)
        _schedule_hot_restart_pairing_services(request.app)
        return out

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


@router.get("/workspace-members")
async def admin_workspace_members(
    admin: dict = Depends(get_current_admin),
) -> dict[str, Any]:
    """
    从 L1 拉取当前 nexus_config.tenant_id 对应工作区的成员列表（服务端密钥）。
    Legacy optional L2 admin endpoint; packaged L3 does not require this pairing path.
    """
    import os

    import httpx

    from core.config import settings

    _ = admin  # 已登录即可；租户以 nexus_config 为准
    cfg = load_nexus_config()
    org_id = (cfg.get("tenant_id") or "").strip()
    if not org_id:
        raise api_error(
            400,
            ERR_BAD_REQUEST_002,
            "nexus_config 中无 tenant_id，请先完成 L1 配对",
        )

    base = settings.NEXUS_BASE_URL.rstrip("/")
    if not base:
        raise api_error(
            500,
            ERR_INTERNAL_001,
            "NEXUS_BASE_URL 未配置，无法请求 L1",
        )

    secret = (os.environ.get("NEXUS_L2_LOGIN_SECRET") or "").strip()
    headers: dict[str, str] = {}
    if secret:
        headers["X-L2-Gateway-Secret"] = secret

    timeout = httpx.Timeout(30.0, connect=15.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(
                f"{base}/api/v1/l2-gateway/workspace-members",
                params={"organization_id": org_id},
                headers=headers,
            )
    except httpx.RequestError as e:
        logger.warning("[workspace-members] L1 request error: %s", e)
        raise api_error(
            502,
            ERR_INTERNAL_001,
            f"无法连接 L1 拉取成员列表: {e}",
        )

    try:
        data = r.json()
    except Exception:
        raise api_error(502, ERR_INTERNAL_001, "L1 返回非 JSON")

    if r.status_code != 200 or not data.get("success"):
        msg = data.get("message") or data.get("error") or "L1 拒绝请求"
        if r.status_code == 503:
            raise api_error(503, ERR_INTERNAL_001, str(msg))
        raise api_error(502, ERR_INTERNAL_001, str(msg))

    return {"data": data.get("data")}


@router.get("/nexus-profile")
async def get_nexus_profile(
    admin: dict = Depends(get_current_admin),
) -> dict[str, Any]:
    """当前 ~/.jachin/nexus_config.json 中的活动租户与 P3 多工作区同步列表（不含明文 token）。"""
    _ = admin
    cfg = load_nexus_config()
    return {
        "tenant_id": (cfg.get("tenant_id") or "").strip(),
        "sync_tenant_ids": normalize_sync_tenant_ids(cfg),
        "nexus_base_url": (cfg.get("nexus_base_url") or "").strip(),
        "has_access_token": bool((cfg.get("access_token") or "").strip()),
    }


@router.post("/nexus-profile")
async def post_nexus_profile(
    request: Request,
    admin: dict = Depends(get_current_admin),
) -> dict[str, Any]:
    """
    更新 P3 多工作区：sync_tenant_ids + 当前活动 tenant_id（manifest 遥测仍以活动租户为主）。
    body: { "sync_tenant_ids": ["uuid", ...], "tenant_id"?: "active uuid" }
    """
    _ = admin
    try:
        body = await request.json()
    except Exception:
        raise api_error(400, ERR_BAD_REQUEST_001, "Invalid JSON")

    sync_raw = body.get("sync_tenant_ids")
    active = (body.get("tenant_id") or body.get("active_tenant_id") or "").strip()

    if not isinstance(sync_raw, list):
        raise api_error(400, ERR_BAD_REQUEST_002, "sync_tenant_ids must be a list")

    cleaned: list[str] = []
    for x in sync_raw:
        s = str(x).strip()
        if not s:
            continue
        if not _is_uuid_like(s):
            raise api_error(400, ERR_BAD_REQUEST_002, f"Invalid tenant UUID: {s}")
        if s not in cleaned:
            cleaned.append(s)

    if active and not _is_uuid_like(active):
        raise api_error(400, ERR_BAD_REQUEST_002, "Invalid tenant_id")

    cfg = load_nexus_config()
    prev_tid = (cfg.get("tenant_id") or "").strip()
    if not cleaned:
        if prev_tid:
            raise api_error(
                400,
                ERR_BAD_REQUEST_002,
                "sync_tenant_ids 不能为空（若需恢复单租户，请至少保留当前工作区 UUID）",
            )
        cfg["sync_tenant_ids"] = []
        cfg.pop("tenant_id", None)
        save_nexus_config(cfg)
        _schedule_hot_restart_pairing_services(request.app)
        return {
            "ok": True,
            "tenant_id": "",
            "sync_tenant_ids": [],
        }

    if active and active not in cleaned:
        cleaned.insert(0, active)

    cfg["sync_tenant_ids"] = cleaned
    if active:
        cfg["tenant_id"] = active
    elif cleaned:
        cfg["tenant_id"] = cleaned[0]

    save_nexus_config(cfg)
    _schedule_hot_restart_pairing_services(request.app)
    return {
        "ok": True,
        "tenant_id": (cfg.get("tenant_id") or "").strip(),
        "sync_tenant_ids": normalize_sync_tenant_ids(cfg),
    }


@router.get("/l1-workspaces-edge")
async def admin_l1_workspaces_edge(
    admin: dict = Depends(get_current_admin),
) -> dict[str, Any]:
    """用 nexus_config 内 L1 edge token 拉取 GET /api/v1/edge/me/workspaces（网关 UI 多工作区勾选）。"""
    _ = admin
    import httpx

    from core.config import settings

    cfg = load_nexus_config()
    token = (cfg.get("access_token") or "").strip()
    base = (cfg.get("nexus_base_url") or settings.NEXUS_BASE_URL or "").strip().rstrip("/")
    if not token or not base:
        raise api_error(
            400,
            ERR_BAD_REQUEST_002,
            "未完成 L1 配对：缺少 access_token 或 nexus_base_url",
        )

    timeout = httpx.Timeout(30.0, connect=15.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(
                f"{base}/api/v1/edge/me/workspaces",
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.RequestError as e:
        logger.warning("[l1-workspaces-edge] L1 request error: %s", e)
        raise api_error(
            502,
            ERR_INTERNAL_001,
            f"无法连接 L1 拉取工作区列表: {e}",
        )

    try:
        data = r.json()
    except Exception:
        raise api_error(502, ERR_INTERNAL_001, "L1 返回非 JSON")

    if r.status_code != 200 or not data.get("success"):
        msg = data.get("message") or data.get("error") or "L1 拒绝请求"
        raise api_error(502, ERR_INTERNAL_001, str(msg))

    return {"data": data.get("data")}


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
# 待审批节点在获批前不会发心跳，last_seen 仅 auth/sync 时更新一次；用 created_at 宽窗口避免「刚注册却列表为空」
_PENDING_UNASSIGNED_MAX_AGE_SEC = 7 * 86400


@router.get("/nodes")
async def list_nodes(
    request: Request,
    admin: dict = Depends(get_current_admin),
) -> list[dict[str, Any]]:
    """
    返回 L3 节点列表。
    待审批：sub_account_id 为空且（5 分钟内有 sync/心跳 **或** 创建时间在 7 天内），否则易因「等审批无心跳」被误隐藏。
    已分配：展示全部，含 last_seen_at 与 is_online。
    """
    import time
    main_user_id = admin.get("main_user_id") or ""
    now = time.time()
    cutoff = now - _OFFLINE_THRESHOLD_SEC
    pending_created_cutoff = now - _PENDING_UNASSIGNED_MAX_AGE_SEC

    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT n.id, n.device_fingerprint, n.sub_account_id, n.capabilities_json, n.model_endpoints,
                   n.last_seen_at, n.created_at, n.display_name,
                   COALESCE(n.organization_id, '') AS organization_id,
                   COALESCE(n.workspace_name, '') AS workspace_name
            FROM l3_nodes n
            LEFT JOIN sub_accounts s ON n.sub_account_id = s.id
            WHERE (n.sub_account_id IS NULL AND (n.last_seen_at > ? OR n.created_at > ?))
               OR (s.main_user_id = ?)
            ORDER BY n.created_at DESC
            """,
            (cutoff, pending_created_cutoff, main_user_id),
        ).fetchall()
        result = []
        for r in rows:
            me = r["model_endpoints"] or "{}"
            try:
                model_endpoints = json.loads(me) if me else {}
            except json.JSONDecodeError:
                model_endpoints = {}
            last_seen = r["last_seen_at"]
            last_seen_float = float(last_seen) if last_seen is not None else 0
            is_online = last_seen_float > cutoff
            result.append({
                "id": r["id"],
                "device_fingerprint": r["device_fingerprint"],
                "sub_account_id": r["sub_account_id"],
                "capabilities_json": r["capabilities_json"],
                "model_endpoints": model_endpoints,
                "last_seen_at": last_seen,
                "created_at": r["created_at"],
                "display_name": r["display_name"] or "",
                "organization_id": r["organization_id"] or "",
                "workspace_name": r["workspace_name"] or "",
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


@router.get("/l1-bridge-config")
async def l1_bridge_config() -> dict[str, Any]:
    """
    公开：供 /gateway 前端拼接「Nexus 账号登录」跳转 URL（无密钥）。
    """
    from core.config import settings

    return {
        "nexus_base_url": settings.NEXUS_BASE_URL.rstrip("/"),
        "brain_base_url": settings.BRAIN_BASE_URL.rstrip("/"),
        "callback_path": "/gateway/l1-bridge-callback.html",
    }


@router.post("/redeem-l1-bridge")
async def redeem_l1_bridge(request: Request) -> dict[str, Any]:
    """
    使用 L1 /console/l2-bridge 授权后带回的 bridge_code，向 L1 兑换凭证并写入
    ~/.jachin/nexus_config.json，同步网关管理员与子账号，返回 Admin JWT。
    """
    import httpx

    from core.config import settings

    try:
        body = await request.json()
    except Exception:
        raise api_error(400, ERR_BAD_REQUEST_001, "Invalid JSON")

    bridge_code = (body.get("bridge_code") or "").strip()
    if not bridge_code:
        raise api_error(400, ERR_BAD_REQUEST_002, "bridge_code is required")

    base = settings.NEXUS_BASE_URL.rstrip("/")
    if not base:
        raise api_error(500, ERR_INTERNAL_001, "NEXUS_BASE_URL 未配置")

    timeout = httpx.Timeout(30.0, connect=15.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                f"{base}/api/v1/l2-bridge/redeem",
                json={"bridge_code": bridge_code},
            )
    except httpx.RequestError as e:
        logger.warning("[redeem-l1-bridge] L1 request error: %s", e)
        raise api_error(
            502,
            ERR_INTERNAL_001,
            f"无法连接 Layer 1（检查 NEXUS_BASE_URL）: {e}",
        )

    try:
        data = r.json()
    except Exception:
        raise api_error(502, ERR_INTERNAL_001, "L1 返回非 JSON")

    if r.status_code != 200:
        msg = data.get("message") or data.get("error") or r.text
        raise api_error(400, ERR_AUTH_005, str(msg))

    if data.get("status") != "success":
        raise api_error(
            400,
            ERR_AUTH_005,
            str(data.get("message") or data.get("error") or "兑换失败"),
        )

    pair_payload: dict[str, Any] = {
        "instance_id": data["instance_id"],
        "access_token": data["access_token"],
        "l1_user_id": data.get("l1_user_id"),
        "tenant_id": data.get("tenant_id"),
        "nexus_base_url": data.get("nexus_base_url"),
    }
    admin_id, uname, main_user_id, role = _persist_l1_pairing_to_l2(
        pair_payload, base, pairing_code_tag="web"
    )
    token = create_admin_token(
        admin_id, uname or "admin", main_user_id, role or "admin"
    )
    import os

    expires_hours = int(os.environ.get("JWT_EXPIRATION_HOURS", "24"))
    _schedule_hot_restart_pairing_services(request.app)
    return {
        "token": token,
        "expires_in_hours": expires_hours,
        "admin": {
            "id": admin_id,
            "username": uname or "admin",
            "main_user_id": main_user_id,
            "role": role or "admin",
        },
        "source": "l1_web_bridge",
    }
