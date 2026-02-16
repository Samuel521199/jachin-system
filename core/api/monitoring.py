"""
Monitoring API - 性能监控 API 端点
提供性能指标查询接口
"""

from fastapi import APIRouter, HTTPException
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from core.monitoring import get_performance_monitor

router = APIRouter(prefix="/api/v3/monitoring", tags=["monitoring"])


@router.get("/stats")
async def get_stats(metric_name: Optional[str] = None) -> Dict[str, Any]:
    """
    获取性能统计信息
    
    Args:
        metric_name: 指标名称（如果为 None，返回所有统计）
    
    Returns:
        统计信息字典
    """
    monitor = get_performance_monitor()
    stats = monitor.get_stats(metric_name)
    
    # 转换为可序列化的格式
    result = {}
    for name, stat in stats.items():
        result[name] = {
            "count": stat.count,
            "avg_time": stat.avg_time,
            "min_time": stat.min_time if stat.min_time != float('inf') else 0.0,
            "max_time": stat.max_time,
            "total_time": stat.total_time,
            "errors": stat.errors,
            "error_rate": stat.error_rate,
        }
    
    return result


@router.get("/metrics")
async def get_recent_metrics(
    metric_name: Optional[str] = None,
    minutes: int = 5
) -> Dict[str, Any]:
    """
    获取最近的性能指标
    
    Args:
        metric_name: 指标名称（如果为 None，返回所有指标）
        minutes: 最近多少分钟
    
    Returns:
        指标列表
    """
    monitor = get_performance_monitor()
    metrics = monitor.get_recent_metrics(metric_name, minutes)
    
    # 转换为可序列化的格式
    result = []
    for metric in metrics:
        result.append({
            "name": metric.name,
            "value": metric.value,
            "timestamp": metric.timestamp.isoformat(),
            "tags": metric.tags,
        })
    
    return {"metrics": result, "count": len(result)}


@router.get("/errors")
async def get_recent_errors(minutes: int = 5) -> Dict[str, Any]:
    """
    获取最近的错误
    
    Args:
        minutes: 最近多少分钟
    
    Returns:
        错误列表
    """
    monitor = get_performance_monitor()
    errors = monitor.get_recent_errors(minutes)
    
    # 转换为可序列化的格式
    result = []
    for error in errors:
        result.append({
            "name": error["name"],
            "duration": error["duration"],
            "tags": error["tags"],
            "timestamp": error["timestamp"].isoformat(),
        })
    
    return {"errors": result, "count": len(result)}


@router.get("/alerts")
async def get_alerts() -> Dict[str, Any]:
    """
    获取当前告警
    
    Returns:
        告警列表
    """
    monitor = get_performance_monitor()
    alerts = monitor.check_alerts()
    
    return {"alerts": alerts, "count": len(alerts)}


@router.post("/reset")
async def reset_stats():
    """
    重置所有性能统计（谨慎使用）
    """
    monitor = get_performance_monitor()
    monitor.reset()
    return {"message": "Performance stats reset successfully"}
