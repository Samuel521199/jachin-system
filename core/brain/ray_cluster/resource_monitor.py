"""
Ray 资源监控器
Ray Resource Monitor
"""

import ray
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from core.brain.ray_cluster.cluster_manager import RayClusterManager

logger = logging.getLogger(__name__)


class ResourceMonitor:
    """资源监控器"""
    
    def __init__(self, cluster_manager: RayClusterManager):
        """
        初始化资源监控器
        
        Args:
            cluster_manager: Ray集群管理器
        """
        self.cluster_manager = cluster_manager
        self.monitoring_interval = 30  # 监控间隔（秒）
        self._monitoring_task: Optional[Any] = None
        
    def get_cluster_resources(self) -> Dict[str, Any]:
        """
        获取集群资源信息
        
        Returns:
            Dict: 资源信息
        """
        if not self.cluster_manager.is_connected():
            return {"error": "Ray cluster not connected"}
        
        try:
            # 获取可用资源
            available = ray.available_resources()
            
            # 获取集群资源
            cluster_resources = ray.cluster_resources()
            
            # 获取节点信息
            nodes = ray.nodes()
            
            return {
                "available": dict(available),
                "cluster": dict(cluster_resources),
                "nodes": [
                    {
                        "node_id": node.get("NodeID"),
                        "alive": node.get("Alive"),
                        "resources": node.get("Resources", {}),
                        "alive_since": node.get("AliveSince"),
                    }
                    for node in nodes
                ],
                "timestamp": datetime.now().isoformat(),
            }
        except Exception as e:
            logger.error(f"Failed to get cluster resources: {e}", exc_info=True)
            return {"error": str(e)}
    
    def get_node_resources(self, node_id: Optional[str] = None) -> Dict[str, Any]:
        """
        获取节点资源信息
        
        Args:
            node_id: 节点ID，如果为None则返回所有节点
        
        Returns:
            Dict: 节点资源信息
        """
        if not self.cluster_manager.is_connected():
            return {"error": "Ray cluster not connected"}
        
        try:
            nodes = ray.nodes()
            
            if node_id:
                # 返回指定节点
                for node in nodes:
                    if node.get("NodeID") == node_id:
                        return {
                            "node_id": node.get("NodeID"),
                            "alive": node.get("Alive"),
                            "resources": node.get("Resources", {}),
                            "alive_since": node.get("AliveSince"),
                        }
                return {"error": f"Node {node_id} not found"}
            else:
                # 返回所有节点
                return {
                    "nodes": [
                        {
                            "node_id": node.get("NodeID"),
                            "alive": node.get("Alive"),
                            "resources": node.get("Resources", {}),
                            "alive_since": node.get("AliveSince"),
                        }
                        for node in nodes
                    ],
                    "timestamp": datetime.now().isoformat(),
                }
        except Exception as e:
            logger.error(f"Failed to get node resources: {e}", exc_info=True)
            return {"error": str(e)}
    
    def check_resource_availability(
        self,
        num_cpus: int = 0,
        num_gpus: int = 0,
        memory_mb: int = 0
    ) -> bool:
        """
        检查资源是否可用
        
        Args:
            num_cpus: 需要的CPU数量
            num_gpus: 需要的GPU数量
            memory_mb: 需要的内存MB
        
        Returns:
            bool: 资源是否可用
        """
        if not self.cluster_manager.is_connected():
            return False
        
        try:
            available = ray.available_resources()
            
            # 检查CPU
            if num_cpus > 0:
                available_cpus = available.get("CPU", 0)
                if available_cpus < num_cpus:
                    return False
            
            # 检查GPU
            if num_gpus > 0:
                available_gpus = available.get("GPU", 0)
                if available_gpus < num_gpus:
                    return False
            
            # 检查内存
            if memory_mb > 0:
                available_memory = available.get("memory", 0)
                if available_memory < memory_mb * 1024 * 1024:  # 转换为字节
                    return False
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to check resource availability: {e}", exc_info=True)
            return False
    
    def get_resource_utilization(self) -> Dict[str, float]:
        """
        获取资源利用率
        
        Returns:
            Dict: 资源利用率（0.0-1.0）
        """
        if not self.cluster_manager.is_connected():
            return {}
        
        try:
            available = ray.available_resources()
            cluster = ray.cluster_resources()
            
            utilization = {}
            
            # CPU利用率
            if "CPU" in cluster:
                used_cpu = cluster["CPU"] - available.get("CPU", 0)
                utilization["cpu"] = used_cpu / cluster["CPU"] if cluster["CPU"] > 0 else 0.0
            
            # GPU利用率
            if "GPU" in cluster:
                used_gpu = cluster["GPU"] - available.get("GPU", 0)
                utilization["gpu"] = used_gpu / cluster["GPU"] if cluster["GPU"] > 0 else 0.0
            
            # 内存利用率
            if "memory" in cluster:
                used_memory = cluster["memory"] - available.get("memory", 0)
                utilization["memory"] = used_memory / cluster["memory"] if cluster["memory"] > 0 else 0.0
            
            return utilization
            
        except Exception as e:
            logger.error(f"Failed to get resource utilization: {e}", exc_info=True)
            return {}
