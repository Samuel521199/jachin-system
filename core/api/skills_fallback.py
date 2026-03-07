"""
技能 API 降级路由：当 skills.py 因 Ray/PluginManager 依赖失败时使用。
提供 GET /api/v3/skills 列表与 POST /{skill_id}/execute 执行，从 ~/.jachin/inventory/skills/ 读取。
"""

from fastapi import APIRouter, HTTPException
from pathlib import Path
from typing import List, Dict, Any
from pydantic import BaseModel

router = APIRouter(prefix="/api/v3/skills", tags=["skills"])


class SkillExecutionRequest(BaseModel):
    capability_name: str
    input_data: Dict[str, Any] = {}


def _inventory_skill_to_info(inv: dict) -> dict:
    """将 inventory 技能格式转为 SkillInfo 兼容格式"""
    perms = inv.get("permissions", [])
    if isinstance(perms, list):
        perm_items = [
            {"id": p.get("scope", p) if isinstance(p, dict) else str(p),
             "label": p.get("scope", p) if isinstance(p, dict) else str(p)}
            for p in perms
        ]
    else:
        perm_items = []
    params = inv.get("params", [])
    caps = [{"name": p if isinstance(p, str) else p.get("name", ""), "description": ""} for p in params] if params else [{"name": "execute", "description": inv.get("description", "")}]
    return {
        "skill_id": inv.get("id", inv.get("item_id", "")),
        "name": inv.get("name", ""),
        "version": inv.get("version", "1.0.0"),
        "description": inv.get("description"),
        "status": "installed",
        "capabilities": caps,
        "permissions": perm_items,
    }


def _find_inventory_skill(skill_id: str) -> Dict[str, Any] | None:
    """按 skill_id 或 item_id 查找 inventory 技能"""
    try:
        from core.inventory_scanner import registered_local_skills
        inv = registered_local_skills.get(skill_id)
        if inv:
            return inv
        for v in registered_local_skills.values():
            if v.get("item_id") == skill_id or v.get("id") == skill_id:
                return v
    except Exception:
        pass
    return None


@router.get("")
async def list_skills_fallback() -> List[Dict[str, Any]]:
    """仅从 inventory 返回技能列表（PluginManager 不可用时的降级）"""
    try:
        from core.inventory_scanner import registered_local_skills
        return [_inventory_skill_to_info(inv) for inv in registered_local_skills.values()]
    except Exception:
        return []


@router.post("/{skill_id}/execute")
async def execute_skill_fallback(skill_id: str, request: SkillExecutionRequest) -> Dict[str, Any]:
    """执行 inventory 中的 Wasm 技能（PluginManager 不可用时的降级）"""
    import sys
    print(f"[Skill Execute] [L2 Fallback] 开始 skill_id={skill_id} capability={request.capability_name}", file=sys.stderr, flush=True)
    inv = _find_inventory_skill(skill_id)
    if not inv:
        print(f"[Skill Execute] [L2 Fallback] 未找到 skill_id={skill_id}", file=sys.stderr, flush=True)
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} not found in inventory")
    wasm_path = inv.get("wasm_path")
    if not wasm_path or not Path(wasm_path).exists():
        print(f"[Skill Execute] [L2 Fallback] Wasm 不存在 skill_id={skill_id} path={wasm_path}", file=sys.stderr, flush=True)
        return {"success": False, "result": None, "error": "Wasm file not found"}
    try:
        from core.wasm_runner import run_wasm_plugin, WasmExecutionError
        stdin_json = {
            "capability": request.capability_name,
            **request.input_data,
        }
        result = run_wasm_plugin(wasm_path, stdin_json=stdin_json)
        print(f"[Skill Execute] [L2 Fallback] 完成 skill_id={skill_id} result_len={len(str(result))}", file=sys.stderr, flush=True)
        if result is None:
            return {"success": False, "result": None, "error": "WASM execution returned None"}
        if isinstance(result, str):
            import json
            if not result.strip():
                return {"success": True, "result": {"text": "[执行完成但无输出，请检查 Wasm 插件或 execute ABI 返回值]"}, "error": None}
            try:
                out = json.loads(result)
                return {"success": True, "result": out, "error": None}
            except json.JSONDecodeError:
                return {"success": True, "result": {"text": result}, "error": None}
        return {"success": True, "result": result if isinstance(result, dict) else {"value": result}, "error": None}
    except WasmExecutionError as e:
        print(f"[Skill Execute] [L2 Fallback] WASM 异常 skill_id={skill_id} error={e} wasm_details={getattr(e, 'wasm_details', '')[:200]}", file=sys.stderr, flush=True)
        return {"success": False, "result": None, "error": str(e), "wasm_details": getattr(e, "wasm_details", "")}
    except Exception as e:
        print(f"[Skill Execute] [L2 Fallback] 异常 skill_id={skill_id} error={e}", file=sys.stderr, flush=True)
        return {"success": False, "result": None, "error": str(e)}
