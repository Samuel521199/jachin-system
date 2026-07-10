"""
技能管理API
Skills Management API
"""

from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, status, Request
from typing import List, Optional, Dict, Any
from pydantic import BaseModel

# v5.0: core.memory.schema (PostgreSQL) 已废弃
from core.system.plugin_manager import PluginManager, get_plugin_manager
from core.runtime.skill_runner import SkillRunner
from core.runtime.skill_loader import SkillLoader
from core.runtime.manifest import ManifestError
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v3/skills", tags=["skills"])

# 技能执行统计（内存存储，供 LiveTile 实时状态）
_skill_stats: dict = {}  # skill_id -> { executions: int, last_at: str }


def _record_skill_execution(skill_id: str) -> None:
    import time
    if skill_id not in _skill_stats:
        _skill_stats[skill_id] = {"executions": 0, "last_at": None}
    _skill_stats[skill_id]["executions"] += 1
    _skill_stats[skill_id]["last_at"] = time.strftime("%Y-%m-%d %H:%M", time.localtime())


# Pydantic模型
class PermissionItem(BaseModel):
    """权限项（供 LiveTile 悬停展示）"""
    id: str
    label: str


class SkillInfo(BaseModel):
    """技能信息"""
    skill_id: str
    name: str
    version: str
    description: Optional[str] = None
    status: str
    capabilities: List[Dict[str, Any]] = []
    permissions: List[Dict[str, str]] = []
    execution_count: Optional[int] = None
    last_executed_at: Optional[str] = None
    item_id: Optional[str] = None  # L2 inventory 目录名，卸载时使用


class SkillExecutionRequest(BaseModel):
    """技能执行请求"""
    capability_name: str
    input_data: Dict[str, Any]


class SkillExecutionResponse(BaseModel):
    """技能执行响应"""
    success: bool
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


# 依赖注入
async def get_skill_registry(request: Request) -> PluginManager:
    """获取技能管理实例（PluginManager 单例）"""
    if hasattr(request.app.state, "plugin_manager") and request.app.state.plugin_manager:
        return request.app.state.plugin_manager
    logger.warning("PluginManager not in app state, using get_plugin_manager")
    pm = get_plugin_manager()
    pm.load_skills()
    return pm


async def get_skill_runner(registry: PluginManager = Depends(get_skill_registry)) -> SkillRunner:
    """获取技能运行器实例"""
    return SkillRunner(registry)


@router.get("/stats")
async def get_skill_stats():
    """获取技能执行统计（供 LiveTile 实时状态）"""
    return {"stats": _skill_stats}


@router.get("/{skill_id}/status")
async def get_skill_status(skill_id: str):
    """获取单个技能的业务状态（供 LiveTile 展示如「已拦截 3 次」）"""
    if skill_id in _skill_stats:
        s = _skill_stats[skill_id]
        return {
            "skill_id": skill_id,
            "executions": s["executions"],
            "last_executed_at": s["last_at"],
        }
    return {"skill_id": skill_id, "executions": 0, "last_executed_at": None}


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
        "item_id": inv.get("item_id"),
        "name": inv.get("name", ""),
        "version": inv.get("version", "1.0.0"),
        "description": inv.get("description"),
        "status": "installed",
        "capabilities": caps,
        "permissions": perm_items,
    }


@router.get("", response_model=List[SkillInfo])
async def list_skills(
    registry: PluginManager = Depends(get_skill_registry)
):
    """
    列出所有已安装的技能。
    合并 skills_repo（PluginManager）与 ~/.jachin/inventory/skills/（L1 同步 + 侧载），
    确保 Skill Matrix 能展示 L1 商店同步下来的技能。
    """
    try:
        skills = await registry.list_skills()
        # 合并 inventory 技能（L1 同步、侧载）
        try:
            from core.inventory_scanner import registered_local_skills
            for inv in registered_local_skills.values():
                sid = inv.get("id", inv.get("item_id", ""))
                if not sid:
                    continue
                if any(s.get("skill_id") == sid for s in skills):
                    continue
                skills.append(_inventory_skill_to_info(inv))
        except Exception as e:
            logger.debug("合并 inventory 技能时跳过: %s", e)
        for s in skills:
            sid = s.get("skill_id", "")
            if sid in _skill_stats:
                s["execution_count"] = _skill_stats[sid]["executions"]
                s["last_executed_at"] = _skill_stats[sid]["last_at"]
        return skills
    except Exception as e:
        logger.error(f"Failed to list skills: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list skills: {str(e)}"
        )


@router.get("/{skill_id}", response_model=SkillInfo)
async def get_skill(
    skill_id: str,
    registry: PluginManager = Depends(get_skill_registry)
):
    """
    获取技能详细信息

    Args:
        skill_id: 技能ID

    Returns:
        SkillInfo: 技能信息
    """
    try:
        manifest = await registry.get_skill(skill_id)
        if not manifest:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Skill {skill_id} not found"
            )

        skills = await registry.list_skills()
        skill_info = next((s for s in skills if s["skill_id"] == skill_id), None)

        if not skill_info:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Skill {skill_id} not found"
            )

        return skill_info

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get skill {skill_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get skill: {str(e)}"
        )


