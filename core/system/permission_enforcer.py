"""
Permission Enforcer - 权限执行器
负责在运行时检查和强制执行插件权限

职责：
- 解析插件权限声明（manifest.permissions）
- 在执行前检查权限
- 运行时权限拦截（通过 Ray RuntimeEnv 或代理）
- 权限违规日志记录
"""

import logging
from typing import Dict, List, Set, Optional
from enum import Enum

from common.schemas.manifest import PluginManifest, Permission

logger = logging.getLogger(__name__)


class PermissionScope(str, Enum):
    """权限作用域枚举"""
    # 网络权限
    INTERNET_ACCESS = "internet.access"
    INTERNET_HTTPS_ONLY = "internet.https_only"

    # 文件系统权限
    FILE_READ = "file.read"
    FILE_WRITE = "file.write"
    FILE_DELETE = "file.delete"

    # 数据库权限
    DATABASE_QUERY = "database.query"
    DATABASE_WRITE = "database.write"

    # LLM/AI 权限
    LLM_CALL = "llm.call"
    LLM_FINE_TUNING = "llm.fine_tuning"

    # 系统权限
    SYSTEM_INFO = "system.info"
    SYSTEM_ENV_VARS = "system.env_vars"

    # 设备权限
    DEVICE_CONTROL = "device.control"
    DEVICE_SENSOR = "device.sensor"


class PermissionEnforcer:
    """
    权限执行器

    负责：
    - 解析权限声明
    - 检查权限是否允许
    - 记录权限违规
    """

    def __init__(self):
        """初始化权限执行器"""
        # 存储每个插件的权限集合
        self.plugin_permissions: Dict[str, Set[str]] = {}

    def register_plugin_permissions(self, plugin_id: str, manifest: PluginManifest):
        """
        注册插件的权限声明

        Args:
            plugin_id: 插件 ID
            manifest: 插件清单
        """
        permissions = set()
        for perm in manifest.permissions:
            if isinstance(perm, Permission):
                permissions.add(perm.scope)
            elif isinstance(perm, dict):
                permissions.add(perm.get("scope", ""))
            elif isinstance(perm, str):
                permissions.add(perm)

        self.plugin_permissions[plugin_id] = permissions
        logger.info(f"Registered permissions for plugin '{plugin_id}': {permissions}")

    def check_permission(self, plugin_id: str, scope: str) -> bool:
        """
        检查插件是否有指定权限

        Args:
            plugin_id: 插件 ID
            scope: 权限作用域

        Returns:
            是否有权限
        """
        if plugin_id not in self.plugin_permissions:
            logger.warning(f"Plugin '{plugin_id}' permissions not registered")
            return False

        permissions = self.plugin_permissions[plugin_id]

        # 检查精确匹配
        if scope in permissions:
            return True

        # 检查通配符权限（例如 "internet.*"）
        scope_parts = scope.split(".")
        for perm in permissions:
            perm_parts = perm.split(".")
            if len(scope_parts) == len(perm_parts):
                match = True
                for i, part in enumerate(perm_parts):
                    if part != "*" and part != scope_parts[i]:
                        match = False
                        break
                if match:
                    return True

        logger.warning(f"Plugin '{plugin_id}' does not have permission '{scope}'")
        return False

    def require_permission(self, plugin_id: str, scope: str) -> None:
        """
        要求插件必须有指定权限，否则抛出异常

        Args:
            plugin_id: 插件 ID
            scope: 权限作用域

        Raises:
            PermissionError: 如果没有权限
        """
        if not self.check_permission(plugin_id, scope):
            raise PermissionError(
                f"Plugin '{plugin_id}' does not have required permission '{scope}'"
            )

    def get_plugin_permissions(self, plugin_id: str) -> Set[str]:
        """
        获取插件的所有权限

        Args:
            plugin_id: 插件 ID

        Returns:
            权限集合
        """
        return self.plugin_permissions.get(plugin_id, set())

    def unregister_plugin(self, plugin_id: str):
        """
        注销插件的权限

        Args:
            plugin_id: 插件 ID
        """
        if plugin_id in self.plugin_permissions:
            del self.plugin_permissions[plugin_id]
            logger.info(f"Unregistered permissions for plugin '{plugin_id}'")


# 全局权限执行器实例
_global_enforcer: Optional[PermissionEnforcer] = None


def get_permission_enforcer() -> PermissionEnforcer:
    """获取全局权限执行器实例"""
    global _global_enforcer
    if _global_enforcer is None:
        _global_enforcer = PermissionEnforcer()
    return _global_enforcer
