"""
Ray 装饰器封装
Ray Decorator Wrappers
"""

import ray
import functools
import logging
from typing import Callable, Any, Optional, Dict

logger = logging.getLogger(__name__)


def ray_task(
    num_cpus: int = 1,
    num_gpus: int = 0,
    memory_mb: int = 512,
    max_retries: int = 0,
    **kwargs
):
    """
    Ray任务装饰器
    
    Args:
        num_cpus: CPU数量
        num_gpus: GPU数量
        memory_mb: 内存MB
        max_retries: 最大重试次数
        **kwargs: 其他Ray remote参数
    
    Returns:
        装饰器函数
    """
    def decorator(func: Callable) -> Callable:
        # 创建Ray远程函数
        remote_func = ray.remote(
            num_cpus=num_cpus,
            num_gpus=num_gpus,
            memory=memory_mb * 1024 * 1024,  # 转换为字节
            max_retries=max_retries,
            **kwargs
        )(func)
        
        # 包装原始函数，添加日志和错误处理
        @functools.wraps(func)
        async def wrapper(*args, **kw):
            try:
                logger.debug(f"Executing Ray task: {func.__name__}")
                result = await remote_func.remote(*args, **kw)
                return result
            except Exception as e:
                logger.error(f"Ray task {func.__name__} failed: {e}", exc_info=True)
                raise
        
        # 保留原始函数和远程函数的引用
        wrapper._remote_func = remote_func
        wrapper._original_func = func
        
        return wrapper
    
    return decorator


def ray_actor(
    num_cpus: int = 1,
    num_gpus: int = 0,
    memory_mb: int = 512,
    **kwargs
):
    """
    Ray Actor装饰器
    
    Args:
        num_cpus: CPU数量
        num_gpus: GPU数量
        memory_mb: 内存MB
        **kwargs: 其他Ray actor参数
    
    Returns:
        装饰器函数
    """
    def decorator(cls: type) -> type:
        # 创建Ray Actor类
        ray_actor_class = ray.remote(
            num_cpus=num_cpus,
            num_gpus=num_gpus,
            memory=memory_mb * 1024 * 1024,  # 转换为字节
            **kwargs
        )(cls)
        
        return ray_actor_class
    
    return decorator
