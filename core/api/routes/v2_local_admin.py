"""
Jachin Nexus V2 - L2 本地局域网管理 API

L2 数据主权：物资清单、角色权限均由本地 DB 维护，不依赖 L1 下发。
供本地管理控制台调用（局域网内）。
"""
from __future__ import annotations

import json
import logging
import secrets
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends

from core.admin_auth import get_current_admin
from core.db import get_connection
from core.errors import ERR_BAD_REQUEST_002, api_error
from core.inventory_scanner import (
    MCPS_DIR,
    SKILLS_DIR,
    ensure_inventory_dirs,
    registered_local_skills,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/admin", tags=["v2-local-admin"])

# 默认租户（L2 本地模式）
_DEFAULT_TENANT = "local"


def _load_nexus_tenant() -> str:
    """从 nexus_config 读取 tenant_id，无则返回默认值"""
    path = Path.home() / ".jachin" / "nexus_config.json"
    if not path.exists():
        return _DEFAULT_TENANT
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return (data.get("tenant_id") or data.get("instance_id") or _DEFAULT_TENANT).strip()
    except Exception:
        return _DEFAULT_TENANT


@router.get("/sync-debug")
async def sync_debug(
    _: dict = Depends(get_current_admin),
) -> dict[str, Any]:
    """
    调试：拉取 L1 manifest（不下载），查看 tenant 与返回数据。
    """
    try:
        from core.sync_daemon import CloudSyncDaemon
        daemon = CloudSyncDaemon()
        if not daemon._is_configured():
            return {"ok": False, "error": "未配对 L1", "tenant": None, "manifest": []}
        m1 = await daemon.poll_manifest()
        return {
            "ok": True,
            "tenant": daemon._tenant_id,
            "base_url": daemon._base_url,
            "manifest_count": len(m1),
            "manifest": m1,
        }
    except Exception as e:
        logger.exception("[LocalAdmin] sync-debug 失败: %s", e)
        return {"ok": False, "error": str(e), "manifest": []}


@router.post("/sync-now")
async def trigger_sync_now(
    _: dict = Depends(get_current_admin),
) -> dict[str, Any]:
    """
    手动触发一次 L1 manifest 拉取与技能空投（调试用）。
    """
    try:
        from core.sync_daemon import CloudSyncDaemon
        daemon = CloudSyncDaemon()
        if not daemon._is_configured():
            return {"ok": False, "error": "未配对 L1 或缺少 tenant_id", "manifest_count": 0}
        await daemon.run_sync_cycle()
        return {"ok": True, "message": "同步完成，请刷新武库"}
    except Exception as e:
        logger.exception("[LocalAdmin] sync-now 失败: %s", e)
        return {"ok": False, "error": str(e), "manifest_count": 0}


@router.get("/inventory")
async def get_local_inventory(
    _: dict = Depends(get_current_admin),
) -> dict[str, Any]:
    """
    扫描并返回本地所有可用物资。
    无缝合并：L1 同步的 PUBLIC 技能（.sync_meta）+ 本地侧载的 PRIVATE 技能（.local_meta）。
    含 Skills 与 MCPs。
    """
    ensure_inventory_dirs()
    # 确保使用最新缓存（可选热重载，避免冷启动未扫描）
    try:
        from core.inventory_reloader import request_reload
        await request_reload()
    except Exception as e:
        logger.warning("[LocalAdmin] reload_inventory 失败，使用缓存: %s", e)

    skills: list[dict[str, Any]] = []
    for s in registered_local_skills.values():
        entry = {
            "id": s.get("id", ""),
            "item_id": s.get("item_id", ""),
            "name": s.get("name", ""),
            "version": s.get("version", "1.0.0"),
            "description": s.get("description", ""),
            "origin": s.get("origin", "L1_SYNC"),
            "is_private": s.get("is_private", False),
            "item_type": "SKILL",
        }
        skills.append(entry)

    mcps: list[dict[str, Any]] = []
    if MCPS_DIR.exists():
        # 1. 扁平 .json 文件
        for p in MCPS_DIR.iterdir():
            if not p.is_file() or p.suffix.lower() != ".json":
                continue
            item_id = p.stem
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                name = ""
                if isinstance(data, dict):
                    name = data.get("name") or data.get("id", item_id)
                elif isinstance(data, list) and data:
                    name = str(data[0].get("name", item_id)) if isinstance(data[0], dict) else item_id
                mcps.append({
                    "id": f"mcp:{item_id}",
                    "item_id": item_id,
                    "name": name or item_id,
                    "version": data.get("version", "1.0.0") if isinstance(data, dict) else "1.0.0",
                    "origin": "L1_SYNC" if (MCPS_DIR / f".{item_id}.sync_meta").exists() else "SIDE_LOAD",
                    "is_private": not (MCPS_DIR / f".{item_id}.sync_meta").exists(),
                    "item_type": "MCP",
                })
            except Exception as e:
                logger.warning("[LocalAdmin] 解析 MCP 配置失败 file=%s err=%s", p.name, e)

        # 2. 子目录结构：local-hr-fs/ 含 plugin.json + config.json（侧载轨）
        seen_item_ids = {m["item_id"] for m in mcps}
        for subdir in MCPS_DIR.iterdir():
            if not subdir.is_dir():
                continue
            plugin_path = subdir / "plugin.json"
            config_path = subdir / "config.json"
            if not plugin_path.exists() or not config_path.exists():
                continue
            item_id = subdir.name
            if item_id in seen_item_ids:
                continue
            seen_item_ids.add(item_id)
            try:
                plugin_data = json.loads(plugin_path.read_text(encoding="utf-8"))
                mcps.append({
                    "id": f"mcp:{item_id}",
                    "item_id": item_id,
                    "name": plugin_data.get("name", item_id),
                    "version": plugin_data.get("version", "1.0.0"),
                    "description": plugin_data.get("description", ""),
                    "origin": "SIDE_LOAD",
                    "is_private": True,
                    "item_type": "MCP",
                })
            except Exception as e:
                logger.warning("[LocalAdmin] 解析 MCP plugin.json 失败 dir=%s err=%s", subdir.name, e)

    return {
        "success": True,
        "skills": skills,
        "mcps": mcps,
        "total_skills": len(skills),
        "total_mcps": len(mcps),
    }


@router.get("/roles")
async def get_local_roles(
    _: dict = Depends(get_current_admin),
) -> dict[str, Any]:
    """
    获取本地角色列表及权限。
    从 roles、role_permissions 表读取，L2 为唯一真相来源。
    """
    tenant_id = _load_nexus_tenant()
    conn = get_connection()
    try:
        roles_rows = conn.execute(
            "SELECT id, role_id, name FROM roles WHERE tenant_id = ? ORDER BY name",
            (tenant_id,),
        ).fetchall()
        perms_rows = conn.execute(
            "SELECT role_id, item_id FROM role_permissions"
        ).fetchall()

        perms_by_role: dict[str, list[str]] = {}
        for r in perms_rows:
            rid, iid = (r[0] or "").strip(), (r[1] or "").strip()
            if not rid or not iid:
                continue
            if rid not in perms_by_role:
                perms_by_role[rid] = []
            perms_by_role[rid].append(iid)

        roles = []
        for row in roles_rows:
            row_id, role_id, name = row[0], row[1], row[2]
            roles.append({
                "id": row_id,
                "role_id": role_id,
                "name": name or role_id,
                "allowed_items": perms_by_role.get(role_id, []),
            })
        return {"success": True, "roles": roles}
    finally:
        conn.close()


@router.post("/roles/assign")
async def assign_role_permissions(
    body: dict[str, Any] = Body(...),
    _: dict = Depends(get_current_admin),
) -> dict[str, Any]:
    """
    在 L2 本地数据库中为指定角色分配 item_ids 权限。
    Body: { "role_id": "r_dev", "item_ids": ["skill:xxx", "mcp:yyy", ...] }
    若角色不存在则自动创建。
    """
    role_id = (body.get("role_id") or "").strip()
    item_ids = body.get("item_ids")
    if not role_id:
        raise api_error(400, ERR_BAD_REQUEST_002, "role_id is required")
    if not isinstance(item_ids, list):
        item_ids = []

    tenant_id = _load_nexus_tenant()
    conn = get_connection()
    try:
        # 确保角色存在
        row = conn.execute(
            "SELECT id FROM roles WHERE tenant_id = ? AND role_id = ? LIMIT 1",
            (tenant_id, role_id),
        ).fetchone()
        if not row:
            role_row_id = f"role-{secrets.token_hex(8)}"
            conn.execute(
                "INSERT INTO roles (id, tenant_id, role_id, name) VALUES (?, ?, ?, ?)",
                (role_row_id, tenant_id, role_id, role_id),
            )
            conn.commit()

        # 替换该角色全部权限
        conn.execute("DELETE FROM role_permissions WHERE role_id = ?", (role_id,))
        for item_id in item_ids:
            iid = str(item_id).strip()
            if not iid:
                continue
            conn.execute(
                "INSERT INTO role_permissions (id, role_id, item_id) VALUES (?, ?, ?)",
                (str(uuid.uuid4()), role_id, iid),
            )
        conn.commit()

        # 刷新 PolicyEnforcer 内存缓存
        from core.policy_enforcer import refresh_policies
        refresh_policies()

        logger.info("[LocalAdmin] 角色 %s 权限已更新 item_ids=%d", role_id, len(item_ids))
        return {
            "success": True,
            "role_id": role_id,
            "assigned_count": len([i for i in item_ids if str(i).strip()]),
        }
    finally:
        conn.close()
