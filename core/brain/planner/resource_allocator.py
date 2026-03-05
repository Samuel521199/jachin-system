"""
资源分配器 (V2)

V2: Ray Cluster 已废弃，资源分配由 L2 调度逻辑接管。
此处为兼容占位，allocate_resources 恒返回 None。
"""
from __future__ import annotations

import logging
from typing import Optional

from core.brain.planner.task_types import RayTask

logger = logging.getLogger(__name__)


class ResourceAllocator:
    """资源分配器（V2 占位：Ray 已废弃，由 L2 调度）"""

    def __init__(self, cluster_manager: Optional[object] = None):
        self.cluster_manager = cluster_manager
        self.monitor = None

    async def allocate_resources(self, task: RayTask) -> Optional[str]:
        """V2: 恒返回 None，资源分配由 L2 协同调度接管。"""
        return None
