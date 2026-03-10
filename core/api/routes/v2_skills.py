"""
Jachin Nexus V2 - 技能配置 API

GET /api/v2/skills/{skill_id}/config：返回 skill_registry 中的键值对。
PUT /api/v2/skills/{skill_id}/config：更新键值对到注册表。
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request, Body

from core.errors import ERR_AUTH_002, api_error
from core.skill_registry import get_skill_config, update_skill_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/skills", tags=["skills"])


def _get_sub_account_id(request: Request) -> str | None:
    sub = request.headers.get("X-Sub-Account-Id", "").strip()
    if sub:
        return sub
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip() or None
    return None


@router.get("/{skill_id}/config")
async def get_skill_config_api(request: Request, skill_id: str) -> dict[str, Any]:
    """
    获取技能在 Jachin 注册表中的 K-V 配置。
    skill_id 可为 item_id 或 jpp:xxx 格式。
    未配对（无 X-Sub-Account-Id）时仍返回配置，供本地技能设置页使用。
    """
    config = get_skill_config(skill_id)
    return {"config": config, "skill_id": skill_id}


@router.put("/{skill_id}/config")
async def put_skill_config_api(
    request: Request,
    skill_id: str,
    body: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """
    更新技能在 Jachin 注册表中的 K-V 配置。
    Body 为键值对字典，如 {"JD_template": "...", "strict_mode": true}。
    需 X-Sub-Account-Id。
    """
    sub_account_id = _get_sub_account_id(request)
    if not sub_account_id:
        raise api_error(401, ERR_AUTH_002, "需要 X-Sub-Account-Id")

    result = update_skill_config(skill_id, body)
    return {
        "ok": True,
        "skill_id": skill_id,
        "updated": result["updated"],
        "inserted": result["inserted"],
    }
