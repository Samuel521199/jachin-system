"""
Plugin Security Layer - 隔离区与代码沙箱
战役五：保卫 Layer 2 绝对安全
P0-3：混合动力沙箱（WASM + heavy_process）

- validator: 静态 AST 安全扫描
- sandbox: 受限执行作用域加载（轻量 Python AST）
- sandbox_engine: 沙箱装载引擎（分流 wasm / heavy_process）
- heavy_process: 重型独立进程 + UDS 通信
- plugin_server_base: heavy_process 插件服务端基类
"""

from core.plugin.validator import (
    extract_and_validate,
    scan_python_code,
    SecurityViolationError,
)
from core.plugin.sandbox import PluginSandbox
from core.plugin.sandbox_engine import SandboxEngine, get_plugin_runners
from core.plugin.heavy_process import HeavyProcessRunner, HeavyProcessClient, PluginState
from core.plugin.plugin_server_base import PluginServerBase

__all__ = [
    "extract_and_validate",
    "scan_python_code",
    "SecurityViolationError",
    "PluginSandbox",
    "SandboxEngine",
    "get_plugin_runners",
    "HeavyProcessRunner",
    "HeavyProcessClient",
    "PluginState",
    "PluginServerBase",
]
