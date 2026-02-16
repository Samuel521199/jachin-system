"""
Resource Schemas - 硬件资源标签

v4.0 蜂群智能：用于 Swarm Scheduler 任务分配
"""

from enum import Enum
from typing import Optional, List
from pydantic import BaseModel, Field


class ResourceTag(str, Enum):
    """资源类型标签"""
    GPU = "gpu"
    NPU = "npu"
    CPU = "cpu"
    CAMERA = "camera"
    MIC = "mic"
    GPS = "gps"


class NodeResourceProfile(BaseModel):
    """节点资源画像"""
    node_id: str
    has_gpu: bool = False
    gpu_count: int = 0
    has_npu: bool = False
    has_camera: bool = False
    has_mic: bool = False
    cpu_cores: int = 1
    memory_mb: int = 512
    tags: List[str] = Field(default_factory=list)  # "iot", "edge", "head"


class TaskResourceRequest(BaseModel):
    """任务资源需求"""
    compute: str = Field(default="cpu_light")  # cpu_light | cpu_medium | gpu_heavy
    num_cpus: float = 1.0
    num_gpus: float = 0.0
    memory_mb: int = 256
