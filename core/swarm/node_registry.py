"""
Node Registry - 节点注册与发现

v4.0 蜂群：谁在线？有什么硬件？供 Swarm Scheduler 分配任务
"""

import logging
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class NodeInfo:
    """节点信息"""
    node_id: str
    host: str
    ray_port: int
    node_type: str  # primary | worker | edge
    has_gpu: bool = False
    gpu_count: int = 0
    has_npu: bool = False
    trust_zone: str = "home"
    last_seen: Optional[datetime] = None
    tags: List[str] = field(default_factory=list)

    @property
    def ip(self) -> str:
        return self.host

    @property
    def capabilities(self) -> List[str]:
        caps = list(self.tags)
        if self.has_gpu:
            caps.append("gpu")
        if self.has_npu:
            caps.append("npu")
        return caps

    @property
    def status(self) -> str:
        if self.last_seen:
            delta = (datetime.utcnow() - self.last_seen).total_seconds()
            return "online" if delta < 120 else "stale"
        return "unknown"


@dataclass
class Node:
    """节点 (Action Plan 兼容)：node_id, ip, capabilities, status"""
    node_id: str
    ip: str
    capabilities: List[str]
    status: str = "online"


class NodeRegistry:
    """
    节点注册表 - 维护集群中所有节点的状态与能力
    与 Ray Cluster 协同，记录节点硬件标签供 Scheduler 做 placement 决策。
    """

    def __init__(self):
        self._nodes: Dict[str, NodeInfo] = {}
        self._primary_node_id: Optional[str] = None

    def register(
        self,
        node_id: str,
        host: str,
        ray_port: int = 10001,
        node_type: str = "worker",
        has_gpu: bool = False,
        gpu_count: int = 0,
        has_npu: bool = False,
        trust_zone: str = "home",
        tags: Optional[List[str]] = None,
    ) -> bool:
        """注册节点"""
        self._nodes[node_id] = NodeInfo(
            node_id=node_id,
            host=host,
            ray_port=ray_port,
            node_type=node_type,
            has_gpu=has_gpu,
            gpu_count=gpu_count,
            has_npu=has_npu,
            trust_zone=trust_zone,
            last_seen=datetime.utcnow(),
            tags=tags or [],
        )
        if node_type == "primary":
            self._primary_node_id = node_id
        logger.info(f"Registered node: {node_id} ({node_type})")
        return True

    def heartbeat(self, node_id: str) -> bool:
        """更新节点心跳"""
        if node_id in self._nodes:
            self._nodes[node_id].last_seen = datetime.utcnow()
            return True
        return False

    def get_node(self, node_id: str) -> Optional[NodeInfo]:
        """获取节点信息"""
        return self._nodes.get(node_id)

    def list_nodes(
        self,
        node_type: Optional[str] = None,
        has_gpu: Optional[bool] = None,
        trust_zone: Optional[str] = None,
    ) -> List[NodeInfo]:
        """列出节点，支持过滤"""
        nodes = list(self._nodes.values())
        if node_type:
            nodes = [n for n in nodes if n.node_type == node_type]
        if has_gpu is not None:
            nodes = [n for n in nodes if n.has_gpu == has_gpu]
        if trust_zone:
            nodes = [n for n in nodes if n.trust_zone == trust_zone]
        return nodes

    def find_gpu_node(self) -> Optional[NodeInfo]:
        """查找有 GPU 的节点（供 gpu_heavy 任务）"""
        for n in self._nodes.values():
            if n.has_gpu and n.gpu_count > 0:
                return n
        return None

    def register_node(
        self,
        node_id: str,
        ip: str,
        capabilities: Optional[List[str]] = None,
        status: str = "online",
        **kwargs,
    ) -> bool:
        """
        注册节点 (Action Plan 兼容接口)
        capabilities: 如 ['gpu', 'camera']
        """
        node_type = kwargs.get("node_type", "worker")
        has_gpu = "gpu" in (capabilities or [])
        gpu_count = kwargs.get("gpu_count", 1 if has_gpu else 0)
        return self.register(
            node_id=node_id,
            host=ip,
            ray_port=kwargs.get("ray_port", 10001),
            node_type=node_type,
            has_gpu=has_gpu,
            gpu_count=gpu_count,
            has_npu=kwargs.get("has_npu", "npu" in (capabilities or [])),
            trust_zone=kwargs.get("trust_zone", "home"),
            tags=capabilities or [],
        )

    def get_capable_nodes(self, capability: str) -> List[NodeInfo]:
        """获取具备指定能力的节点列表"""
        result = []
        for n in self._nodes.values():
            if capability in n.tags:
                result.append(n)
            elif capability == "gpu" and n.has_gpu:
                result.append(n)
            elif capability == "camera" and "camera" in n.tags:
                result.append(n)
        return result

    def get_nodes_as_node(self, capability: Optional[str] = None) -> List[Node]:
        """返回 Node 列表（Action Plan 兼容）"""
        if capability:
            infos = self.get_capable_nodes(capability)
        else:
            infos = list(self._nodes.values())
        return [
            Node(
                node_id=n.node_id,
                ip=n.host,
                capabilities=n.capabilities,
                status=n.status,
            )
            for n in infos
        ]

    def list_all_node_ids(self) -> List[str]:
        """列出所有节点 ID"""
        return list(self._nodes.keys())

    def unregister(self, node_id: str) -> bool:
        """注销节点"""
        if node_id in self._nodes:
            del self._nodes[node_id]
            if self._primary_node_id == node_id:
                self._primary_node_id = None
            return True
        return False
