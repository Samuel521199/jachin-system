"""
Model Abstraction Layer (MAL) - 模型抽象层

所有 LLM 调用必须通过此层，严禁在业务逻辑中直接调用特定模型 API。
"""

from .base import BaseLLMProvider
from .qwen_adapter import QwenAdapter
from .local_adapter import LocalAdapter
from .router import ModelRouter, TaskComplexity
from .factory import LLMProviderFactory
from .personality import PersonalityManager, Personality, get_personality_manager

__all__ = [
    "BaseLLMProvider",
    "QwenAdapter",
    "LocalAdapter",
    "ModelRouter",
    "TaskComplexity",
    "LLMProviderFactory",
    "PersonalityManager",
    "Personality",
    "get_personality_manager",
]
