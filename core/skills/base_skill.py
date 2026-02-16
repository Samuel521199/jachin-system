"""
Base Skill - 技能基类 (v4.0)

Ray 不支持继承 @ray.remote 的 Actor 类，因此：
- BaseSkill: 普通类，技能实现继承此类
- BaseSkillActor: @ray.remote 类，仅用于 SkillActorWrapper，不供技能继承

职责：
- 定义技能标准接口
- execute() 通过 getattr 将 capability 分发到同名方法
- 子类实现 capability 方法（如 list_files、get_performance_snapshot）
- v4.0: zone_restricted 信任域拦截
"""

import asyncio
import logging
import ray
from typing import Dict, Any, List, Optional

from core.exceptions import AccessDenied

logger = logging.getLogger(__name__)


class BaseSkill:
    """
    技能基类（非 Actor）

    所有技能实现继承此类。Ray 不支持继承 actor 类，故技能必须继承普通类。
    capability 方法签名：async def capability_name(self, params: Dict[str, Any]) -> Dict[str, Any]
    v4.0: manifest 可声明 zone_restricted，执行前检查 SecurityContext
    """

    def __init__(self, manifest: Dict[str, Any]):
        self.manifest = manifest
        self.skill_id = manifest.get("id") or manifest.get("skill_id", "unknown")
        logger.info(f"Initialized skill: {self.skill_id}")

    def _check_zone_restricted(self, context: Optional[Dict[str, Any]]) -> None:
        zone_restricted = self.manifest.get("zone_restricted")
        if not zone_restricted:
            return
        if not context:
            raise AccessDenied("此技能需要信任域上下文")
        current_zone = (context.get("current_zone") or "").upper()
        allowed = str(zone_restricted).upper()
        if current_zone != allowed:
            raise AccessDenied(f"此技能只能在 {zone_restricted} 网络中使用，当前: {current_zone}")

    async def execute(
        self,
        capability: str,
        params: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        self._check_zone_restricted(context)
        method = getattr(self, capability, None)
        if not method or not callable(method):
            raise NotImplementedError(f"Capability {capability} not found in {self.skill_id}")
        if asyncio.iscoroutinefunction(method):
            return await method(params)
        return method(params)

    async def get_manifest(self) -> Dict[str, Any]:
        return self.manifest


@ray.remote
class BaseSkillActor(BaseSkill):
    """
    技能 Actor 基类（仅用于需要 Ray Actor 的包装场景）

    继承 BaseSkill，添加 @ray.remote。注意：Ray 不支持继承 actor 类，
    故 SkillActorWrapper 不能继承此类，技能实现应继承 BaseSkill。
    """

    async def shutdown(self) -> None:
        ray.actor.exit_actor()
