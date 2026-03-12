"""
Performance Monitor - 性能监控模块
用于监控系统性能指标，包括插件执行延迟、错误率等
"""

import time
import logging
import asyncio
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from collections import defaultdict, deque
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


@dataclass
class PerformanceMetric:
    """性能指标"""
    name: str
    value: float
    timestamp: datetime = field(default_factory=datetime.now)
    tags: Dict[str, str] = field(default_factory=dict)


@dataclass
class PerformanceStats:
    """性能统计"""
    count: int = 0
    total_time: float = 0.0
    min_time: float = float('inf')
    max_time: float = 0.0
    errors: int = 0
    
    @property
    def avg_time(self) -> float:
        """平均时间"""
        return self.total_time / self.count if self.count > 0 else 0.0
    
    @property
    def error_rate(self) -> float:
        """错误率"""
        return self.errors / self.count if self.count > 0 else 0.0


class PerformanceMonitor:
    """性能监控器"""
    
    def __init__(self, max_history: int = 1000):
        """
        初始化性能监控器
        
        Args:
            max_history: 最大历史记录数
        """
        self.max_history = max_history
        self.metrics: deque = deque(maxlen=max_history)
        self.stats: Dict[str, PerformanceStats] = defaultdict(PerformanceStats)
        self.recent_errors: deque = deque(maxlen=100)
    
    def record(
        self,
        name: str,
        duration: float,
        success: bool = True,
        tags: Optional[Dict[str, str]] = None
    ):
        """
        记录性能指标
        
        Args:
            name: 指标名称（如 "plugin.execution", "intent.planning"）
            duration: 持续时间（秒）
            success: 是否成功
            tags: 标签（如 {"plugin_id": "com.jachin.sys-monitor"}）
        """
        metric = PerformanceMetric(
            name=name,
            value=duration,
            tags=tags or {}
        )
        self.metrics.append(metric)
        
        # 更新统计
        stats = self.stats[name]
        stats.count += 1
        stats.total_time += duration
        stats.min_time = min(stats.min_time, duration)
        stats.max_time = max(stats.max_time, duration)
        
        if not success:
            stats.errors += 1
            self.recent_errors.append({
                "name": name,
                "duration": duration,
                "tags": tags or {},
                "timestamp": datetime.now()
            })
    
    def get_stats(self, name: Optional[str] = None) -> Dict[str, PerformanceStats]:
        """
        获取统计信息
        
        Args:
            name: 指标名称（如果为 None，返回所有统计）
        
        Returns:
            统计信息字典
        """
        if name:
            return {name: self.stats.get(name, PerformanceStats())}
        return dict(self.stats)
    
    def get_recent_metrics(
        self,
        name: Optional[str] = None,
        minutes: int = 5
    ) -> List[PerformanceMetric]:
        """
        获取最近的指标
        
        Args:
            name: 指标名称（如果为 None，返回所有指标）
            minutes: 最近多少分钟
        
        Returns:
            指标列表
        """
        cutoff = datetime.now() - timedelta(minutes=minutes)
        if name:
            return [
                m for m in self.metrics
                if m.name == name and m.timestamp >= cutoff
            ]
        return [m for m in self.metrics if m.timestamp >= cutoff]
    
    def get_recent_errors(self, minutes: int = 5) -> List[Dict[str, Any]]:
        """
        获取最近的错误
        
        Args:
            minutes: 最近多少分钟
        
        Returns:
            错误列表
        """
        cutoff = datetime.now() - timedelta(minutes=minutes)
        return [
            e for e in self.recent_errors
            if e["timestamp"] >= cutoff
        ]
    
    def check_alerts(self) -> List[Dict[str, Any]]:
        """
        检查告警
        
        Returns:
            告警列表
        """
        alerts = []
        
        for name, stats in self.stats.items():
            # 错误率告警
            if stats.error_rate > 0.1:  # 错误率 > 10%
                alerts.append({
                    "type": "high_error_rate",
                    "name": name,
                    "error_rate": stats.error_rate,
                    "message": f"{name} has high error rate: {stats.error_rate:.2%}"
                })
            
            # 延迟告警
            if stats.avg_time > 5.0:  # 平均延迟 > 5 秒
                alerts.append({
                    "type": "high_latency",
                    "name": name,
                    "avg_time": stats.avg_time,
                    "message": f"{name} has high latency: {stats.avg_time:.2f}s"
                })
        
        return alerts
    
    def reset(self):
        """重置所有统计"""
        self.metrics.clear()
        self.stats.clear()
        self.recent_errors.clear()


# 全局性能监控器实例
_performance_monitor: Optional[PerformanceMonitor] = None


def get_performance_monitor() -> PerformanceMonitor:
    """获取性能监控器实例（单例）"""
    global _performance_monitor
    if _performance_monitor is None:
        _performance_monitor = PerformanceMonitor()
    return _performance_monitor


# 上下文管理器：用于自动记录性能指标
class PerformanceContext:
    """性能上下文管理器"""
    
    def __init__(
        self,
        name: str,
        monitor: Optional[PerformanceMonitor] = None,
        tags: Optional[Dict[str, str]] = None
    ):
        self.name = name
        self.monitor = monitor or get_performance_monitor()
        self.tags = tags or {}
        self.start_time: Optional[float] = None
        self.success = True
    
    def __enter__(self):
        self.start_time = time.time()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = time.time() - self.start_time
        success = exc_type is None
        self.monitor.record(self.name, duration, success, self.tags)
        return False  # 不抑制异常


# 装饰器：用于自动记录函数执行时间
def monitor_performance(name: Optional[str] = None, tags: Optional[Dict[str, str]] = None):
    """
    性能监控装饰器
    
    Args:
        name: 指标名称（如果为 None，使用函数名）
        tags: 标签
    
    Example:
        @monitor_performance("plugin.execution", {"plugin_id": "com.jachin.sys-monitor"})
        async def invoke_plugin(...):
            ...
    """
    def decorator(func):
        metric_name = name or f"{func.__module__}.{func.__name__}"
        
        async def async_wrapper(*args, **kwargs):
            with PerformanceContext(metric_name, tags=tags):
                return await func(*args, **kwargs)
        
        def sync_wrapper(*args, **kwargs):
            with PerformanceContext(metric_name, tags=tags):
                return func(*args, **kwargs)
        
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator
