"""
技能注册表 - 已废弃，统一使用 PluginManager (core.system.plugin_manager)
DEPRECATED: Use PluginManager for skill management.
此模块为兼容性存根，返回空数据。重构时请将调用方迁移至 PluginManager。
"""

import logging
import warnings
from typing import Dict, Any, Optional, List

warnings.warn(
    "SkillRegistry is deprecated. Use PluginManager from core.system.plugin_manager.",
    DeprecationWarning,
    stacklevel=2,
)

logger = logging.getLogger(__name__)


class SkillRegistry:
    """DEPRECATED: 兼容性存根。请使用 PluginManager。"""

    def __init__(self):
        self._cache: Dict[str, Any] = {}

    async def load_all_skills(self) -> int:
        """占位：返回 0"""
        logger.warning("SkillRegistry.load_all_skills is deprecated. Use PluginManager.")
        return 0

    async def load_skill(self, skill_id: str) -> bool:
        """占位：返回 False"""
        return False

    async def reload_skills(self) -> Dict[str, Any]:
        """占位"""
        return {"total_discovered": 0, "newly_loaded": 0, "updated": 0, "errors": 0, "error_details": []}

    async def get_skill(self, skill_id: str) -> Optional[Any]:
        """占位：返回 None"""
        return None

    async def list_skills(self) -> List[Dict[str, Any]]:
        """占位：返回空列表"""
        return []

    async def list_capabilities(self, capability_filter: str) -> List[Dict[str, Any]]:
        """占位：返回空列表"""
        return []

    async def update_skill_status(self, skill_id: str, status: str) -> bool:
        """占位：返回 False"""
        return False
