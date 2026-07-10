"""
LocalAdapter - 本地模型适配器

封装本地 OpenAI-compatible API（如 vLLM/Ollama），实现 BaseLLMProvider 接口。
"""

import json
from typing import List, Dict, Optional, AsyncIterator, Any
import logging

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False
    logging.warning("httpx not installed. LocalAdapter will not work.")

from core.config import settings
from .base import BaseLLMProvider

logger = logging.getLogger(__name__)


class LocalAdapter(BaseLLMProvider):
    """本地部署的 LLM 提供者（兼容 OpenAI 格式）"""

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: str = "qwen-7b-chat",
        api_key: Optional[str] = None,
        timeout: float = 60.0,
        **kwargs
    ):
        """
        初始化 Local Adapter

        Args:
            base_url: 本地模型服务的基础 URL（如 http://localhost:8000）
            model: 模型名称
            api_key: API Key（如果需要）
            timeout: 请求超时时间（秒）
            **kwargs: 其他参数
        """
        if not HTTPX_AVAILABLE:
            raise ImportError(
                "httpx package is required for LocalAdapter. "
                "Install it with: pip install httpx"
            )

        self.base_url = base_url or settings.LOCAL_LLM_URL
        self.model = model
        self.api_key = api_key or settings.LOCAL_LLM_API_KEY
        self.timeout = timeout

        # 创建 HTTP 客户端
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=timeout
        )

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
            **kwargs: 其他参数

        Returns:
            模型返回的文本响应
        """
        try:
            # 准备请求体（OpenAI 兼容格式）
            request_body = {
                "model": self.model,
                "messages": messages,
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
                    request_body["messages"] = messages_with_system

            # 调用本地 API
            response = await self.client.post(
                "/v1/chat/completions",
                json=request_body
            )
            response.raise_for_status()

            result = response.json()
            return result["choices"][0]["message"]["content"]

        except httpx.HTTPError as e:
            error_msg = f"Local LLM HTTP error: {str(e)}"
            logger.error(error_msg)
            raise Exception(error_msg)
        except Exception as e:
            logger.error(f"Error calling Local LLM API: {str(e)}")
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
            # 准备请求体
            request_body = {
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
                    request_body["messages"] = messages_with_system

            # 流式调用
            async with self.client.stream(
                "POST",
                "/v1/chat/completions",
                json=request_body
            ) as response:
                response.raise_for_status()

                async for line in response.aiter_lines():
                    if not line.strip():
                        continue

                    # OpenAI 流式格式：data: {...}
                    if line.startswith("data: "):
                        data_str = line[6:]  # 移除 "data: " 前缀

                        # 流式结束标记
                        if data_str.strip() == "[DONE]":
                            break

                        try:
                            data = json.loads(data_str)
                            if "choices" in data and data["choices"]:
                                delta = data["choices"][0].get("delta", {})
                                content = delta.get("content", "")
                                if content:
                                    yield content
                        except json.JSONDecodeError:
                            logger.warning(f"Failed to parse stream data: {data_str}")
                            continue

        except httpx.HTTPError as e:
            error_msg = f"Local LLM HTTP error: {str(e)}"
            logger.error(error_msg)
            raise Exception(error_msg)
        except Exception as e:
            logger.error(f"Error in Local LLM stream API: {str(e)}")
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
            # OpenAI 兼容的嵌入接口
            embedding_model = kwargs.get("embedding_model", f"{self.model}-embedding")

            request_body = {
                "model": embedding_model,
                "input": texts,
            }

            response = await self.client.post(
                "/v1/embeddings",
                json=request_body
            )
            response.raise_for_status()

            result = response.json()
            return [item["embedding"] for item in result["data"]]

        except httpx.HTTPError as e:
            error_msg = f"Local LLM Embedding HTTP error: {str(e)}"
            logger.error(error_msg)
            raise Exception(error_msg)
        except Exception as e:
            logger.error(f"Error calling Local LLM Embedding API: {str(e)}")
            raise

    def get_model_info(self) -> Dict[str, Any]:
        """
        获取模型信息

        Returns:
            包含模型名称、版本等信息的字典
        """
        return {
            "provider": "local",
            "model": self.model,
            "base_url": self.base_url,
            "api_key_set": bool(self.api_key),
            "httpx_available": HTTPX_AVAILABLE,
        }

    async def health_check(self) -> bool:
        """
        健康检查

        Returns:
            模型服务是否可用
        """
        try:
            # 尝试访问健康检查端点或模型列表端点
            response = await self.client.get("/health", timeout=5.0)
            return response.status_code == 200
        except httpx.HTTPError:
            # 如果没有健康检查端点，尝试调用模型列表
            try:
                response = await self.client.get("/v1/models", timeout=5.0)
                return response.status_code == 200
            except Exception:
                return False
        except Exception as e:
            logger.error(f"Local LLM health check failed: {str(e)}")
            return False

    async def close(self):
        """关闭 HTTP 客户端"""
        await self.client.aclose()
