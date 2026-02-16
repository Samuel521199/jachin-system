"""
Ray 集群管理器
Ray Cluster Manager
"""

import ray
import yaml
import logging
from typing import Optional, Dict, Any
from pathlib import Path
from core.config import settings

logger = logging.getLogger(__name__)


class RayClusterManager:
    """Ray集群管理器"""
    
    def __init__(self, config_path: Optional[str] = None):
        """
        初始化Ray集群管理器
        
        Args:
            config_path: Ray配置文件路径，默认使用settings中的路径
        """
        self.config_path = config_path or settings.RAY_CONFIG_PATH
        self.config: Optional[Dict[str, Any]] = None
        self.is_initialized = False
        self.ray_address: Optional[str] = None
        
    def load_config(self) -> Dict[str, Any]:
        """加载Ray配置"""
        if self.config is not None:
            return self.config
            
        config_file = Path(self.config_path)
        if not config_file.exists():
            logger.warning(f"Ray config file not found: {self.config_path}, using defaults")
            return self._get_default_config()
        
        with open(config_file, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)
        
        return self.config
    
    def _get_default_config(self) -> Dict[str, Any]:
        """获取默认配置"""
        return {
            "mode": settings.RAY_MODE,
            "head": {
                "host": settings.RAY_HEAD_HOST,
                "port": settings.RAY_HEAD_PORT,
                "dashboard_port": settings.RAY_DASHBOARD_PORT,
            },
        }
    
    async def initialize(self) -> bool:
        """
        初始化Ray集群连接
        
        Returns:
            bool: 是否成功初始化
        """
        if self.is_initialized:
            logger.info("Ray cluster already initialized")
            return True
        
        try:
            config = self.load_config()
            mode = config.get("mode", "single")
            
            if mode == "single":
                # Single模式：启动本地Ray
                if not ray.is_initialized():
                    logger.info("Initializing Ray in single mode...")
                    import os
                    import tempfile
                    # 使用系统临时目录，Windows 兼容
                    temp_dir = os.path.join(tempfile.gettempdir(), "ray")
                    os.makedirs(temp_dir, exist_ok=True)
                    ray.init(
                        ignore_reinit_error=True,
                        _temp_dir=temp_dir,
                        configure_logging=True,
                        num_cpus=os.cpu_count() or 2,
                    )
                    logger.info("Ray initialized successfully in single mode")
                else:
                    logger.info("Ray already initialized")
            elif mode == "cluster":
                # Cluster模式：连接到Ray Head节点
                head_config = config.get("head", {})
                head_host = head_config.get("host", settings.RAY_HEAD_HOST)
                head_port = head_config.get("port", settings.RAY_HEAD_PORT)
                self.ray_address = f"ray://{head_host}:{head_port}"
                
                logger.info(f"Connecting to Ray cluster at {self.ray_address}...")
                ray.init(
                    address=self.ray_address,
                    ignore_reinit_error=True,
                )
                logger.info("Connected to Ray cluster successfully")
            else:
                raise ValueError(f"Unknown Ray mode: {mode}")
            
            self.is_initialized = True
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize Ray cluster: {e}", exc_info=True)
            self.is_initialized = False
            return False
    
    async def shutdown(self) -> bool:
        """
        关闭Ray集群连接
        
        Returns:
            bool: 是否成功关闭
        """
        if not self.is_initialized:
            return True
        
        try:
            if ray.is_initialized():
                ray.shutdown()
                logger.info("Ray cluster shutdown successfully")
            self.is_initialized = False
            return True
        except Exception as e:
            logger.error(f"Failed to shutdown Ray cluster: {e}", exc_info=True)
            return False
    
    def get_cluster_info(self) -> Dict[str, Any]:
        """
        获取集群信息
        
        Returns:
            Dict: 集群信息
        """
        if not self.is_initialized or not ray.is_initialized():
            return {"status": "not_initialized"}
        
        try:
            # 获取集群资源
            resources = ray.available_resources()
            nodes = ray.nodes()
            
            node_list = []
            for node in nodes:
                node_id = node.get("NodeID") or node.get("node_id") or ""
                host = ""
                port = 0
                # Ray 节点可能包含 NodeManagerAddress (ip:port) 或 NodeManagerHostName
                addr = node.get("NodeManagerAddress") or node.get("NodeManagerHostName") or ""
                if addr and ":" in str(addr):
                    parts = str(addr).rsplit(":", 1)
                    host = parts[0] if parts else ""
                    try:
                        port = int(parts[1]) if len(parts) > 1 else 0
                    except (ValueError, IndexError):
                        port = 0
                elif addr:
                    host = str(addr)
                node_list.append({
                    "node_id": node_id,
                    "alive": node.get("Alive", node.get("alive", False)),
                    "resources": node.get("Resources", node.get("resources", {})),
                    "host": host or node.get("host", ""),
                    "port": port or node.get("port", 0),
                })
            return {
                "status": "initialized",
                "ray_address": self.ray_address or "local",
                "resources": dict(resources),
                "nodes": node_list,
            }
        except Exception as e:
            logger.error(f"Failed to get cluster info: {e}", exc_info=True)
            return {"status": "error", "error": str(e)}
    
    def is_connected(self) -> bool:
        """检查是否已连接到Ray集群"""
        return self.is_initialized and ray.is_initialized()