@router.post("", status_code=status.HTTP_201_CREATED)
async def install_skill(
    skill_file: UploadFile = File(...),
    overwrite: bool = False,
    loader: SkillLoader = Depends(lambda: SkillLoader()),
    registry: PluginManager = Depends(get_skill_registry)
):
    """
    安装技能（从zip包）并动态加载到系统（无需重启）

    Args:
        skill_file: 技能zip包文件
        overwrite: 是否覆盖已存在的技能

    Returns:
        Dict: 安装结果
    """
    try:
        # 保存上传的文件到临时目录
        import tempfile
        import shutil
        from pathlib import Path

        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp_file:
            shutil.copyfileobj(skill_file.file, tmp_file)
            tmp_path = tmp_file.name

        try:
            # 安装技能到文件系统
            skill_id = loader.install_skill(tmp_path, overwrite=overwrite)

            if not skill_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to install skill"
                )

            # 动态加载技能到注册表（无需重启）
            loaded = await registry.load_skill(skill_id)
            if not loaded:
                logger.warning(f"Skill {skill_id} installed but failed to load into registry")
                return {
                    "success": True,
                    "skill_id": skill_id,
                    "message": f"Skill {skill_id} installed successfully, but loading into registry failed. Please use /reload endpoint.",
                    "loaded": False
                }

            return {
                "success": True,
                "skill_id": skill_id,
                "message": f"Skill {skill_id} installed and loaded successfully",
                "loaded": True
            }
        finally:
            # 清理临时文件
            Path(tmp_path).unlink(missing_ok=True)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to install skill: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to install skill: {str(e)}"
        )


@router.delete("/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
async def uninstall_skill(
    skill_id: str,
    registry: PluginManager = Depends(get_skill_registry),
    runner: SkillRunner = Depends(get_skill_runner),
    loader: SkillLoader = Depends(lambda: SkillLoader())
):
    """
    卸载技能

    Args:
        skill_id: 技能ID

    Returns:
        204 No Content
    """
    try:
        # 先卸载运行时
        await runner.unload_skill(skill_id)
        if hasattr(registry, "unload_skill"):
            registry.unload_skill(skill_id)

        # 从文件系统删除
        success = loader.uninstall_skill(skill_id)

        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Skill {skill_id} not found"
            )

        return None

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to uninstall skill {skill_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to uninstall skill: {str(e)}"
        )


@router.post("/{skill_id}/execute", response_model=SkillExecutionResponse)
async def execute_skill(
    skill_id: str,
    request: SkillExecutionRequest,
    runner: SkillRunner = Depends(get_skill_runner)
):
    """
    执行技能能力

    Args:
        skill_id: 技能ID
        request: 执行请求

    Returns:
        SkillExecutionResponse: 执行结果
    """
    try:
        result = await runner.execute_capability(
            skill_id=skill_id,
            capability_name=request.capability_name,
            input_data=request.input_data
        )
        _record_skill_execution(skill_id)
        return SkillExecutionResponse(
            success=result.get("success", False),
            result=result.get("result"),
            error=result.get("error")
        )

    except Exception as e:
        logger.error(
            f"Failed to execute capability {request.capability_name} for skill {skill_id}: {e}",
            exc_info=True
        )
        return SkillExecutionResponse(
            success=False,
            error=str(e)
        )


