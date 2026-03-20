"""
Jachin Nexus V2 - L2 本地数字仓库 API

暴露仓库管理接口，供 L2 面板或 L3 查阅侧载技能、触发热重载。
支持技能卸载与 GC（垃圾回收）：DELETE /skills/{item_id}?purge_data=true/false
支持技能/MCP 隐藏：POST /skills/{id}/hide、/unhide；POST /l3_mcps/{id}/hide、/unhide
支持 MCP 删除：DELETE /l3_mcps/{item_id}
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Request, Query
from fastapi.responses import FileResponse

from core.errors import ERR_AUTH_001, ERR_AUTH_002, ERR_AUTH_003, ERR_NOT_FOUND_003, api_error
from core.inventory_scanner import (
    ensure_inventory_dirs,
    L3_MCPS_DIR,
    registered_local_skills,
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
    logger.info("[Inventory API] L3 请求技能清单 sub=%s", (_get_sub_account_id(request) or "")[:16])
    sub_account_id = _get_sub_account_id(request)
    if not sub_account_id:
        # TODO(MVP): 无身份时全量返回，后续版本再开启鉴权过滤（排除已隐藏）
        from core.hidden_inventory import get_hidden_skills
        hidden = get_hidden_skills()
        allowed = [
            _normalize_skill_for_api(s)
            for s in registered_local_skills.values()
            if s.get("item_id") not in hidden
        ]
        logger.debug("[Inventory API] GET /skills 无身份，返回 count=%d", len(allowed))
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

    # MVP: 权限分配已隐藏，直接全量下发所有技能（排除已隐藏）
    from core.hidden_inventory import get_hidden_skills
    hidden = get_hidden_skills()
    allowed = [
        _normalize_skill_for_api(s)
        for s in registered_local_skills.values()
        if s.get("item_id") not in hidden
    ]
    logger.debug("[Inventory API] GET /skills sub=%s 返回 count=%d (hidden=%d)", sub_account_id[:16], len(allowed), len(hidden))
    return {"skills": allowed, "count": len(allowed)}


@router.get("/skills/hidden")
async def list_hidden_skills(request: Request) -> dict[str, Any]:
    """列出已隐藏的技能 item_id，供前端「已隐藏」Tab 展示与取消隐藏。"""
    from core.hidden_inventory import get_hidden_skills
    hidden = list(get_hidden_skills())
    return {"item_ids": hidden, "count": len(hidden)}


@router.get("/skills/{item_id}/download")
async def download_skill_wasm(request: Request, item_id: str) -> FileResponse:
    """
    返回技能 Wasm 二进制流，供 L3 冷启动静默拉取。
    MVP: X-Sub-Account-Id 可选；无身份时直接放行。
    响应头 X-SHA256 携带校验值。
    """
    logger.info("[Inventory API] L3 请求下载技能 item_id=%s sub=%s", item_id, (_get_sub_account_id(request) or "")[:16])
    sub_account_id = _get_sub_account_id(request)
    skill = _skill_by_item_id(item_id)
    if not skill:
        raise api_error(404, ERR_NOT_FOUND_003, f"Skill item_id={item_id} not found")

    from core.hidden_inventory import is_hidden_skill
    if is_hidden_skill(item_id):
        raise api_error(404, ERR_NOT_FOUND_003, f"Skill item_id={item_id} not found")

    # MVP: 权限分配已隐藏，有身份即放行
    wasm_path = Path(skill["wasm_path"])
    logger.info("[Inventory API] 即将读取 Wasm 文件 item_id=%s path=%s", item_id, wasm_path)
    if not wasm_path.exists():
        logger.warning("[Inventory API] Wasm 不存在 item_id=%s path=%s", item_id, wasm_path)
        raise api_error(404, ERR_NOT_FOUND_003, "Wasm file not found on L2")

    sha256_val = skill.get("sha256", "")
    try:
        size = wasm_path.stat().st_size
    except OSError:
        size = 0
    logger.info("[Inventory API] 技能下载成功 item_id=%s size=%d", item_id, size)
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
            from core.inventory_reloader import request_reload
            await request_reload()
            return {"ok": True, "synced_from_l1": False, "message": "L2 未配对 L1，仅重载本地"}
        await daemon.run_sync_cycle()
        logger.info("[Inventory API] trigger-sync: L1 同步完成，L3 可拉取技能")
        return {"ok": True, "synced_from_l1": True, "message": "同步完成"}
    except Exception as e:
        logger.exception("[Inventory API] trigger-sync 失败: %s", e)
        return {"ok": False, "error": str(e)}


@router.post("/skills/{item_id}/hide")
async def hide_skill(request: Request, item_id: str) -> dict[str, Any]:
    """隐藏技能：从列表中排除，L3 不可见。需 X-Sub-Account-Id 或管理员。"""
    sub_account_id = _get_sub_account_id(request)
    if not sub_account_id:
        return {"ok": False, "error": "需要 X-Sub-Account-Id"}

    skill = _skill_by_item_id(item_id)
    if not skill:
        return {"ok": False, "error": f"Skill item_id={item_id} not found", "item_id": item_id}

    from core.hidden_inventory import hide_skill as do_hide
    if do_hide(item_id):
        return {"ok": True, "message": "技能已隐藏", "item_id": item_id}
    return {"ok": True, "message": "技能已处于隐藏状态", "item_id": item_id}


@router.post("/skills/{item_id}/unhide")
async def unhide_skill(request: Request, item_id: str) -> dict[str, Any]:
    """取消隐藏技能。需 X-Sub-Account-Id。"""
    sub_account_id = _get_sub_account_id(request)
    if not sub_account_id:
        return {"ok": False, "error": "需要 X-Sub-Account-Id"}

    from core.hidden_inventory import unhide_skill as do_unhide
    if do_unhide(item_id):
        return {"ok": True, "message": "技能已取消隐藏", "item_id": item_id}
    return {"ok": True, "message": "技能未在隐藏列表中", "item_id": item_id}


@router.delete("/skills/{item_id}")
async def uninstall_skill(
    request: Request,
    item_id: str,
    purge_data: bool = Query(False, description="是否清理业务数据（注册表、数据卷）"),
) -> dict[str, Any]:
    """
    卸载技能：移入回收站（软删除），非彻底删除。
    - 技能在 inventory/cache/builtin：移入 ~/.jachin/recycle_bin/
    - 回收站中可恢复或彻底删除
    需 X-Sub-Account-Id。
    """
    sub_account_id = _get_sub_account_id(request)
    if not sub_account_id:
        return {"ok": False, "error": "需要 X-Sub-Account-Id"}

    try:
        from core.recycle_bin import move_to_recycle_bin
        result = move_to_recycle_bin(item_id, purge_data)
        if not result.get("ok"):
            return {"ok": False, "error": result.get("error", "移入回收站失败"), "item_id": item_id}
        if result.get("source") == "inventory":
            from core.inventory_reloader import request_reload
            await request_reload()
        return {**result, "message": "已移入回收站"}
    except Exception as e:
        logger.exception("[Inventory API] 移入回收站失败 item_id=%s err=%s", item_id, e)
        return {"ok": False, "error": str(e), "item_id": item_id}


@router.get("/l3_mcps")
async def list_l3_mcps(request: Request) -> dict[str, Any]:
    """
    返回 L3_LOCAL MCP 列表（路径 3）。
    供 L3 mcp_sync 拉取，下载到 l3_mcp_cache 后动态加载。
    """
    logger.info("[Inventory API] L3 请求 MCP 清单 sub=%s", (_get_sub_account_id(request) or "")[:16])
    mcps: list[dict[str, Any]] = []
    try:
        sub_account_id = _get_sub_account_id(request)
        if not sub_account_id:
            from core.db import get_connection
            conn = get_connection()
            try:
                conn.execute("SELECT 1 FROM sub_accounts LIMIT 1")
            except Exception:
                pass
            finally:
                conn.close()

        if not L3_MCPS_DIR.exists():
            return {"mcps": mcps, "count": 0}

        from core.hidden_inventory import get_hidden_l3_mcps
        hidden_mcps = get_hidden_l3_mcps()

        for subdir in L3_MCPS_DIR.iterdir():
            if not subdir.is_dir():
                continue
            item_id = subdir.name
            if item_id in hidden_mcps:
                continue
            plugin_path = subdir / "plugin.json"
            if not plugin_path.exists():
                continue
            try:
                plugin = json.loads(plugin_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            tools_list = plugin.get("tools") or []
            if isinstance(tools_list, dict):
                tools_list = list(tools_list.values()) if tools_list else []
            tool_ids = []
            for t in tools_list:
                tid = t.get("id") if isinstance(t, dict) else str(t)
                if tid:
                    tool_ids.append(f"mcp:{tid}" if not tid.startswith("mcp:") else tid)
            mcps.append({
                "item_id": item_id,
                "name": plugin.get("name", item_id),
                "description": plugin.get("description", ""),
                "tools": tool_ids,
                "entry": "tools",
                "version": plugin.get("version", "1.0.0"),
            })
        return {"mcps": mcps, "count": len(mcps)}
    except Exception as e:
        logger.warning("[Inventory API] list_l3_mcps 异常: %s", e, exc_info=True)
        return {"mcps": [], "count": 0}


@router.get("/l3_mcps/hidden")
async def list_hidden_l3_mcps(request: Request) -> dict[str, Any]:
    """列出已隐藏的 L3 MCP item_id，供前端「已隐藏」Tab 展示与取消隐藏。"""
    from core.hidden_inventory import get_hidden_l3_mcps
    hidden = list(get_hidden_l3_mcps())
    return {"item_ids": hidden, "count": len(hidden)}


@router.delete("/l3_mcps/{item_id}")
async def delete_l3_mcp(request: Request, item_id: str) -> dict[str, Any]:
    """删除 L3_LOCAL MCP：从 inventory/l3_mcps 移除。需 X-Sub-Account-Id。"""
    sub_account_id = _get_sub_account_id(request)
    if not sub_account_id:
        return {"ok": False, "error": "需要 X-Sub-Account-Id"}

    import shutil
    dest = L3_MCPS_DIR / item_id
    if not dest.exists() or not dest.is_dir():
        return {"ok": False, "error": f"L3 MCP item_id={item_id} not found", "item_id": item_id}

    try:
        logger.info("[Inventory API] 即将删除 L3 MCP item_id=%s dest=%s", item_id, dest)
        shutil.rmtree(dest)
        from core.hidden_inventory import unhide_l3_mcp
        unhide_l3_mcp(item_id)  # 若在隐藏列表则移除
        from core.inventory_reloader import request_reload
        await request_reload()
        return {"ok": True, "message": "MCP 已删除", "item_id": item_id}
    except Exception as e:
        logger.exception("[Inventory API] 删除 MCP 失败 item_id=%s err=%s", item_id, e)
        return {"ok": False, "error": str(e), "item_id": item_id}


@router.post("/l3_mcps/{item_id}/hide")
async def hide_l3_mcp(request: Request, item_id: str) -> dict[str, Any]:
    """隐藏 L3_LOCAL MCP：从列表中排除，L3 不可见。需 X-Sub-Account-Id。"""
    sub_account_id = _get_sub_account_id(request)
    if not sub_account_id:
        return {"ok": False, "error": "需要 X-Sub-Account-Id"}

    dest = L3_MCPS_DIR / item_id
    if not dest.exists() or not dest.is_dir():
        return {"ok": False, "error": f"L3 MCP item_id={item_id} not found", "item_id": item_id}

    from core.hidden_inventory import hide_l3_mcp as do_hide
    if do_hide(item_id):
        return {"ok": True, "message": "MCP 已隐藏", "item_id": item_id}
    return {"ok": True, "message": "MCP 已处于隐藏状态", "item_id": item_id}


@router.post("/l3_mcps/{item_id}/unhide")
async def unhide_l3_mcp(request: Request, item_id: str) -> dict[str, Any]:
    """取消隐藏 L3_LOCAL MCP。需 X-Sub-Account-Id。"""
    sub_account_id = _get_sub_account_id(request)
    if not sub_account_id:
        return {"ok": False, "error": "需要 X-Sub-Account-Id"}

    from core.hidden_inventory import unhide_l3_mcp as do_unhide
    if do_unhide(item_id):
        return {"ok": True, "message": "MCP 已取消隐藏", "item_id": item_id}
    return {"ok": True, "message": "MCP 未在隐藏列表中", "item_id": item_id}


@router.get("/l3_mcps/{item_id}/download")
async def download_l3_mcp(request: Request, item_id: str):
    """
    下载 L3_LOCAL MCP 包（zip），供 L3 解压到 l3_mcp_cache。
    """
    logger.info("[Inventory API] L3 请求下载 MCP item_id=%s sub=%s", item_id, (_get_sub_account_id(request) or "")[:16])
    sub_account_id = _get_sub_account_id(request)
    dest = L3_MCPS_DIR / item_id
    logger.info("[Inventory API] 即将打包 MCP item_id=%s dest=%s", item_id, dest)
    if not dest.exists() or not dest.is_dir():
        raise api_error(404, ERR_NOT_FOUND_003, f"L3 MCP item_id={item_id} not found")

    from core.hidden_inventory import is_hidden_l3_mcp
    if is_hidden_l3_mcp(item_id):
        raise api_error(404, ERR_NOT_FOUND_003, f"L3 MCP item_id={item_id} not found")

    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in dest.rglob("*"):
            if f.is_file():
                arcname = f.relative_to(dest)
                zf.write(f, arcname)
    buf.seek(0)
    logger.info("[Inventory API] MCP 打包完成 item_id=%s size=%d", item_id, len(buf.getvalue()))
    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={item_id}.zip"},
    )


@router.post("/reload")
async def trigger_reload() -> dict[str, Any]:
    """
    热重载：触发 InventoryScanner 重新扫盘。
    用户手动往 ~/.jachin/inventory/ 拖入新文件后，可调用此接口让 L2 立刻生效。
    """
    logger.info("[Inventory API] 收到热重载请求")
    try:
        from core.inventory_reloader import request_reload
        result = await request_reload()
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
