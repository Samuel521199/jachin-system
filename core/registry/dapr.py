"""
Dapr Integration for Device Registry
设备注册表的 Dapr 集成模块

这个模块提供 StateStore 和 PubSub 的导入，用于设备注册表。
"""

# 从 core.dapr 导入 StateStore 和 PubSub
from core.dapr import StateStore, PubSub

# 导出这些类，使其可以通过 from core.registry.dapr import StateStore, PubSub 使用
__all__ = ["StateStore", "PubSub"]
