"""
QwenAdapter V2 - 增强版阿里云 Qwen 模型适配器

支持多地域、多模态、多种调用方式
"""

import os
import json
import base64
from typing import List, Dict, Optional, AsyncIterator, Any
import logging

try:
    import dashscope
    from dashscope import Generation, MultiModalConversation
    # Note: In newer versions of dashscope, Embedding was renamed to TextEmbedding
    # Try both for compatibility
    try:
        from dashscope import TextEmbedding as Embedding
    except ImportError:
        from dashscope import Embedding  # Fallback for older versions
    DASHSCOPE_AVAILABLE = True
except ImportError:
    DASHSCOPE_AVAILABLE = False
    logging.warning("dashscope not installed. QwenAdapter will not work.")

from .base import BaseLLMProvider
from .regions import Region, RegionConfig, get_region_config, get_default_region
from .call_types import (
    CallType, ImageInput, VideoInput, AudioInput, 
    DocumentInput, WebSearchConfig, ToolCall
)
from .qwen_models import (
    get_model_capabilities, is_stream_required, 
    supports_modality, QwenModel
)

logger = logging.getLogger(__name__)


def _extract_response_content(response) -> str:
    """安全地从 Qwen API 响应中提取内容"""
    if not response or response.status_code != 200:
        error_msg = f"Qwen API error (status {response.status_code if response else 'None'}): {response.message if response else 'No response'}"
        logger.error(error_msg)
        raise Exception(error_msg)
    
    if not response.output:
        error_msg = "Qwen API returned empty output"
        logger.error(error_msg)
        raise Exception(error_msg)
    
    # 支持新版本的响应格式：直接包含 text 字段
    if hasattr(response.output, 'text') and response.output.text:
        content = response.output.text
        # 确保返回的是字符串类型，并且是 UTF-8 编码
        if isinstance(content, bytes):
            content = content.decode('utf-8')
        elif not isinstance(content, str):
            content = str(content)
        return content
    
    # 支持旧版本的响应格式：output.choices[0].message.content
    if hasattr(response.output, 'choices') and response.output.choices:
        if response.output.choices[0].message:
            content = response.output.choices[0].message.content
            # 确保返回的是字符串类型，并且是 UTF-8 编码
            if isinstance(content, bytes):
                content = content.decode('utf-8')
            elif not isinstance(content, str):
                content = str(content)
            return content
    
    # 如果都不匹配，记录详细信息以便调试
    error_msg = f"Qwen API returned unsupported response format: {response.output}"
    logger.error(error_msg)
    raise Exception(error_msg)


