"""
Swarm Health Monitor - 集群健康监控

v4.0 蜂群：心跳检测、节点存活状态、故障转移
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from core.swarm.node_registry import NodeRegistry, NodeInfo

logger = logging.getLogger(__name__)

DEFAULT_HEARTBEAT_TIMEOUT_SEC = 60


class SwarmHealthMonitor:
    """
    蜂群健康监控 - 检测节点存活，标记离线节点
    """

    def __init__(
        self,
        node_registry: NodeRegistry,
        timeout_sec: int = DEFAULT_HEARTBEAT_TIMEOUT_SEC,
    ):
        self.registry = node_registry
        self.timeout_sec = timeout_sec

    def check_node_health(self, node_id: str) -> bool:
        """检查节点是否存活（基于 last_seen）"""
        node = self.registry.get_node(node_id)
        if not node or not node.last_seen:
            return False
        delta = datetime.utcnow() - node.last_seen
        return delta.total_seconds() < self.timeout_sec

    def get_dead_nodes(self) -> List[str]:
        """获取超时未心跳的节点列表"""
        dead = []
        for node_id in self.registry.list_all_node_ids():
            if not self.check_node_health(node_id):
                dead.append(node_id)
        return dead

    def prune_dead_nodes(self) -> int:
        """清理离线节点，返回清理数量"""
        dead = self.get_dead_nodes()
        for node_id in dead:
            self.registry.unregister(node_id)
            logger.warning(f"Pruned dead node: {node_id}")
        return len(dead)

    def get_cluster_status(self) -> Dict:
        """获取集群健康摘要"""
        total = len(self.registry.list_all_node_ids())
        dead = self.get_dead_nodes()
        alive = total - len(dead)
        return {
            "total_nodes": total,
            "alive_nodes": alive,
            "dead_nodes": len(dead),
            "dead_node_ids": dead,
        }
