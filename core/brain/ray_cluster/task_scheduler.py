"""
Ray 任务调度器
Ray Task Scheduler
"""

import asyncio
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime
from core.brain.ray_cluster.task_types import RayTask, TaskStatus, TaskType, TaskResource
from core.brain.ray_cluster.cluster_manager import RayClusterManager
from core.brain.ray_cluster.tasks import llm_inference_task, skill_execution_task

logger = logging.getLogger(__name__)


class TaskScheduler:
    """任务调度器"""
    
    def __init__(self, cluster_manager: RayClusterManager):
        """
        初始化任务调度器
        
        Args:
            cluster_manager: Ray集群管理器
        """
        self.cluster_manager = cluster_manager
        self.pending_tasks: Dict[str, RayTask] = {}
        self.running_tasks: Dict[str, RayTask] = {}
        self.completed_tasks: Dict[str, RayTask] = {}
        self.failed_tasks: Dict[str, RayTask] = {}
        
    async def submit_task(self, task: RayTask) -> str:
        """
        提交任务到Ray集群
        
        Args:
            task: Ray任务对象
        
        Returns:
            str: 任务ID
        """
        if not self.cluster_manager.is_connected():
            raise RuntimeError("Ray cluster is not connected")
        
        try:
            # 根据任务类型选择对应的Ray远程函数
            if task.task_type == TaskType.LLM_INFERENCE:
                ray_task_ref = llm_inference_task.remote(
                    provider=task.provider,
                    model=task.model,
                    messages=task.input_data.get("messages", []),
                    temperature=task.input_data.get("temperature", 0.7),
                    max_tokens=task.input_data.get("max_tokens", 2000),
                )
            elif task.task_type == TaskType.SKILL_EXECUTION:
                # 根据 compute 需求选择 GPU 节点（gpu_heavy -> num_gpus=1）
                res = task.resources or TaskResource()
                opts = {}
                if res.num_gpus > 0:
                    opts["num_gpus"] = res.num_gpus
                if res.num_cpus != 1:
                    opts["num_cpus"] = max(0.1, res.num_cpus)
                remote_fn = skill_execution_task.options(**opts) if opts else skill_execution_task
                ray_task_ref = remote_fn.remote(
                    skill_id=task.skill_id,
                    capability_name=task.capability_name,
                    input_data=task.input_data or {},
                )
            else:
                raise ValueError(f"Unsupported task type: {task.task_type}")
            
            # 更新任务状态
            task.status = TaskStatus.RUNNING
            task.ray_task_ref = ray_task_ref
            task.started_at = datetime.now()
            
            # 移动到运行中队列
            self.pending_tasks.pop(task.task_id, None)
            self.running_tasks[task.task_id] = task
            
            logger.info(f"Task {task.task_id} submitted successfully")
            return task.task_id
            
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error_message = str(e)
            self.failed_tasks[task.task_id] = task
            logger.error(f"Failed to submit task {task.task_id}: {e}", exc_info=True)
            raise
    
    async def wait_for_task(self, task_id: str, timeout: Optional[float] = None) -> RayTask:
        """
        等待任务完成
        
        Args:
            task_id: 任务ID
            timeout: 超时时间（秒）
        
        Returns:
            RayTask: 完成的任务对象
        """
        if task_id not in self.running_tasks:
            raise ValueError(f"Task {task_id} not found in running tasks")
        
        task = self.running_tasks[task_id]
        
        try:
            # 等待Ray任务完成
            if timeout:
                result = await asyncio.wait_for(
                    task.ray_task_ref,
                    timeout=timeout
                )
            else:
                result = await task.ray_task_ref
            
            # 更新任务状态
            task.status = TaskStatus.COMPLETED
            task.result = result
            task.completed_at = datetime.now()
            if task.started_at:
                duration = (task.completed_at - task.started_at).total_seconds() * 1000
                task.duration_ms = int(duration)
            
            # 移动到已完成队列
            self.running_tasks.pop(task_id)
            self.completed_tasks[task_id] = task
            
            logger.info(f"Task {task_id} completed successfully")
            return task
            
        except asyncio.TimeoutError:
            task.status = TaskStatus.FAILED
            task.error_message = f"Task timeout after {timeout} seconds"
            self.running_tasks.pop(task_id)
            self.failed_tasks[task_id] = task
            logger.error(f"Task {task_id} timed out")
            return task
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error_message = str(e)
            self.running_tasks.pop(task_id)
            self.failed_tasks[task_id] = task
            logger.error(f"Task {task_id} failed: {e}", exc_info=True)
            return task
    
    async def cancel_task(self, task_id: str) -> bool:
        """
        取消任务
        
        Args:
            task_id: 任务ID
        
        Returns:
            bool: 是否成功取消
        """
        if task_id not in self.running_tasks:
            return False
        
        task = self.running_tasks[task_id]
        
        try:
            # 取消Ray任务
            import ray
            ray.cancel(task.ray_task_ref)
            
            task.status = TaskStatus.CANCELLED
            self.running_tasks.pop(task_id)
            logger.info(f"Task {task_id} cancelled")
            return True
            
        except Exception as e:
            logger.error(f"Failed to cancel task {task_id}: {e}", exc_info=True)
            return False
    
    def get_task_status(self, task_id: str) -> Optional[TaskStatus]:
        """
        获取任务状态
        
        Args:
            task_id: 任务ID
        
        Returns:
            TaskStatus: 任务状态，如果任务不存在则返回None
        """
        # 检查所有队列
        for task_dict in [self.pending_tasks, self.running_tasks, self.completed_tasks, self.failed_tasks]:
            if task_id in task_dict:
                return task_dict[task_id].status
        return None
    
    def get_task(self, task_id: str) -> Optional[RayTask]:
        """
        获取任务对象
        
        Args:
            task_id: 任务ID
        
        Returns:
            RayTask: 任务对象，如果不存在则返回None
        """
        # 检查所有队列
        for task_dict in [self.pending_tasks, self.running_tasks, self.completed_tasks, self.failed_tasks]:
            if task_id in task_dict:
                return task_dict[task_id]
        return None
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        获取调度器统计信息
        
        Returns:
            Dict: 统计信息
        """
        return {
            "pending": len(self.pending_tasks),
            "running": len(self.running_tasks),
            "completed": len(self.completed_tasks),
            "failed": len(self.failed_tasks),
            "total": len(self.pending_tasks) + len(self.running_tasks) + len(self.completed_tasks) + len(self.failed_tasks),
        }
