"""
Model Router - 大小脑路由

根据 query 复杂度返回 ModelType.SMALL | BIG | CLOUD
"""

import logging
from enum import Enum
from typing import Optional, Tuple

from core.brain.llm.base import BaseLLMProvider
from core.brain.llm.factory import LLMProviderFactory
from core.config import settings

logger = logging.getLogger(__name__)


class ModelType(str, Enum):
    """模型类型"""
    SMALL = "small"   # CPU 小模型
    BIG = "big"       # GPU 大模型
    CLOUD = "cloud"   # 云端 API


class ModelRouter:
    """
    模型路由器 - Tier 2 入口网关

    使用关键词/启发式规则估算 query 复杂度分数 (0~1)，
    将请求路由到 SmallModel / BigModel / CloudModel。
    """

    def __init__(
        self,
        small_model_provider: str = "local",
        big_model_provider: Optional[str] = None,
        cloud_model_provider: str = "qwen-v2",
        small_threshold: float = 0.3,
        cloud_threshold: float = 0.8,
    ):
        self.small_model_provider = small_model_provider
        self.big_model_provider = big_model_provider or settings.LLM_PROVIDER
        self.cloud_model_provider = cloud_model_provider
        self.small_threshold = small_threshold
        self.cloud_threshold = cloud_threshold

        self._simple_keywords = [
            "开灯", "关灯", "打开", "关闭", "查询", "获取", "记一下", "提醒",
            "音量", "调大", "调小", "静音", "明天几点", "天气",
            "turn on", "turn off", "open", "close", "get", "read", "show",
        ]
        self._complex_keywords = [
            "编写", "生成", "计划", "分析", "设计", "优化", "论文", "报告",
            "创建", "构建", "开发", "实现", "解释", "推理", "总结",
            "write", "generate", "plan", "analyze", "design", "create",
            "4K", "赛博朋克", "图片", "画", "财报", "摘要",
        ]

    def estimate_complexity_score(self, query: str) -> float:
        """估算 query 的复杂度分数 (0~1)"""
        q = query.strip().lower()
        if not q:
            return 0.0
        score = 0.5
        for kw in self._simple_keywords:
            if kw in q:
                score -= 0.25
                break
        for kw in self._complex_keywords:
            if kw in q:
                score += 0.35
                break
        words = len(q.split())
        if words <= 3:
            score -= 0.15
        elif words >= 30:
            score += 0.2
        if "?" in q or "？" in q:
            score += 0.05
        return max(0.0, min(1.0, score))

    def route(self, query: str) -> ModelType:
        """
        根据 query 返回模型类型

        Returns:
            ModelType.SMALL | ModelType.BIG | ModelType.CLOUD
        """
        score = self.estimate_complexity_score(query)
        if score < self.small_threshold:
            return ModelType.SMALL
        if score > self.cloud_threshold:
            return ModelType.CLOUD
        return ModelType.BIG

    def route_request(self, query: str) -> Tuple[BaseLLMProvider, str]:
        """
        根据 query 路由到合适的 Provider

        Returns:
            (provider, tier): tier 为 "small" | "big" | "cloud"
        """
        model_type = self.route(query)
        tier = model_type.value

        if model_type == ModelType.SMALL:
            try:
                provider = LLMProviderFactory.create_provider(self.small_model_provider)
                logger.debug(f"Route to SmallModel")
                return provider, tier
            except Exception as e:
                logger.warning(f"SmallModel unavailable: {e}, fallback to BigModel")

        if model_type == ModelType.CLOUD:
            try:
                provider = LLMProviderFactory.create_provider(self.cloud_model_provider)
                logger.debug(f"Route to CloudModel")
                return provider, tier
            except Exception as e:
                logger.warning(f"CloudModel unavailable: {e}, fallback to BigModel")

        provider = LLMProviderFactory.create_provider(self.big_model_provider)
        logger.debug(f"Route to BigModel")
        return provider, tier
