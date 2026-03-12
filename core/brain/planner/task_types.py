"""
V2 任务类型（原 ray_cluster.task_types 已删除）

轻量占位，供 task_planner、resource_allocator 兼容导入。
V2 资源分配由 L2 调度逻辑接管，此处仅保留类型定义。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class TaskType(Enum):
    SKILL = "skill"
    LLM = "llm"
    DEVICE = "device"


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class TaskResource:
    num_cpus: int = 0
    num_gpus: int = 0
    memory_mb: int = 0


@dataclass
class RayTask:
    """任务对象（V2 占位，原 Ray 任务类型）"""
    task_id: str
    task_type: TaskType
    status: TaskStatus = TaskStatus.PENDING
    skill_id: Optional[str] = None
    capability_name: Optional[str] = None
    worker_node: Optional[str] = None
    priority: int = 0
    resources: Optional[TaskResource] = None
    result: Any = None
    error_message: Optional[str] = None


def create_skill_task(skill_id: str, capability: str, params: dict) -> RayTask:
    import uuid
    return RayTask(
        task_id=str(uuid.uuid4()),
        task_type=TaskType.SKILL,
        skill_id=skill_id,
        capability_name=capability,
    )


def create_llm_task(prompt: str, **kwargs) -> RayTask:
    import uuid
    return RayTask(
        task_id=str(uuid.uuid4()),
        task_type=TaskType.LLM,
    )
