"""
com.jachin.sys-monitor - 系统性能监控
继承 BaseSkill，实现 get_performance_snapshot
"""

from typing import Dict, Any
from core.skills.base_skill import BaseSkill


class SysMonitorSkill(BaseSkill):
    """系统仪表盘：获取 CPU、内存、磁盘等性能快照"""

    async def execute(self, capability: str, params: Dict[str, Any]) -> Dict[str, Any]:
        if capability == "get_performance_snapshot":
            return await self._get_performance_snapshot(params)
        return {"success": False, "error": f"Unknown capability: {capability}"}

    async def _get_performance_snapshot(self, params: Dict[str, Any]) -> Dict[str, Any]:
        try:
            import psutil
            from datetime import datetime
            cpu = {
                "percent": psutil.cpu_percent(interval=0.1),
                "count": psutil.cpu_count(),
                "freq": psutil.cpu_freq()._asdict() if psutil.cpu_freq() else None,
            }
            mem = psutil.virtual_memory()._asdict()
            disk = psutil.disk_usage("/")._asdict() if hasattr(psutil, "disk_usage") else {}
            try:
                temps = psutil.sensors_temperatures()
                temperature = {k: [t._asdict() for t in v] for k, v in temps.items()} if temps else {}
            except AttributeError:
                temperature = {}
            return {
                "success": True,
                "result": {
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                    "cpu": cpu,
                    "memory": mem,
                    "disk": disk,
                    "temperature": temperature,
                },
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
