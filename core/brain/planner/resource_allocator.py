"""
资源分配器
Resource Allocator
"""

import logging
from typing import Optional, Dict, Any
from core.brain.ray_cluster.task_types import RayTask, TaskResource
from core.brain.ray_cluster.resource_monitor import ResourceMonitor
from core.brain.ray_cluster.cluster_manager import RayClusterManager

logger = logging.getLogger(__name__)


class ResourceAllocator:
    """资源分配器"""
    
    def __init__(self, cluster_manager: Optional[RayClusterManager] = None):
        """
        初始化资源分配器
        
        Args:
            cluster_manager: Ray集群管理器，如果为None则创建新实例
        """
        self.cluster_manager = cluster_manager
        if cluster_manager:
            self.monitor = ResourceMonitor(cluster_manager)
        else:
            self.monitor = None
    
    async def allocate_resources(self, task: RayTask) -> Optional[str]:
        """
        为任务分配资源
        
        Args:
            task: Ray任务对象
        
        Returns:
            str: 分配的节点ID，如果分配失败则返回None
        """
        if not self.monitor or not self.cluster_manager:
            logger.warning("Resource monitor not available, using default allocation")
            return None
        
        if not self.cluster_manager.is_connected():
            logger.warning("Ray cluster not connected, cannot allocate resources")
            return None
        
        try:
            # 获取任务资源需求
            resources = task.resources or TaskResource()
            
            # 检查资源可用性
            if not self.monitor.check_resource_availability(
                num_cpus=resources.num_cpus,
                num_gpus=resources.num_gpus,
                memory_mb=resources.memory_mb
            ):
                logger.warning(
                    f"Insufficient resources for task {task.task_id}: "
                    f"CPU={resources.num_cpus}, GPU={resources.num_gpus}, Memory={resources.memory_mb}MB"
                )
                return None
            
            # 获取集群信息
            cluster_info = self.cluster_manager.get_cluster_info()
            nodes = cluster_info.get("nodes", [])
            
            if not nodes:
                logger.warning("No nodes available in cluster")
                return None
            
            # 选择最优节点
            selected_node = self._select_best_node(nodes, resources)
            
            if selected_node:
                logger.info(f"Allocated resources for task {task.task_id} on node {selected_node}")
                return selected_node
            else:
                logger.warning(f"Failed to select node for task {task.task_id}")
                return None
                
        except Exception as e:
            logger.error(f"Failed to allocate resources for task {task.task_id}: {e}", exc_info=True)
            return None
    
    def _select_best_node(
        self,
        nodes: list,
        resources: TaskResource
    ) -> Optional[str]:
        """
        选择最优节点
        
        Args:
            nodes: 节点列表
            resources: 资源需求
        
        Returns:
            str: 节点ID，如果找不到则返回None
        """
        # 过滤可用节点
        available_nodes = [
            node for node in nodes
            if node.get("alive", False)
        ]
        
        if not available_nodes:
            return None
        
        # 如果有GPU需求，优先选择有GPU的节点
        if resources.num_gpus > 0:
            gpu_nodes = [
                node for node in available_nodes
                if node.get("resources", {}).get("GPU", 0) >= resources.num_gpus
            ]
            if gpu_nodes:
                available_nodes = gpu_nodes
        
        # 选择资源最充足的节点（简单策略：选择CPU最多的节点）
        best_node = None
        max_cpu = 0
        
        for node in available_nodes:
            node_resources = node.get("resources", {})
            cpu = node_resources.get("CPU", 0)
            
            # 检查是否满足资源需求
            if cpu >= resources.num_cpus:
                if cpu > max_cpu:
                    max_cpu = cpu
                    best_node = node
        
        if best_node:
            return best_node.get("node_id")
        
        return None
    
    async def check_resource_availability(
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
        if not self.monitor:
            return False
        
        return self.monitor.check_resource_availability(
            num_cpus=num_cpus,
            num_gpus=num_gpus,
            memory_mb=memory_mb
        )
