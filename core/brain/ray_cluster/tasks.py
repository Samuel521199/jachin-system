"""
Ray 远程任务函数
Ray Remote Task Functions

注意：Ray remote tasks 不支持 async def，需要使用同步函数包装异步代码
"""

import ray
import asyncio
import logging
from typing import Dict, Any, List
from core.brain.ray_cluster.task_types import TaskType

logger = logging.getLogger(__name__)


@ray.remote(num_gpus=0, num_cpus=1)
def llm_inference_task(
    provider: str,
    model: str,
    messages: List[Dict[str, Any]],
    temperature: float = 0.7,
    max_tokens: int = 2000
) -> Dict[str, Any]:
    """
    LLM推理任务（Ray远程函数）
    
    注意：Ray remote tasks 必须是同步函数，内部使用 asyncio.run() 运行异步代码
    
    Args:
        provider: LLM提供者 (qwen, qwen-v2, local)
        model: 模型名称
        messages: 消息列表
        temperature: 温度参数
        max_tokens: 最大token数
    
    Returns:
        Dict: 包含text, usage, model的结果字典
    """
    async def _async_llm_inference():
        try:
            from core.brain.llm.factory import LLMProviderFactory
            
            factory = LLMProviderFactory()
            llm_provider = factory.create_provider(provider)
            
            result = await llm_provider.chat(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens
            )
            
            return {
                "text": result.text,
                "usage": result.usage,
                "model": result.model,
                "success": True,
            }
        except Exception as e:
            logger.error(f"LLM inference task failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
            }
    
    # 使用 asyncio.run() 运行异步函数
    try:
        return asyncio.run(_async_llm_inference())
    except Exception as e:
        logger.error(f"Failed to run LLM inference task: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
        }


@ray.remote(num_gpus=0, num_cpus=1)
def skill_execution_task(
    skill_id: str,
    capability_name: str,
    input_data: Dict[str, Any]
) -> Dict[str, Any]:
    """
    技能执行任务（Ray远程函数）
    
    注意：Ray remote tasks 必须是同步函数，内部使用 asyncio.run() 运行异步代码
    
    Args:
        skill_id: 技能ID
        capability_name: 能力名称
        input_data: 输入数据
    
    Returns:
        Dict: 执行结果
    """
    async def _async_skill_execution():
        try:
            from core.runtime.skill_runner import SkillRunner
            from core.system.plugin_manager import get_plugin_manager
            
            # 使用 PluginManager 单例
            registry = get_plugin_manager()
            runner = SkillRunner(registry)
            
            # 执行能力
            result = await runner.execute_capability(
                skill_id=skill_id,
                capability_name=capability_name,
                input_data=input_data
            )
            
            return result
        except Exception as e:
            logger.error(f"Skill execution task failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
            }
    
    # 使用 asyncio.run() 运行异步函数
    try:
        return asyncio.run(_async_skill_execution())
    except Exception as e:
        logger.error(f"Failed to run skill execution task: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
        }


# 动态创建Ray远程函数的装饰器
def create_ray_task(
    task_type: TaskType,
    num_cpus: int = 1,
    num_gpus: int = 0,
    memory_mb: int = 512
):
    """
    动态创建Ray远程任务装饰器
    
    Args:
        task_type: 任务类型
        num_cpus: CPU数量
        num_gpus: GPU数量
        memory_mb: 内存MB
    
    Returns:
        装饰器函数
    """
    def decorator(func):
        return ray.remote(
            num_cpus=num_cpus,
            num_gpus=num_gpus,
            memory=memory_mb * 1024 * 1024  # 转换为字节
        )(func)
    return decorator
