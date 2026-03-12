"""
LLMProviderFactory - LLM Provider 工厂类

用于创建和管理 LLM Provider 实例。
"""

import logging
from typing import Optional, Dict, Any

from core.config import settings, get_effective_qwen_api_key
from .base import BaseLLMProvider
from .qwen_adapter import QwenAdapter
from .qwen_adapter_v2 import QwenAdapterV2
from .local_adapter import LocalAdapter
from .regions import Region

logger = logging.getLogger(__name__)


class LLMProviderFactory:
    """LLM Provider 工厂类"""
    
    _providers: Dict[str, BaseLLMProvider] = {}
    
    @staticmethod
    def create_provider(
        provider_type: str,
        **kwargs
    ) -> BaseLLMProvider:
        """
        创建 LLM Provider 实例
        
        Args:
            provider_type: 提供者类型 ("qwen", "local")
            **kwargs: 提供者特定参数
                - qwen: api_key, model
                - local: base_url, model, api_key
        
        Returns:
            BaseLLMProvider 实例
        
        Raises:
            ValueError: 当 provider_type 未知时
        """
        # 检查缓存
        cache_key = f"{provider_type}_{id(kwargs)}"
        if cache_key in LLMProviderFactory._providers:
            return LLMProviderFactory._providers[cache_key]
        
        provider: Optional[BaseLLMProvider] = None
        
        if provider_type == "qwen" or provider_type == "qwen-v2":
            # Qwen Provider (支持V2增强版)：优先使用用户保存的覆盖
            api_key = (
                kwargs.pop("api_key", None)
                or get_effective_qwen_api_key()
            )
            model = kwargs.pop("model", None) or settings.LLM_MODEL
            
            if not api_key:
                raise ValueError(
                    "Qwen API Key is required. "
                    "Set one of these environment variables: QWEN_API_KEY, DASHSCOPE_API_KEY, or QWEN_AI_API_KEY. "
                    "Or pass api_key as parameter."
                )
            
            # 支持地域配置
            region = kwargs.pop("region", None)
            if isinstance(region, str):
                try:
                    region = Region(region)
                except ValueError:
                    logger.warning(f"Unknown region: {region}, using default")
                    region = None
            
            # 使用V2版本（支持多地域和多模态）
            if provider_type == "qwen-v2" or kwargs.get("use_v2", True):
                provider = QwenAdapterV2(api_key=api_key, model=model, region=region, **kwargs)
            else:
                # 兼容旧版本
                provider = QwenAdapter(api_key=api_key, model=model, **kwargs)
        
        elif provider_type == "local":
            # Local Provider
            base_url = kwargs.get("base_url") or settings.LOCAL_LLM_URL
            model = kwargs.get("model") or settings.LOCAL_LLM_MODEL
            api_key = kwargs.get("api_key") or settings.LOCAL_LLM_API_KEY
            
            provider = LocalAdapter(
                base_url=base_url,
                model=model,
                api_key=api_key,
                **kwargs
            )
        
        else:
            raise ValueError(
                f"Unknown provider type: {provider_type}. "
                f"Supported types: 'qwen', 'local'"
            )
        
        # 缓存 Provider（可选，根据需求决定是否缓存）
        # LLMProviderFactory._providers[cache_key] = provider
        
        logger.info(f"Created {provider_type} provider: {provider.get_model_info()}")
        return provider
    
    @staticmethod
    def create_router(
        qwen_config: Optional[Dict[str, Any]] = None,
        local_config: Optional[Dict[str, Any]] = None,
        **router_kwargs
    ) -> "ModelRouter":
        """
        创建 ModelRouter 实例（便捷方法）
        
        Args:
            qwen_config: Qwen Adapter 配置（如果为 None，则尝试从环境变量创建）
            local_config: Local Adapter 配置（如果为 None，则尝试从环境变量创建）
            **router_kwargs: Router 其他参数
        
        Returns:
            ModelRouter 实例
        """
        from .router import ModelRouter
        
        qwen_adapter = None
        local_adapter = None
        
        # 创建 Qwen Adapter（如果配置存在）
        if qwen_config is not False:  # False 表示明确禁用
            try:
                if qwen_config:
                    qwen_adapter = LLMProviderFactory.create_provider("qwen", **qwen_config)
                else:
                    # 尝试从配置创建
                    if get_effective_qwen_api_key():
                        qwen_adapter = LLMProviderFactory.create_provider("qwen")
            except Exception as e:
                logger.warning(f"Failed to create Qwen adapter: {e}")
        
        # 创建 Local Adapter（如果配置存在）
        if local_config is not False:  # False 表示明确禁用
            try:
                if local_config:
                    local_adapter = LLMProviderFactory.create_provider("local", **local_config)
                else:
                    # 尝试从配置创建
                    if settings.LOCAL_LLM_URL:
                        local_adapter = LLMProviderFactory.create_provider("local")
            except Exception as e:
                logger.warning(f"Failed to create Local adapter: {e}")
        
        return ModelRouter(
            qwen_adapter=qwen_adapter,
            local_adapter=local_adapter,
            **router_kwargs
        )
    
    @staticmethod
    def clear_cache():
        """清除 Provider 缓存"""
        LLMProviderFactory._providers.clear()
        logger.info("Provider cache cleared")