@router.post("/{skill_id}/enable", status_code=status.HTTP_200_OK)
async def enable_skill(
    skill_id: str,
    registry: PluginManager = Depends(get_skill_registry),
    runner: SkillRunner = Depends(get_skill_runner)
):
    """
    启用技能

    Args:
        skill_id: 技能ID

    Returns:
        Dict: 操作结果
    """
    try:
        # 更新状态
        success = await registry.update_skill_status(skill_id, "active")

        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Skill {skill_id} not found"
            )

        # 加载技能到运行时
        manifest = await registry.get_skill(skill_id)
        if manifest:
            skill_path = registry.get_skill_path(skill_id)
            if skill_path:
                await runner.load_skill(skill_id, str(skill_path / "manifest.yaml"))

        return {
            "success": True,
            "message": f"Skill {skill_id} enabled"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to enable skill {skill_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to enable skill: {str(e)}"
        )


@router.post("/{skill_id}/disable", status_code=status.HTTP_200_OK)
async def disable_skill(
    skill_id: str,
    registry: PluginManager = Depends(get_skill_registry),
    runner: SkillRunner = Depends(get_skill_runner)
):
    """
    禁用技能

    Args:
        skill_id: 技能ID

    Returns:
        Dict: 操作结果
    """
    try:
        # 卸载运行时
        await runner.unload_skill(skill_id)

        # 更新状态
        success = await registry.update_skill_status(skill_id, "disabled")

        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Skill {skill_id} not found"
            )

        return {
            "success": True,
            "message": f"Skill {skill_id} disabled"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to disable skill {skill_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to disable skill: {str(e)}"
        )


@router.get("/{skill_id}/health")
async def health_check(
    skill_id: str,
    runner: SkillRunner = Depends(get_skill_runner)
):
    """
    检查技能健康状态

    Args:
        skill_id: 技能ID

    Returns:
        Dict: 健康状态
    """
    try:
        is_healthy = await runner.health_check(skill_id)
        return {
            "skill_id": skill_id,
            "healthy": is_healthy
        }
    except Exception as e:
        logger.error(f"Failed to check health for skill {skill_id}: {e}", exc_info=True)
        return {
            "skill_id": skill_id,
            "healthy": False,
            "error": str(e)
        }


@router.post("/reload", status_code=status.HTTP_200_OK)
async def reload_skills(
    registry: PluginManager = Depends(get_skill_registry)
):
    """
    重新扫描并加载所有技能（动态刷新，无需重启）

    此端点会：
    1. 重新扫描技能存储库（包括 _bundled 目录）
    2. 加载新发现的技能到数据库
    3. 更新已存在技能的 manifest
    4. 清除缓存并重新加载

    Returns:
        Dict: 刷新结果统计
    """
    try:
        result = await registry.reload_skills()
        return {
            "success": True,
            "message": "Skills reloaded successfully",
            **result
        }
    except Exception as e:
        logger.error(f"Failed to reload skills: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to reload skills: {str(e)}"
        )


@router.post("/{skill_id}/reload", status_code=status.HTTP_200_OK)
async def reload_single_skill(
    skill_id: str,
    registry: PluginManager = Depends(get_skill_registry)
):
    """
    重新加载单个技能（动态刷新）

    Args:
        skill_id: 技能ID

    Returns:
        Dict: 操作结果
    """
    try:
        # 清除缓存
        if skill_id in registry._actors:
            del registry._actors[skill_id]
        if skill_id in registry._manifests:
            del registry._manifests[skill_id]

        # 重新加载
        loaded = await registry.load_skill(skill_id)

        if not loaded:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Skill {skill_id} not found or failed to load"
            )

        return {
            "success": True,
            "skill_id": skill_id,
            "message": f"Skill {skill_id} reloaded successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to reload skill {skill_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to reload skill: {str(e)}"
        )


@router.get("/debug/discovery", status_code=status.HTTP_200_OK)
async def debug_skill_discovery(
    registry: PluginManager = Depends(get_skill_registry)
):
    """
    调试端点：检查技能发现状态

    Returns:
        Dict: 调试信息
    """
    from pathlib import Path
    import os

    loader = registry.loader
    repo_path = loader.repo_path

    debug_info = {
        "repo_path": str(repo_path.absolute()),
        "repo_path_exists": repo_path.exists(),
        "bundled_path": str((repo_path / "_bundled").absolute()),
        "bundled_path_exists": (repo_path / "_bundled").exists(),
        "current_working_directory": os.getcwd(),
        "project_root_check": {
            "core_exists": Path("core").exists(),
            "skills_repo_exists": Path("skills_repo").exists(),
        }
    }

    # 检查 _bundled 目录内容
    bundled_dir = repo_path / "_bundled"
    if bundled_dir.exists():
        skill_dirs = [d for d in bundled_dir.iterdir() if d.is_dir()]
        debug_info["bundled_skill_dirs"] = [d.name for d in skill_dirs]
        debug_info["skill_details"] = []

        for skill_dir in skill_dirs:
            manifest_file = skill_dir / "manifest.yaml"
            skill_info = {
                "name": skill_dir.name,
                "manifest_exists": manifest_file.exists(),
            }

            if manifest_file.exists():
                try:
                    manifest = loader.load_skill_manifest(skill_dir.name)
                    if manifest:
                        skill_info["skill_id"] = manifest.skill_id
                        skill_info["name"] = manifest.name
                        skill_info["runtime_type"] = manifest.runtime_type
                        skill_info["parse_success"] = True
                    else:
                        skill_info["parse_success"] = False
                        skill_info["error"] = "load_skill_manifest returned None"
                except Exception as e:
                    skill_info["parse_success"] = False
                    skill_info["error"] = str(e)

            debug_info["skill_details"].append(skill_info)

    # 尝试发现技能
    try:
        discovered = loader.discover_skills()
        debug_info["discovered_skills"] = discovered
        debug_info["discovered_count"] = len(discovered)
    except Exception as e:
        debug_info["discovery_error"] = str(e)
        debug_info["discovered_skills"] = []
        debug_info["discovered_count"] = 0

    return debug_info
