"""
System Management Module
系统管理模块

包含：
- Kernel: 系统内核启动流程
- Telemetry: 硬件探针 (HAL)
- Permission: 权限检查接口
- PluginManager: 插件管理器
- PluginExecutor: 插件执行器
- PermissionEnforcer: 权限执行器
"""

from core.system.kernel import Kernel, get_kernel, initialize_kernel, shutdown_kernel
from core.system.telemetry import Telemetry, get_telemetry
from core.system.permission import PermissionChecker, PermissionScope as PermissionScopeEnum, get_permission_checker
from core.system.plugin_manager import PluginManager
from core.system.plugin_executor import PluginExecutor
from core.system.permission_enforcer import PermissionEnforcer, PermissionScope, get_permission_enforcer
from core.system.runtime_permission_interceptor import (
    RuntimePermissionInterceptor,
    install_interceptor_for_plugin,
)

# PermissionError 是 Python 内置异常，不需要导入
# 使用内置的 PermissionError: from builtins import PermissionError

__all__ = [
    # 内核模块
    "Kernel",
    "get_kernel",
    "initialize_kernel",
    "shutdown_kernel",
    # 硬件探针
    "Telemetry",
    "get_telemetry",
    # 权限检查
    "PermissionChecker",
    "PermissionScopeEnum",
    "get_permission_checker",
    # 插件管理
    "PluginManager",
    "PluginExecutor",
    "PermissionEnforcer",
    "PermissionScope",
    "get_permission_enforcer",
    "RuntimePermissionInterceptor",
    "install_interceptor_for_plugin",
]
