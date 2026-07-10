"""
Swarm Scheduler - 分布式任务调度 (V2)

V2: Ray Cluster 已废弃。根据 compute 标签选择节点，供 L2 协同调度使用。
"""

import logging
from typing import Optional, Dict, Any
from core.swarm.node_registry import NodeRegistry, NodeInfo

logger = logging.getLogger(__name__)


class SwarmScheduler:
    """
    蜂群调度器 - 根据任务 compute 需求选择目标节点

    当 capability.compute == gpu_heavy 时，优先将任务发给带 GPU 的 Worker。
    否则发给 Primary 或任意可用 Worker。
    """

    def __init__(self, node_registry: Optional[NodeRegistry] = None):
        self.registry = node_registry or NodeRegistry()

    def select_node(
        self,
        compute_tag: str = "cpu_light",
        trust_zone: Optional[str] = None,
    ) -> Optional[NodeInfo]:
        """
        为任务选择目标节点

        Args:
            compute_tag: cpu_light | cpu_medium | gpu_heavy
            trust_zone: 信任域过滤（可选）

        Returns:
            选中的节点，None 表示使用默认（Primary）
        """
        if compute_tag == "gpu_heavy":
            node = self.registry.find_gpu_node()
            if node:
                return node
            logger.warning("No GPU node available, fallback to default")

        # cpu_light / cpu_medium: 任意可用节点，当前返回 None 表示用默认
        return None

    def get_placement_hint(
        self,
        skill_id: str,
        capability: str,
        compute_tag: str = "cpu_light",
    ) -> Dict[str, Any]:
        """
        获取任务放置提示（供 Ray TaskScheduler 使用）

        Returns:
            {"num_gpus": 0|1, "node_id": "..."} 等
        """
        node = self.select_node(compute_tag=compute_tag)
        if compute_tag == "gpu_heavy":
            return {"num_gpus": 1, "num_cpus": 2, "node_id": node.node_id if node else None}
        if compute_tag == "cpu_medium":
            return {"num_gpus": 0, "num_cpus": 2}
        return {"num_gpus": 0, "num_cpus": 1}
