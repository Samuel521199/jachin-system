"""
GPU 状态工具 - 供推理分流策略使用
当 GPU 温度过高时，可触发云端回退或模型降级
"""

import time
import logging

logger = logging.getLogger(__name__)

GPU_OVERHEAT_THRESHOLD = 85
_cache: dict = {"max_temp": None, "ts": 0}
_CACHE_TTL = 10  # 秒


def update_cache(gpus: list) -> None:
    """更新 GPU 温度缓存（由 get_gpu_stats 调用）"""
    global _cache
    max_temp = None
    for g in gpus:
        t = g.get("temperature_c")
        if t is not None and (max_temp is None or t > max_temp):
            max_temp = t
    _cache["max_temp"] = max_temp
    _cache["ts"] = time.time()


def is_gpu_overheated() -> bool:
    """
    判断 GPU 是否过热（>= 85°C）
    用于推理分流：过热时优先使用云端模型
    """
    global _cache
    if time.time() - _cache["ts"] < _CACHE_TTL and _cache["max_temp"] is not None:
        return _cache["max_temp"] >= GPU_OVERHEAT_THRESHOLD
    # 缓存过期或无数据时，尝试采集
    try:
        from core.api.console import _collect_gpu_stats
        gpus = _collect_gpu_stats()
        update_cache(gpus)
        return _cache["max_temp"] is not None and _cache["max_temp"] >= GPU_OVERHEAT_THRESHOLD
    except Exception as e:
        logger.debug("gpu_status.is_gpu_overheated collect failed: %s", e)
        return False
