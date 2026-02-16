"""
ModelRouter - 模型路由器

根据任务类型和复杂度自动选择最合适的模型 Provider。
"""

import os
import re
from enum import Enum
from typing import Optional, Dict, Any
import logging

from .base import BaseLLMProvider
from .qwen_adapter import QwenAdapter
from .local_adapter import LocalAdapter

logger = logging.getLogger(__name__)


class TaskComplexity(Enum):
    """任务复杂度枚举"""
    SIMPLE = "simple"      # 简单指令，如 IoT 控制
    MEDIUM = "medium"      # 中等复杂度对话
    COMPLEX = "complex"    # 复杂推理、代码生成、计划制定


class ModelRouter:
    """模型路由器 - 根据任务类型选择最合适的模型"""
    
    def __init__(
        self,
        qwen_adapter: Optional[QwenAdapter] = None,
        local_adapter: Optional[LocalAdapter] = None,
        prefer_local_for_simple: bool = True,
        prefer_local_for_medium: bool = False,
    ):
        """
        初始化 Model Router
        
        Args:
            qwen_adapter: Qwen Adapter 实例（可选）
            local_adapter: Local Adapter 实例（可选）
            prefer_local_for_simple: 简单任务是否优先使用本地模型
            prefer_local_for_medium: 中等任务是否优先使用本地模型
        """
        self.qwen_adapter = qwen_adapter
        self.local_adapter = local_adapter
        self.prefer_local_for_simple = prefer_local_for_simple
        self.prefer_local_for_medium = prefer_local_for_medium
        
        # 简单指令关键词（中文）
        self.simple_keywords = [
            "开灯", "关灯", "打开", "关闭", "查询", "获取",
            "开启", "关闭", "启动", "停止", "读取", "显示",
            "turn on", "turn off", "open", "close", "get", "read", "show"
        ]
        
        # 复杂任务关键词（中文）
        self.complex_keywords = [
            "编写", "生成", "计划", "分析", "设计", "优化",
            "创建", "构建", "开发", "实现", "解释", "推理",
            "write", "generate", "plan", "analyze", "design", "optimize",
            "create", "build", "develop", "implement", "explain", "reason"
        ]
    
    def route(
        self,
        task_type: TaskComplexity,
        **kwargs
    ) -> BaseLLMProvider:
        """
        路由到合适的 Provider
        
        Args:
            task_type: 任务复杂度
            **kwargs: 额外参数（如 user_preference, force_provider）
        
        Returns:
            合适的 LLM Provider 实例
        
        Raises:
            ValueError: 当没有可用的 Provider 时
        """
        # 强制指定 Provider（用于测试或特殊场景）
        force_provider = kwargs.get("force_provider")
        if force_provider == "qwen" and self.qwen_adapter:
            return self.qwen_adapter
        if force_provider == "local" and self.local_adapter:
            return self.local_adapter
        
        # 根据任务复杂度路由
        if task_type == TaskComplexity.SIMPLE:
            # 简单任务：优先使用本地小模型（7B/14B）
            if self.prefer_local_for_simple and self.local_adapter:
                return self.local_adapter
            # 降级到 Qwen-Turbo（如果本地模型不可用）
            if self.qwen_adapter:
                return self.qwen_adapter
            raise ValueError("No available provider for simple tasks")
        
        elif task_type == TaskComplexity.COMPLEX:
            # 复杂任务：使用云端大模型
            if self.qwen_adapter:
                return self.qwen_adapter
            # 降级到本地模型（如果云端不可用）
            if self.local_adapter:
                logger.warning("Using local model for complex task (Qwen unavailable)")
                return self.local_adapter
            raise ValueError("No available provider for complex tasks")
        
        else:  # MEDIUM
            # 中等复杂度：根据配置选择
            if self.prefer_local_for_medium and self.local_adapter:
                return self.local_adapter
            if self.qwen_adapter:
                return self.qwen_adapter
            if self.local_adapter:
                return self.local_adapter
            raise ValueError("No available provider for medium tasks")
    
    def analyze_complexity(
        self,
        user_input: str,
        context: Optional[Dict[str, Any]] = None
    ) -> TaskComplexity:
        """
        分析用户输入的复杂度（使用规则或关键词匹配）
        
        Args:
            user_input: 用户输入
            context: 上下文信息（可选）
        
        Returns:
            任务复杂度
        """
        # 如果上下文中有明确的复杂度标记，直接使用
        if context and "complexity" in context:
            try:
                return TaskComplexity(context["complexity"])
            except ValueError:
                pass
        
        input_lower = user_input.lower()
        
        # 检查简单指令关键词
        for keyword in self.simple_keywords:
            if keyword.lower() in input_lower:
                return TaskComplexity.SIMPLE
        
        # 检查复杂任务关键词
        for keyword in self.complex_keywords:
            if keyword.lower() in input_lower:
                return TaskComplexity.COMPLEX
        
        # 根据输入长度和结构判断（启发式规则）
        # 短指令通常是简单任务
        if len(user_input.strip().split()) <= 5:
            return TaskComplexity.SIMPLE
        
        # 包含问号可能是中等复杂度对话
        if "?" in user_input or "？" in user_input:
            return TaskComplexity.MEDIUM
        
        # 默认中等复杂度
        return TaskComplexity.MEDIUM
    
    async def process_request(
        self,
        user_input: str,
        context: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> str:
        """
        处理用户请求（便捷方法）
        
        Args:
            user_input: 用户输入
            context: 上下文信息
            **kwargs: 其他参数
        
        Returns:
            模型响应
        """
        # 分析复杂度
        complexity = self.analyze_complexity(user_input, context)
        
        # 路由到合适的 Provider
        provider = self.route(complexity, **kwargs)
        
        # 调用模型
        messages = [{"role": "user", "content": user_input}]
        response = await provider.chat(messages, context=context, **kwargs)
        
        return response
    
    def get_available_providers(self) -> Dict[str, bool]:
        """
        获取可用的 Provider 列表
        
        Returns:
            包含 Provider 可用状态的字典
        """
        return {
            "qwen": self.qwen_adapter is not None,
            "local": self.local_adapter is not None,
        }
