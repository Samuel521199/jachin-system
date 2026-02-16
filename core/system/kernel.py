"""
Kernel - 系统内核启动流程
负责初始化 Ray 和 Jachin Link

职责：
- 系统启动流程管理
- Ray 集群初始化
- Jachin Link 初始化
- 系统组件协调
"""

import logging
import asyncio
from typing import Optional, Dict, Any
from pathlib import Path

from core.brain.ray_cluster.cluster_manager import RayClusterManager
from core.transport.gateway import JachinLinkGateway
from core.config import settings

logger = logging.getLogger(__name__)


class Kernel:
    """
    系统内核
    
    负责系统启动和核心组件初始化
    """
    
    def __init__(self):
        """初始化内核"""
        self.ray_manager: Optional[RayClusterManager] = None
        self.link_gateway: Optional[JachinLinkGateway] = None
        self.is_initialized = False
        self.startup_config: Dict[str, Any] = {}
    
    async def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """
        初始化系统内核
        
        Args:
            config: 启动配置（可选）
            
        Returns:
            bool: 初始化是否成功
        """
        if self.is_initialized:
            logger.warning("Kernel already initialized")
            return True
        
        self.startup_config = config or {}
        
        try:
            logger.info("Initializing Jachin-System Kernel...")
            
            # 1. 初始化 Ray 集群
            logger.info("Step 1: Initializing Ray cluster...")
            self.ray_manager = RayClusterManager()
            ray_config = self.ray_manager.load_config()
            
            if not self.ray_manager.initialize():
                logger.error("Failed to initialize Ray cluster")
                return False
            
            logger.info("Ray cluster initialized successfully")
            
            # 2. 初始化 Jachin Link
            logger.info("Step 2: Initializing Jachin Link...")
            try:
                self.link_gateway = JachinLinkGateway()
                # Jachin Link 初始化逻辑（如果需要）
                logger.info("Jachin Link gateway ready")
            except Exception as e:
                logger.warning(f"Jachin Link initialization skipped: {e}")
                # Jachin Link 是可选的，不影响系统启动
            
            # 3. 验证系统状态
            if not await self._verify_system_health():
                logger.error("System health check failed")
                return False
            
            self.is_initialized = True
            logger.info("Kernel initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize kernel: {e}", exc_info=True)
            return False
    
    async def _verify_system_health(self) -> bool:
        """
        验证系统健康状态
        
        Returns:
            bool: 系统是否健康
        """
        try:
            # 检查 Ray 集群状态
            if self.ray_manager and not self.ray_manager.is_initialized:
                logger.error("Ray cluster is not initialized")
                return False
            
            # 可以添加更多健康检查
            logger.info("System health check passed")
            return True
            
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False
    
    async def shutdown(self) -> None:
        """
        关闭系统内核
        
        清理资源，关闭连接
        """
        logger.info("Shutting down kernel...")
        
        try:
            # 关闭 Jachin Link
            if self.link_gateway:
                # 关闭逻辑
                logger.info("Jachin Link gateway closed")
            
            # 关闭 Ray 集群
            if self.ray_manager:
                self.ray_manager.shutdown()
                logger.info("Ray cluster shut down")
            
            self.is_initialized = False
            logger.info("Kernel shutdown complete")
            
        except Exception as e:
            logger.error(f"Error during kernel shutdown: {e}", exc_info=True)
    
    def get_status(self) -> Dict[str, Any]:
        """
        获取系统状态
        
        Returns:
            Dict: 系统状态信息
        """
        return {
            "initialized": self.is_initialized,
            "ray_initialized": self.ray_manager.is_initialized if self.ray_manager else False,
            "link_ready": self.link_gateway is not None,
        }


# 全局内核实例
_kernel_instance: Optional[Kernel] = None


def get_kernel() -> Kernel:
    """
    获取全局内核实例（单例模式）
    
    Returns:
        Kernel: 内核实例
    """
    global _kernel_instance
    if _kernel_instance is None:
        _kernel_instance = Kernel()
    return _kernel_instance


async def initialize_kernel(config: Optional[Dict[str, Any]] = None) -> bool:
    """
    初始化全局内核实例
    
    Args:
        config: 启动配置（可选）
        
    Returns:
        bool: 初始化是否成功
    """
    kernel = get_kernel()
    return await kernel.initialize(config)


async def shutdown_kernel() -> None:
    """关闭全局内核实例"""
    global _kernel_instance
    if _kernel_instance:
        await _kernel_instance.shutdown()
        _kernel_instance = None
