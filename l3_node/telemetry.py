"""
Jachin Nexus V2 - L3 硬件遥测

采集 cpu_load、memory_free、has_gpu 等，供 L2 调度器负载感知。
"""
from __future__ import annotations

import logging
import subprocess
from typing import Any

logger = logging.getLogger(__name__)

try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False


def collect_hardware_telemetry() -> dict[str, Any]:
    """
    采集当前节点硬件负载与标签。
    返回: { cpu_load, memory_free, has_gpu }
    - cpu_load: 0-100，CPU 使用率 %
    - memory_free: MB，可用内存
    - has_gpu: bool，是否具备 GPU（尝试 nvidia-smi 检测）
    """
    out: dict[str, Any] = {
        "cpu_load": 0.0,
        "memory_free": 0.0,
        "has_gpu": False,
    }
    if _HAS_PSUTIL:
        try:
            out["cpu_load"] = round(psutil.cpu_percent(interval=None) or 0, 2)
            vm = psutil.virtual_memory()
            out["memory_free"] = round((vm.available or 0) / (1024 * 1024), 2)
        except Exception as e:
            logger.debug("[Telemetry] psutil 采集失败: %s", e)
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            timeout=2,
            text=True,
        )
        out["has_gpu"] = r.returncode == 0 and bool(r.stdout and r.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        pass
    return out
