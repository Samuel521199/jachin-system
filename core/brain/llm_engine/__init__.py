"""
LLM Engine - 模型路由与推理入口

大小脑协同 (Big-Little Brain)：根据任务复杂度路由到不同模型
"""

from core.brain.llm_engine.router import ModelRouter, ModelType

_router = None


def get_model_router() -> ModelRouter:
    """获取 ModelRouter 单例"""
    global _router
    if _router is None:
        _router = ModelRouter()
    return _router


def route_and_get_llm(query: str):
    """
    便捷函数：根据 query 路由并返回 LLM Provider
    供 AgentOrchestrator 等调用
    """
    from core.brain.llm.base import BaseLLMProvider
    router = get_model_router()
    provider, _ = router.route_request(query)
    return provider


__all__ = ["ModelRouter", "ModelType", "get_model_router", "route_and_get_llm"]
