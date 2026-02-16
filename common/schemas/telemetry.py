"""
Telemetry Schema - 匿名监控数据格式
用于系统性能监控和错误追踪

注意：此文件仅包含数据模型定义（Pydantic Schemas）
严禁包含业务逻辑代码
"""

from typing import Optional, Dict, Any
from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime
from enum import Enum


class TelemetryLevel(str, Enum):
    """监控数据级别"""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class TelemetryEvent(BaseModel):
    """监控事件数据模型"""
    
    # 事件标识
    event_id: str = Field(description="事件唯一标识")
    event_type: str = Field(description="事件类型，如 'plugin_install', 'api_call'")
    level: TelemetryLevel = Field(default=TelemetryLevel.INFO)
    
    # 时间戳
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    
    # 上下文信息（匿名化）
    tier: str = Field(description="层级：'tier1', 'tier2', 'tier3'")
    component: str = Field(description="组件名称，如 'market', 'brain', 'gateway'")
    
    # 数据（不包含敏感信息）
    data: Dict[str, Any] = Field(default_factory=dict, description="事件数据（已匿名化）")
    
    # 元数据
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据")
    
    model_config = ConfigDict(use_enum_values=True)
