"""
Ray 任务类型定义
Ray Task Type Definitions
"""

from enum import Enum
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from datetime import datetime
import uuid


class TaskType(str, Enum):
    """任务类型枚举"""
    LLM_INFERENCE = "llm_inference"  # LLM推理任务
    SKILL_EXECUTION = "skill_execution"  # 技能执行任务
    VIDEO_PROCESSING = "video_processing"  # 视频处理任务
    AUDIO_PROCESSING = "audio_processing"  # 音频处理任务
    IMAGE_PROCESSING = "image_processing"  # 图像处理任务
    DATA_ANALYSIS = "data_analysis"  # 数据分析任务
    CUSTOM = "custom"  # 自定义任务


class TaskStatus(str, Enum):
    """任务状态枚举"""
    PENDING = "pending"  # 等待中
    RUNNING = "running"  # 运行中
    COMPLETED = "completed"  # 已完成
    FAILED = "failed"  # 失败
    CANCELLED = "cancelled"  # 已取消


@dataclass
class TaskResource:
    """任务资源需求"""
    num_cpus: int = 1
    num_gpus: int = 0
    memory_mb: int = 512
    custom_resources: Optional[Dict[str, float]] = None


@dataclass
class RayTask:
    """Ray任务定义"""
    task_id: str
    task_type: TaskType
    status: TaskStatus = TaskStatus.PENDING
    priority: int = 0
    
    # 任务参数
    provider: Optional[str] = None  # LLM provider
    model: Optional[str] = None  # Model name
    skill_id: Optional[str] = None  # Skill ID
    capability_name: Optional[str] = None  # Capability name
    input_data: Optional[Dict[str, Any]] = None
    
    # 资源需求
    resources: Optional[TaskResource] = None
    
    # 执行信息
    worker_node: Optional[str] = None  # Ray worker node ID
    ray_task_ref: Optional[Any] = None  # Ray ObjectRef
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    
    # 结果和错误
    result: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    
    def __post_init__(self):
        """初始化后处理"""
        if self.task_id is None:
            self.task_id = str(uuid.uuid4())
        if self.created_at is None:
            self.created_at = datetime.now()
        if self.resources is None:
            self.resources = TaskResource()


@dataclass
class TaskResult:
    """任务执行结果"""
    task_id: str
    status: TaskStatus
    result: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    duration_ms: Optional[int] = None
    worker_node: Optional[str] = None


def create_llm_task(
    provider: str,
    model: str,
    messages: List[Dict[str, Any]],
    temperature: float = 0.7,
    max_tokens: int = 2000,
    num_cpus: int = 1,
    num_gpus: int = 0,
    priority: int = 0
) -> RayTask:
    """创建LLM推理任务"""
    return RayTask(
        task_id=str(uuid.uuid4()),
        task_type=TaskType.LLM_INFERENCE,
        provider=provider,
        model=model,
        input_data={
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        resources=TaskResource(num_cpus=num_cpus, num_gpus=num_gpus),
        priority=priority,
    )


def create_skill_task(
    skill_id: str,
    capability_name: str,
    input_data: Dict[str, Any],
    num_cpus: int = 1,
    num_gpus: int = 0,
    priority: int = 0
) -> RayTask:
    """创建技能执行任务"""
    return RayTask(
        task_id=str(uuid.uuid4()),
        task_type=TaskType.SKILL_EXECUTION,
        skill_id=skill_id,
        capability_name=capability_name,
        input_data=input_data,
        resources=TaskResource(num_cpus=num_cpus, num_gpus=num_gpus),
        priority=priority,
    )
