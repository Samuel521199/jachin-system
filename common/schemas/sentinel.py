"""
Sentinel 哨兵任务数据模型
"""

from enum import Enum
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field


class SentinelPriority(str, Enum):
    """任务优先级"""
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class SentinelTask(BaseModel):
    """哨兵监控任务"""

    task_id: str
    priority: SentinelPriority = SentinelPriority.NORMAL
    escalation_level: int = 0  # 当前升级到了第几级 (0=未开始)
    required_ack: bool = True  # 是否必须用户确认收到
    context: Dict[str, Any] = Field(default_factory=dict)  # 任务详情，如"约会时间"

    # 内部状态
    last_notified_at: Optional[str] = None  # ISO 格式，上次通知时间
    acked_at: Optional[str] = None  # 用户确认时间
    status: str = "pending"  # pending | notified | acked | escalated | failed

    class Config:
        use_enum_values = True
