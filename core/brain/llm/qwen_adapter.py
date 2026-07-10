"""
QwenAdapter - 阿里云 Qwen 模型适配器

封装阿里云 DashScope SDK，实现 BaseLLMProvider 接口。
"""

import json
from typing import List, Dict, Optional, AsyncIterator, Any
import logging

try:
    import dashscope
    from dashscope import Generation
    # dashscope 1.25+ 将 Embedding 重命名为 TextEmbedding
    try:
        from dashscope import TextEmbedding as Embedding
    except ImportError:
        from dashscope import Embedding
    DASHSCOPE_AVAILABLE = True
except ImportError:
    DASHSCOPE_AVAILABLE = False
    logging.warning("dashscope not installed. QwenAdapter will not work.")

from core.config import settings
from .base import BaseLLMProvider

logger = logging.getLogger(__name__)


class QwenAdapter(BaseLLMProvider):
    """阿里云 Qwen 模型提供者"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "qwen-turbo",
        **kwargs
    ):
        """
        初始化 Qwen Adapter

        Args:
            api_key: 阿里云 API Key（如果为 None，则从环境变量读取）
            model: 模型名称，可选值：qwen-turbo, qwen-plus, qwen-max
            **kwargs: 其他参数
        """
        if not DASHSCOPE_AVAILABLE:
            raise ImportError(
                "dashscope package is required for QwenAdapter. "
                "Install it with: pip install dashscope"
            )

        # Priority: parameter > settings (QWEN_API_KEY 已由 model_validator 统一)
        self.api_key = (
            api_key
            or settings.QWEN_API_KEY
            or settings.DASHSCOPE_API_KEY
            or settings.QWEN_AI_API_KEY
        )
        if not self.api_key:
            raise ValueError(
                "Qwen API Key is required. "
                "Set one of these environment variables: QWEN_API_KEY, DASHSCOPE_API_KEY, or QWEN_AI_API_KEY. "
                "Or pass api_key as parameter."
            )

        self.model = model
        dashscope.api_key = self.api_key

        # 默认参数
        self.default_temperature = kwargs.get("temperature", 0.7)
        self.default_max_tokens = kwargs.get("max_tokens", 2000)

    async def chat(
        self,
        messages: List[Dict[str, str]],
        context: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> str:
        """
        同步聊天接口

        Args:
            messages: 消息列表
            context: 上下文信息（可选）
            **kwargs: 其他参数（temperature, max_tokens 等）

        Returns:
            模型返回的文本响应
        """
        try:
            # 准备参数
            call_params = {
                "model": self.model,
                "messages": messages,
                "temperature": kwargs.get("temperature", self.default_temperature),
                "max_tokens": kwargs.get("max_tokens", self.default_max_tokens),
            }

            # 如果有上下文，可以添加到 system message
            if context:
                system_message = context.get("system_message")
                if system_message:
                    messages_with_system = [
                        {"role": "system", "content": system_message}
                    ] + messages
                    call_params["messages"] = messages_with_system

            # 调用 DashScope API
            response = Generation.call(**call_params)

            # 检查响应状态
            if response.status_code == 200:
                return response.output.choices[0].message.content
            else:
                error_msg = f"Qwen API error: {response.message}"
                logger.error(error_msg)
                raise Exception(error_msg)

        except Exception as e:
            logger.error(f"Error calling Qwen API: {str(e)}")
            raise

    async def stream_chat(
        self,
        messages: List[Dict[str, str]],
        context: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> AsyncIterator[str]:
        """
        流式聊天接口

        Args:
            messages: 消息列表
            context: 上下文信息（可选）
            **kwargs: 其他参数

        Yields:
            流式返回的文本片段
        """
        try:
            # 准备参数
            call_params = {
                "model": self.model,
                "messages": messages,
                "stream": True,
                "temperature": kwargs.get("temperature", self.default_temperature),
                "max_tokens": kwargs.get("max_tokens", self.default_max_tokens),
            }

            # 如果有上下文，添加 system message
            if context:
                system_message = context.get("system_message")
                if system_message:
                    messages_with_system = [
                        {"role": "system", "content": system_message}
                    ] + messages
                    call_params["messages"] = messages_with_system

            # 调用 DashScope API（流式）
            responses = Generation.call(**call_params)

            for response in responses:
                if response.status_code == 200:
                    if response.output and response.output.choices:
                        content = response.output.choices[0].message.content
                        if content:
                            yield content
                else:
                    error_msg = f"Qwen API error: {response.message}"
                    logger.error(error_msg)
                    raise Exception(error_msg)

        except Exception as e:
            logger.error(f"Error in Qwen stream API: {str(e)}")
            raise

    async def embed(
        self,
        texts: List[str],
        **kwargs
    ) -> List[List[float]]:
        """
        文本嵌入接口

        Args:
            texts: 文本列表
            **kwargs: 其他参数

        Returns:
            嵌入向量列表
        """
        try:
            # Qwen 使用 text-embedding-v2 模型
            embedding_model = kwargs.get("embedding_model", "text-embedding-v2")

            response = Embedding.call(
                model=embedding_model,
                input=texts
            )

            if response.status_code == 200:
                return [item["embedding"] for item in response.output["embeddings"]]
            else:
                error_msg = f"Qwen Embedding API error: {response.message}"
                logger.error(error_msg)
                raise Exception(error_msg)

        except Exception as e:
            logger.error(f"Error calling Qwen Embedding API: {str(e)}")
            raise

    def get_model_info(self) -> Dict[str, Any]:
        """
        获取模型信息

        Returns:
            包含模型名称、版本等信息的字典
        """
        return {
            "provider": "qwen",
            "model": self.model,
            "api_key_set": bool(self.api_key),
            "dashscope_available": DASHSCOPE_AVAILABLE,
        }

    async def health_check(self) -> bool:
        """
        健康检查

        Returns:
            模型服务是否可用
        """
        try:
            # 发送一个简单的测试请求
            test_messages = [{"role": "user", "content": "test"}]
            response = Generation.call(
                model=self.model,
                messages=test_messages,
                max_tokens=1
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Qwen health check failed: {str(e)}")
            return False
