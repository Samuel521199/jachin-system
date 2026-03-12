"""
Telemetry - 硬件探针 (HAL)
实现硬件信息读取：CPU、内存、温度等

职责：
- 读取系统硬件信息
- 监控系统资源使用情况
- 提供性能指标
"""

import logging
import platform
import psutil
from typing import Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class Telemetry:
    """
    硬件探针 (Hardware Abstraction Layer)
    
    提供系统硬件和性能监控功能
    """
    
    def __init__(self):
        """初始化探针"""
        self._cache: Dict[str, Any] = {}
        self._cache_ttl = 5.0  # 缓存时间（秒）
        self._last_update: Optional[datetime] = None
    
    def get_cpu_info(self) -> Dict[str, Any]:
        """
        获取 CPU 信息
        
        Returns:
            Dict: CPU 信息
        """
        try:
            cpu_count_physical = psutil.cpu_count(logical=False)
            cpu_count_logical = psutil.cpu_count(logical=True)
            cpu_freq = psutil.cpu_freq()
            
            return {
                "physical_cores": cpu_count_physical or 0,
                "logical_cores": cpu_count_logical or 0,
                "frequency_mhz": cpu_freq.current if cpu_freq else None,
                "frequency_max_mhz": cpu_freq.max if cpu_freq else None,
                "architecture": platform.machine(),
                "processor": platform.processor(),
            }
        except Exception as e:
            logger.error(f"Failed to get CPU info: {e}")
            return {}
    
    def get_memory_info(self) -> Dict[str, Any]:
        """
        获取内存信息
        
        Returns:
            Dict: 内存信息
        """
        try:
            mem = psutil.virtual_memory()
            swap = psutil.swap_memory()
            
            return {
                "total_bytes": mem.total,
                "available_bytes": mem.available,
                "used_bytes": mem.used,
                "percent": mem.percent,
                "swap_total_bytes": swap.total,
                "swap_used_bytes": swap.used,
                "swap_percent": swap.percent,
            }
        except Exception as e:
            logger.error(f"Failed to get memory info: {e}")
            return {}
    
    def get_disk_info(self) -> Dict[str, Any]:
        """
        获取磁盘信息
        
        Returns:
            Dict: 磁盘信息
        """
        try:
            disk = psutil.disk_usage('/')
            disk_io = psutil.disk_io_counters()
            
            result = {
                "total_bytes": disk.total,
                "used_bytes": disk.used,
                "free_bytes": disk.free,
                "percent": disk.percent,
            }
            
            if disk_io:
                result.update({
                    "read_bytes": disk_io.read_bytes,
                    "write_bytes": disk_io.write_bytes,
                    "read_count": disk_io.read_count,
                    "write_count": disk_io.write_count,
                })
            
            return result
        except Exception as e:
            logger.error(f"Failed to get disk info: {e}")
            return {}
    
    def get_temperature_info(self) -> Dict[str, Any]:
        """
        获取温度信息（如果可用）
        
        Returns:
            Dict: 温度信息
        """
        try:
            temps = psutil.sensors_temperatures()
            if not temps:
                return {}
            
            result = {}
            for name, entries in temps.items():
                if entries:
                    # 取第一个传感器的值
                    result[name] = {
                        "current": entries[0].current,
                        "high": entries[0].high,
                        "critical": entries[0].critical if hasattr(entries[0], 'critical') else None,
                    }
            
            return result
        except Exception as e:
            logger.debug(f"Temperature info not available: {e}")
            return {}
    
    def get_performance_snapshot(self) -> Dict[str, Any]:
        """
        获取性能快照
        
        Returns:
            Dict: 完整的性能快照
        """
        try:
            cpu_percent = psutil.cpu_percent(interval=0.1)
            cpu_per_core = psutil.cpu_percent(interval=0.1, percpu=True)
            
            return {
                "timestamp": datetime.now().isoformat(),
                "cpu": {
                    "percent": cpu_percent,
                    "per_core": cpu_per_core,
                    **self.get_cpu_info(),
                },
                "memory": self.get_memory_info(),
                "disk": self.get_disk_info(),
                "temperature": self.get_temperature_info(),
                "platform": {
                    "system": platform.system(),
                    "release": platform.release(),
                    "version": platform.version(),
                    "machine": platform.machine(),
                },
            }
        except Exception as e:
            logger.error(f"Failed to get performance snapshot: {e}")
            return {}
    
    def get_system_info(self) -> Dict[str, Any]:
        """
        获取系统基本信息
        
        Returns:
            Dict: 系统信息
        """
        return {
            "platform": platform.platform(),
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "python_version": platform.python_version(),
        }


# 全局探针实例
_telemetry_instance: Optional[Telemetry] = None


def get_telemetry() -> Telemetry:
    """
    获取全局探针实例（单例模式）
    
    Returns:
        Telemetry: 探针实例
    """
    global _telemetry_instance
    if _telemetry_instance is None:
        _telemetry_instance = Telemetry()
    return _telemetry_instance
