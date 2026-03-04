"""
Kernel - 系统内核启动流程

v5.0: Ray 集群已废弃，内核改为极简占位实现。
Layer 2 核心链路由 daemon + agent_loop + wasm_runner 接管。
"""

import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class Kernel:
    """v5.0 极简内核（Ray/Jachin Link 已废弃）"""

    def __init__(self):
        self.ray_manager = None
        self.link_gateway = None
        self.is_initialized = False
        self.startup_config: Dict[str, Any] = {}

    async def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """v5.0: 占位初始化，始终成功"""
        self.startup_config = config or {}
        self.is_initialized = True
        logger.info("Kernel v5.0 (Ray/Jachin Link 已废弃)")
        return True

    async def shutdown(self) -> None:
        """占位关闭"""
        self.is_initialized = False
        logger.info("Kernel shutdown (v5.0 stub)")

    def get_status(self) -> Dict[str, Any]:
        return {
            "initialized": self.is_initialized,
            "ray_initialized": False,
            "link_ready": False,
        }


_kernel_instance: Optional[Kernel] = None


def get_kernel() -> Kernel:
    global _kernel_instance
    if _kernel_instance is None:
        _kernel_instance = Kernel()
    return _kernel_instance


async def initialize_kernel(config: Optional[Dict[str, Any]] = None) -> bool:
    return await get_kernel().initialize(config)


async def shutdown_kernel() -> None:
    global _kernel_instance
    if _kernel_instance:
        await _kernel_instance.shutdown()
        _kernel_instance = None
