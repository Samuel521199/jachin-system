"""
技能运行器
Skill Runner
"""

import logging
import yaml
import ray
from typing import Dict, Any, Optional, Union
from pathlib import Path
from core.runtime.interfaces import SkillRuntime
from core.runtime.sandbox import DockerSandbox
from core.config import settings

logger = logging.getLogger(__name__)


def _get_capability_from_manifest(manifest: Any, capability_name: str) -> Optional[Dict]:
    """从 manifest（dict 或对象）中获取指定能力"""
    caps = manifest.get("capabilities", []) if isinstance(manifest, dict) else getattr(manifest, "capabilities", [])
    for cap in caps:
        name = cap.get("name") if isinstance(cap, dict) else getattr(cap, "name", "")
        if name == capability_name:
            return cap if isinstance(cap, dict) else (cap.dict() if hasattr(cap, "dict") else {})
    return None


class SkillRunner(SkillRuntime):
    """技能运行器（支持 PluginManager 与 Ray Actor）"""
    
    def __init__(self, skill_registry=None):
        """
        初始化技能运行器
        
        Args:
            skill_registry: PluginManager 或 SkillRegistry，若为 None 则使用空占位
        """
        from core.system.plugin_manager import PluginManager, get_plugin_manager
        self.registry = skill_registry or get_plugin_manager()
        self._is_plugin_manager = isinstance(self.registry, PluginManager)
        self.sandboxes: Dict[str, SkillRuntime] = {}
        
        # 加载沙箱配置
        self._load_sandbox_config()
    
    def _load_sandbox_config(self) -> None:
        """加载沙箱配置"""
        import yaml
        from pathlib import Path
        
        config_path = Path(settings.SKILLS_CONFIG_PATH)
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
                self.sandbox_config = config.get("runtime", {})
        else:
            self.sandbox_config = {}
    
    def _get_sandbox(self, skill_id: str, runtime_type: str) -> Optional[SkillRuntime]:
        """
        获取或创建沙箱实例
        
        Args:
            skill_id: 技能ID
            runtime_type: 运行时类型
        
        Returns:
            SkillRuntime: 沙箱实例，如果失败则返回None
        """
        cache_key = f"{skill_id}:{runtime_type}"
        
        if cache_key in self.sandboxes:
            return self.sandboxes[cache_key]
        
        # 根据运行时类型创建沙箱
        if runtime_type == "docker":
            config = self.sandbox_config.get("docker", {})
            sandbox = DockerSandbox(config)
            self.sandboxes[cache_key] = sandbox
            return sandbox
        elif runtime_type == "wasm":
            # TODO: 实现Wasm沙箱
            logger.warning("Wasm sandbox not yet implemented")
            return None
        elif runtime_type == "native":
            # TODO: 实现Native沙箱
            logger.warning("Native sandbox not yet implemented")
            return None
        elif runtime_type == "ray":
            # Ray 运行时：技能通过 PluginManager 和 PluginExecutor 执行
            # 不需要创建沙箱，返回 None 表示由 PluginExecutor 处理
            logger.info(f"Ray runtime for skill {skill_id} will be handled by PluginExecutor")
            return None
        else:
            logger.error(f"Unknown runtime type: {runtime_type}")
            return None
    
    async def load_skill(self, skill_id: str, manifest_path: str) -> bool:
        """
        加载技能
        
        Args:
            skill_id: 技能ID
            manifest_path: Manifest文件路径
        
        Returns:
            bool: 是否成功加载
        """
        try:
            manifest = await self.registry.get_skill(skill_id)
            if not manifest:
                logger.error(f"Manifest not found for skill {skill_id}")
                return False
            
            runtime_type = manifest.get("runtime", {}).get("type", "ray") if isinstance(manifest, dict) else getattr(manifest, "runtime_type", "ray")
            
            # Ray 运行时 / PluginManager: 由 Actor 处理，无需沙箱
            if runtime_type == "ray" or self._is_plugin_manager:
                logger.info(f"Ray/PluginManager skill {skill_id} handled by Actor, skipping sandbox")
                return True
            
            sandbox = self._get_sandbox(skill_id, runtime_type)
            
            if not sandbox:
                logger.error(f"Failed to create sandbox for skill {skill_id}")
                return False
            
            # 创建沙箱环境
            resources = manifest.get("resources", {}) if isinstance(manifest, dict) else getattr(manifest, "resources", {})
            config = {
                "memory_limit": resources.get("memory", "512m"),
                "cpu_limit": str(resources.get("cpu", 1)),
            }
            
            success = await sandbox.create(skill_id, config)
            if success:
                logger.info(f"Skill {skill_id} loaded successfully")
            else:
                logger.error(f"Failed to load skill {skill_id}")
            
            return success
            
        except Exception as e:
            logger.error(f"Failed to load skill {skill_id}: {e}", exc_info=True)
            return False
    
    async def execute_capability(
        self,
        skill_id: str,
        capability_name: str,
        input_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        执行技能能力
        
        Args:
            skill_id: 技能ID
            capability_name: 能力名称
            input_data: 输入数据
        
        Returns:
            Dict: 执行结果
        """
        try:
            # 获取技能Manifest
            manifest = await self.registry.get_skill(skill_id)
            if not manifest:
                return {
                    "success": False,
                    "error": f"Skill {skill_id} not found",
                }
            
            # 检查能力是否存在
            capability = _get_capability_from_manifest(manifest, capability_name)
            if not capability and not self._is_plugin_manager:
                return {
                    "success": False,
                    "error": f"Capability {capability_name} not found in skill {skill_id}",
                }
            
            # PluginManager + Ray: 直接通过 Actor 执行
            if self._is_plugin_manager:
                actor = self.registry.get_actor(skill_id)
                if not actor:
                    return {"success": False, "error": f"Skill {skill_id} actor not loaded"}
                ref = actor.execute.remote(capability_name, input_data)
                result = ray.get(ref)
                await self._update_usage_stats(skill_id)
                return result

            # 验证输入数据（如果定义了schema）
            input_schema = capability.get("input_schema") if capability else None
            if input_schema:
                # TODO: 实现JSON Schema验证
                pass
            
            # 获取沙箱（非 Ray 运行时）
            runtime_type = manifest.get("runtime", {}).get("type", "ray") if isinstance(manifest, dict) else getattr(manifest, "runtime_type", "ray")
            
            sandbox = self._get_sandbox(skill_id, runtime_type)
            if not sandbox:
                return {
                    "success": False,
                    "error": f"Sandbox not available for skill {skill_id}",
                }
            
            # 构建执行命令
            entrypoint = manifest.runtime.get("entrypoint", "python main.py")
            command = f"{entrypoint} {capability_name}"
            
            # 执行能力
            result = await sandbox.execute(
                skill_id=skill_id,
                command=command,
                input_data=input_data,
                timeout=settings.SKILL_DEFAULT_TIMEOUT,
            )
            
            # 验证输出数据（如果定义了schema）
            output_schema = capability.get("output_schema")
            if output_schema and result.get("success"):
                # TODO: 实现JSON Schema验证
                pass
            
            # 更新使用统计
            await self._update_usage_stats(skill_id)
            
            return result
            
        except Exception as e:
            logger.error(
                f"Failed to execute capability {capability_name} for skill {skill_id}: {e}",
                exc_info=True
            )
            return {
                "success": False,
                "error": str(e),
            }
    
    async def unload_skill(self, skill_id: str) -> bool:
        """
        卸载技能
        
        Args:
            skill_id: 技能ID
        
        Returns:
            bool: 是否成功卸载
        """
        try:
            # 查找并销毁所有相关沙箱
            keys_to_remove = [
                key for key in self.sandboxes.keys()
                if key.startswith(f"{skill_id}:")
            ]
            
            for key in keys_to_remove:
                sandbox = self.sandboxes[key]
                await sandbox.destroy(skill_id)
                del self.sandboxes[key]
            
            logger.info(f"Skill {skill_id} unloaded successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to unload skill {skill_id}: {e}", exc_info=True)
            return False
    
    async def health_check(self, skill_id: str) -> bool:
        """
        健康检查
        
        Args:
            skill_id: 技能ID
        
        Returns:
            bool: 是否健康
        """
        try:
            if self._is_plugin_manager:
                return self.registry.get_actor(skill_id) is not None
            
            manifest = await self.registry.get_skill(skill_id)
            if not manifest:
                return False
            
            runtime_type = manifest.get("runtime", {}).get("type", "ray") if isinstance(manifest, dict) else getattr(manifest, "runtime_type", "ray")
            if runtime_type == "ray":
                return True
            
            sandbox = self._get_sandbox(skill_id, runtime_type)
            
            if not sandbox:
                return False
            
            return await sandbox.health_check(skill_id)
            
        except Exception as e:
            logger.error(f"Failed to check health for skill {skill_id}: {e}")
            return False
    
    async def _update_usage_stats(self, skill_id: str) -> None:
        """更新技能使用统计"""
        try:
            from sqlalchemy.ext.asyncio import AsyncSession
            from sqlalchemy import select, update
            from datetime import datetime
            from core.memory.schema import get_db, Skill
            
            async for db in get_db():
                try:
                    result = await db.execute(
                        select(Skill).where(Skill.skill_id == skill_id)
                    )
                    skill = result.scalar_one_or_none()
                    
                    if skill:
                        skill.usage_count += 1
                        skill.last_used_at = datetime.now()
                        await db.commit()
                finally:
                    break
        except Exception as e:
            logger.warning(f"Failed to update usage stats for skill {skill_id}: {e}")
