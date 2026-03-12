"""
回收站 API - 与 move_to_recycle_bin 同进程，确保读写路径一致

L2 处理 DELETE 时执行 move_to_recycle_bin，回收站列表/恢复/彻底删除也由 L2 提供。
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request

from core.errors import ERR_AUTH_002, api_error

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/recycle-bin", tags=["recycle-bin"])


def _get_sub_account_id(request: Request) -> str | None:
    sub = request.headers.get("X-Sub-Account-Id", "").strip()
    if sub:
        return sub
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip() or None
    return None


@router.get("/skills")
async def list_recycle_bin_skills(request: Request) -> dict[str, Any]:
    """列出回收站中的技能（与 move_to_recycle_bin 同进程，路径一致）"""
    try:
        from core.recycle_bin import list_recycle_bin
        items = list_recycle_bin()
        return {"items": items, "count": len(items)}
    except Exception as e:
        logger.warning("[RecycleBin] list failed: %s", e)
        return {"items": [], "count": 0}


@router.post("/skills/{recycle_id}/restore")
async def restore_recycle_bin_skill(request: Request, recycle_id: str) -> dict[str, Any]:
    """从回收站恢复技能"""
    sub = _get_sub_account_id(request)
    if not sub:
        raise api_error(401, ERR_AUTH_002, "需要 X-Sub-Account-Id")
    try:
        from core.recycle_bin import restore_from_recycle_bin
        from core.inventory_scanner import reload_inventory
        result = restore_from_recycle_bin(recycle_id)
        if not result.get("ok"):
            return result
        await reload_inventory()
        return result
    except Exception as e:
        logger.warning("[RecycleBin] restore failed: %s", e)
        return {"ok": False, "error": str(e)}


@router.delete("/skills/{recycle_id}")
async def permanent_delete_recycle_bin_skill(request: Request, recycle_id: str) -> dict[str, Any]:
    """从回收站彻底删除"""
    sub = _get_sub_account_id(request)
    if not sub:
        raise api_error(401, ERR_AUTH_002, "需要 X-Sub-Account-Id")
    try:
        from core.recycle_bin import permanent_delete_from_recycle_bin
        return permanent_delete_from_recycle_bin(recycle_id)
    except Exception as e:
        logger.warning("[RecycleBin] permanent delete failed: %s", e)
        return {"ok": False, "error": str(e)}
