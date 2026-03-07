"""
Jachin Nexus V2 - L2 本地数字仓库 API

暴露仓库管理接口，供 L2 面板或 L3 查阅侧载技能、触发热重载。
MVP: 权限分配已隐藏，有 X-Sub-Account-Id 即全量下发所有技能。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from core.errors import ERR_AUTH_001, ERR_AUTH_002, ERR_AUTH_003, ERR_NOT_FOUND_003, api_error
from core.inventory_scanner import (
    ensure_inventory_dirs,
    registered_local_skills,
    reload_inventory,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/inventory", tags=["inventory"])


def _get_sub_account_id(request: Request) -> Optional[str]:
    """从 X-Sub-Account-Id 或 Authorization: Bearer 提取子账号 ID"""
    sub = request.headers.get("X-Sub-Account-Id", "").strip()
    if sub:
        return sub
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip() or None
    return None


def _skill_by_item_id(item_id: str) -> dict[str, Any] | None:
    """按 item_id（目录名）查找技能。"""
    for s in registered_local_skills.values():
        if s.get("item_id") == item_id:
            return s
    return None


def _normalize_skill_for_api(skill: dict[str, Any]) -> dict[str, Any]:
    """
    统一标记：确保 PRIVATE 侧载包与 PUBLIC 同步包结构一致，L3 可无差别拉取。
    每个技能必有 origin（SIDE_LOAD | L1_SYNC）和 is_private。
    """
    out = dict(skill)
    if "origin" not in out:
        out["origin"] = "L1_SYNC"  # 有 .sync_meta 的默认为 L1 同步
    if "is_private" not in out:
        out["is_private"] = out.get("origin") == "SIDE_LOAD"
    return out


@router.get("/skills")
async def list_inventory_skills(request: Request) -> dict[str, Any]:
    """
    返回本地 Wasm 技能列表。
    MVP: X-Sub-Account-Id 可选；无身份时直接返回全部技能，不再按角色过滤。
    """
    sub_account_id = _get_sub_account_id(request)
    if not sub_account_id:
        # TODO(MVP): 无身份时全量返回，后续版本再开启鉴权过滤
        allowed = [_normalize_skill_for_api(s) for s in registered_local_skills.values()]
        logger.debug("[Inventory API] GET /skills 无身份，全量返回 count=%d", len(allowed))
        return {"skills": allowed, "count": len(allowed)}

    from core.db import get_connection

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, is_active FROM sub_accounts WHERE id = ?",
            (sub_account_id,),
        ).fetchone()
        if not row:
            raise api_error(
                401,
                ERR_AUTH_002,
                "未找到有效的子账号凭证",
                detail="请携带 X-Sub-Account-Id 或 Authorization: Bearer <session_token>",
            )
        if row[1] == 0:
            raise api_error(403, ERR_AUTH_003, "您的账号已被禁用，无权访问技能仓库")
    finally:
        conn.close()

    # MVP: 权限分配已隐藏，直接全量下发所有技能
    allowed = [_normalize_skill_for_api(s) for s in registered_local_skills.values()]
    logger.debug("[Inventory API] GET /skills sub=%s 全量返回 count=%d", sub_account_id[:16], len(allowed))
    return {"skills": allowed, "count": len(allowed)}


@router.get("/skills/{item_id}/download")
async def download_skill_wasm(request: Request, item_id: str) -> FileResponse:
    """
    返回技能 Wasm 二进制流，供 L3 冷启动静默拉取。
    MVP: X-Sub-Account-Id 可选；无身份时直接放行。
    响应头 X-SHA256 携带校验值。
    """
    sub_account_id = _get_sub_account_id(request)
    skill = _skill_by_item_id(item_id)
    if not skill:
        raise api_error(404, ERR_NOT_FOUND_003, f"Skill item_id={item_id} not found")

    # MVP: 权限分配已隐藏，有身份即放行
    wasm_path = Path(skill["wasm_path"])
    if not wasm_path.exists():
        raise api_error(404, ERR_NOT_FOUND_003, "Wasm file not found on L2")

    sha256_val = skill.get("sha256", "")
    return FileResponse(
        path=wasm_path,
        media_type="application/wasm",
        headers={"X-SHA256": sha256_val} if sha256_val else {},
        filename=wasm_path.name,
    )


@router.post("/trigger-sync")
async def trigger_sync_from_l1(request: Request) -> dict[str, Any]:
    """
    L3 启动时调用：触发 L2 从 L1 拉取 manifest 并下载技能，完成后 L3 再拉取。
    需 X-Sub-Account-Id（已审批的 L3 节点）。
    """
    sub_account_id = _get_sub_account_id(request)
    if not sub_account_id:
        return {"ok": False, "error": "需要 X-Sub-Account-Id"}
    try:
        from core.db import get_connection
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT id FROM sub_accounts WHERE id = ? AND is_active = 1",
                (sub_account_id,),
            ).fetchone()
            if not row:
                return {"ok": False, "error": "无效的子账号"}
        finally:
            conn.close()

        from core.sync_daemon import CloudSyncDaemon
        daemon = CloudSyncDaemon()
        if not daemon._is_configured():
            logger.info("[Inventory API] trigger-sync: L2 未配对 L1，跳过")
            await reload_inventory()
            return {"ok": True, "synced_from_l1": False, "message": "L2 未配对 L1，仅重载本地"}
        await daemon.run_sync_cycle()
        logger.info("[Inventory API] trigger-sync: L1 同步完成，L3 可拉取技能")
        return {"ok": True, "synced_from_l1": True, "message": "同步完成"}
    except Exception as e:
        logger.exception("[Inventory API] trigger-sync 失败: %s", e)
        return {"ok": False, "error": str(e)}


@router.post("/reload")
async def trigger_reload() -> dict[str, Any]:
    """
    热重载：触发 InventoryScanner 重新扫盘。
    用户手动往 ~/.jachin/inventory/ 拖入新文件后，可调用此接口让 L2 立刻生效。
    """
    logger.info("[Inventory API] 收到热重载请求")
    try:
        result = await reload_inventory()
        logger.info("[Inventory API] 热重载完成 %s", result)
        return result
    except Exception as e:
        logger.exception("[Inventory API] 热重载失败 err=%s", e)
        return {
            "ok": False,
            "error": str(e),
            "mcps_injected": 0,
            "skills_found": 0,
        }