class QwenAdapterV2(BaseLLMProvider):
    """增强版阿里云 Qwen 模型提供者"""
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "qwen-turbo",
        region: Optional[Region] = None,
        **kwargs
    ):
        """
        初始化 Qwen Adapter V2
        
        Args:
            api_key: 阿里云 API Key（如果为 None，则从环境变量读取）
            model: 模型名称
            region: 地域（如果为 None，则使用默认地域）
            **kwargs: 其他参数
        """
        if not DASHSCOPE_AVAILABLE:
            raise ImportError(
                "dashscope package is required for QwenAdapter. "
                "Install it with: pip install dashscope"
            )
        
        # 获取API Key
        from core.config import settings
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
        self.region = region or get_default_region()
        self.region_config = get_region_config(self.region)
        
        # 设置API Key和地域
        dashscope.api_key = self.api_key
        # 注意：dashscope SDK会自动处理地域，但我们可以通过base_url覆盖
        if hasattr(dashscope, 'api_base'):
            dashscope.api_base = self.region_config.base_url
        
        # 默认参数
        self.default_temperature = kwargs.get("temperature", 0.7)
        self.default_max_tokens = kwargs.get("max_tokens", 2000)
        
        # 获取模型能力
        self.capabilities = get_model_capabilities(model)
    
    async def chat(
        self,
        messages: List[Dict[str, str]],
        context: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> str:
        """同步聊天接口"""
        # 检查是否需要强制使用流式
        if is_stream_required(self.model):
            # 对于仅支持流式的模型，收集流式输出
            full_response = ""
            async for chunk in self.stream_chat(messages, context, **kwargs):
                full_response += chunk
            return full_response
        
        try:
            call_params = {
                "model": self.model,
                "messages": self._prepare_messages(messages, context),
                "temperature": kwargs.get("temperature", self.default_temperature),
                "max_tokens": kwargs.get("max_tokens", self.default_max_tokens),
            }
            
            response = Generation.call(**call_params)
            return _extract_response_content(response)
        
        except Exception as e:
            logger.error(f"Error calling Qwen API: {str(e)}")
            raise
    
    async def stream_chat(
        self,
        messages: List[Dict[str, str]],
        context: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> AsyncIterator[str]:
        """流式聊天接口"""
        try:
            call_params = {
                "model": self.model,
                "messages": self._prepare_messages(messages, context),
                "stream": True,
                "temperature": kwargs.get("temperature", self.default_temperature),
                "max_tokens": kwargs.get("max_tokens", self.default_max_tokens),
            }
            
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
    
    async def chat_with_image(
        self,
        messages: List[Dict[str, Any]],
        images: List[ImageInput],
        context: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> str:
        """图像输入聊天接口"""
        if not self.capabilities.supports_image:
            raise ValueError(f"Model {self.model} does not support image input")
        
        # 检查是否需要流式
        if is_stream_required(self.model):
            full_response = ""
            async for chunk in self.stream_chat_with_image(messages, images, context, **kwargs):
                full_response += chunk
            return full_response
        
        try:
            # 准备多模态消息
            multimodal_messages = self._prepare_multimodal_messages(messages, images, context)
            
            call_params = {
                "model": self.model,
                "messages": multimodal_messages,
                "temperature": kwargs.get("temperature", self.default_temperature),
                "max_tokens": kwargs.get("max_tokens", self.default_max_tokens),
            }
            
            # 使用MultiModalConversation进行多模态调用
            response = MultiModalConversation.call(**call_params)
            return _extract_response_content(response)
        
        except Exception as e:
            logger.error(f"Error calling Qwen Multimodal API: {str(e)}")
            raise
    
    async def stream_chat_with_image(
        self,
        messages: List[Dict[str, Any]],
        images: List[ImageInput],
        context: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> AsyncIterator[str]:
        """流式图像输入聊天接口"""
        if not self.capabilities.supports_image:
            raise ValueError(f"Model {self.model} does not support image input")
        
        try:
            multimodal_messages = self._prepare_multimodal_messages(messages, images, context)
            
            call_params = {
                "model": self.model,
                "messages": multimodal_messages,
                "stream": True,
                "temperature": kwargs.get("temperature", self.default_temperature),
                "max_tokens": kwargs.get("max_tokens", self.default_max_tokens),
            }
            
            responses = MultiModalConversation.call(**call_params)
            
            for response in responses:
                if response.status_code == 200:
                    if response.output and response.output.choices:
                        content = response.output.choices[0].message.content
                        if content:
                            yield content
                else:
                    error_msg = f"Qwen Multimodal API error: {response.message}"
                    logger.error(error_msg)
                    raise Exception(error_msg)
        
        except Exception as e:
            logger.error(f"Error in Qwen Multimodal stream API: {str(e)}")
            raise
    
    async def chat_with_web_search(
        self,
        messages: List[Dict[str, str]],
        web_search_config: WebSearchConfig,
        context: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> str:
        """联网搜索聊天接口"""
        if not self.capabilities.supports_web_search:
            raise ValueError(f"Model {self.model} does not support web search")
        
        try:
            call_params = {
                "model": self.model,
                "messages": self._prepare_messages(messages, context),
                "temperature": kwargs.get("temperature", self.default_temperature),
                "max_tokens": kwargs.get("max_tokens", self.default_max_tokens),
            }
            
            # 添加联网搜索参数
            if web_search_config.enabled:
                call_params["enable_search"] = True
                # 注意：具体的搜索参数可能因API版本而异
            
            response = Generation.call(**call_params)
            return _extract_response_content(response)
        
        except Exception as e:
            logger.error(f"Error calling Qwen API with web search: {str(e)}")
            raise
    
    async def chat_with_audio(
        self,
        messages: List[Dict[str, Any]],
        audios: List[AudioInput],
        context: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> str:
        """音频输入聊天接口"""
        if not self.capabilities.supports_audio:
            raise ValueError(f"Model {self.model} does not support audio input")
        
        try:
            # 准备多模态消息
            multimodal_messages = self._prepare_messages(messages, context)
            
            # 将音频添加到消息中
            for audio in audios:
                audio_content = {}
                
                if audio.audio_url:
                    audio_content["url"] = audio.audio_url
                elif audio.audio_base64:
                    audio_content["data"] = audio.audio_base64
                elif audio.audio_bytes:
                    import base64
                    audio_content["data"] = base64.b64encode(audio.audio_bytes).decode('utf-8')
                else:
                    raise ValueError("Audio input must provide audio_url, audio_base64, or audio_bytes")
                
                audio_content["type"] = "audio"
                audio_content["format"] = audio.format
                
                # 添加到最后一条用户消息
                if multimodal_messages and multimodal_messages[-1]["role"] == "user":
                    if isinstance(multimodal_messages[-1]["content"], list):
                        multimodal_messages[-1]["content"].append(audio_content)
                    else:
                        # 转换为多模态格式
                        text_content = multimodal_messages[-1]["content"]
                        multimodal_messages[-1]["content"] = [
                            {"type": "text", "text": text_content},
                            audio_content
                        ]
                else:
                    # 创建新的用户消息
                    multimodal_messages.append({
                        "role": "user",
                        "content": [audio_content]
                    })
            
            call_params = {
                "model": self.model,
                "messages": multimodal_messages,
                "temperature": kwargs.get("temperature", self.default_temperature),
                "max_tokens": kwargs.get("max_tokens", self.default_max_tokens),
            }
            
            # 使用 MultiModalConversation 进行音频输入
            response = MultiModalConversation.call(**call_params)
            return _extract_response_content(response)
        
        except Exception as e:
            logger.error(f"Error in Qwen audio chat API: {str(e)}")
            raise
    
    async def chat_with_tools(
        self,
        messages: List[Dict[str, str]],
        tools: List[ToolCall],
        context: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """工具调用接口"""
        if not self.capabilities.supports_tool_call:
            raise ValueError(f"Model {self.model} does not support tool calling")
        
        try:
            # 准备工具定义
            tools_def = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description or "",
                        "parameters": tool.parameters,
                    }
                }
                for tool in tools
            ]
            
            call_params = {
                "model": self.model,
                "messages": self._prepare_messages(messages, context),
                "tools": tools_def,
                "temperature": kwargs.get("temperature", self.default_temperature),
                "max_tokens": kwargs.get("max_tokens", self.default_max_tokens),
            }
            
            response = Generation.call(**call_params)
            
            if response.status_code == 200:
                content = _extract_response_content(response)
                result = {
                    "content": content,
                    "tool_calls": [],
                }
                
                # 提取工具调用
                if hasattr(response.output.choices[0].message, 'tool_calls'):
                    result["tool_calls"] = response.output.choices[0].message.tool_calls
                
                return result
            else:
                error_msg = f"Qwen API error: {response.message}"
                logger.error(error_msg)
                raise Exception(error_msg)
        
        except Exception as e:
            logger.error(f"Error calling Qwen API with tools: {str(e)}")
            raise
    
    def supports_call_type(self, call_type: CallType) -> bool:
        """检查是否支持某种调用方式"""
        support_map = {
            CallType.TEXT: self.capabilities.supports_text,
            CallType.STREAM: self.capabilities.supports_stream,
            CallType.IMAGE: self.capabilities.supports_image,
            CallType.VIDEO: self.capabilities.supports_video,
            CallType.AUDIO: self.capabilities.supports_audio,
            CallType.WEB_SEARCH: self.capabilities.supports_web_search,
            CallType.TOOL_CALL: self.capabilities.supports_tool_call,
            CallType.ASYNC: self.capabilities.supports_async,
            CallType.DOCUMENT: self.capabilities.supports_document,
        }
        return support_map.get(call_type, False)
    
    async def embed(
        self,
        texts: List[str],
        **kwargs
    ) -> List[List[float]]:
        """文本嵌入接口"""
        try:
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
        """获取模型信息"""
        return {
            "provider": "qwen",
            "model": self.model,
            "region": self.region.value,
            "region_name": self.region_config.name,
            "api_key_set": bool(self.api_key),
            "dashscope_available": DASHSCOPE_AVAILABLE,
            "capabilities": {
                "text": self.capabilities.supports_text,
                "stream": self.capabilities.supports_stream,
                "image": self.capabilities.supports_image,
                "video": self.capabilities.supports_video,
                "audio": self.capabilities.supports_audio,
                "web_search": self.capabilities.supports_web_search,
                "tool_call": self.capabilities.supports_tool_call,
            }
        }
    
    async def health_check(self) -> bool:
        """健康检查"""
        try:
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
    
    # ========== 辅助方法 ==========
    
    def _prepare_messages(
        self,
        messages: List[Dict[str, Any]],
        context: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """准备消息列表"""
        if context:
            system_message = context.get("system_message")
            if system_message:
                return [
                    {"role": "system", "content": system_message}
                ] + messages
        return messages
    
    def _prepare_multimodal_messages(
        self,
        messages: List[Dict[str, Any]],
        images: List[ImageInput],
        context: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """准备多模态消息"""
        multimodal_messages = []
        
        # 添加系统消息
        if context:
            system_message = context.get("system_message")
            if system_message:
                multimodal_messages.append({"role": "system", "content": system_message})
        
        # 处理消息和图像
        for msg in messages:
            content = []
            
            # 添加文本内容
            if isinstance(msg.get("content"), str):
                content.append({"text": msg["content"]})
            elif isinstance(msg.get("content"), list):
                content.extend(msg["content"])
            
            # 添加图像
            for img in images:
                if img.image_url:
                    content.append({"image": img.image_url})
                elif img.image_base64:
                    content.append({"image": f"data:image/jpeg;base64,{img.image_base64}"})
                elif img.image_bytes:
                    img_base64 = base64.b64encode(img.image_bytes).decode('utf-8')
                    content.append({"image": f"data:image/jpeg;base64,{img_base64}"})
            
            multimodal_messages.append({
                "role": msg.get("role", "user"),
                "content": content
            })
        
        return multimodal_messages
