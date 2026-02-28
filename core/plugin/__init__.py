"""
Plugin Security Layer - 隔离区与代码沙箱
战役五：保卫 Layer 2 绝对安全

- validator: 静态 AST 安全扫描
- sandbox: 受限执行作用域加载
"""

from core.plugin.validator import (
    extract_and_validate,
    scan_python_code,
    SecurityViolationError,
)
from core.plugin.sandbox import PluginSandbox

__all__ = [
    "extract_and_validate",
    "scan_python_code",
    "SecurityViolationError",
    "PluginSandbox",
]
