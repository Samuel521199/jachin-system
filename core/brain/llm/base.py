"""
BaseLLMProvider - LLM Provider 抽象基类

所有 LLM Provider 必须实现此接口，确保模型无关性。
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Optional, AsyncIterator, Any, Union
from .call_types import (
    CallType, CallRequest, CallResponse, 
    ImageInput, VideoInput, AudioInput, 
    DocumentInput, WebSearchConfig, ToolCall
)


class BaseLLMProvider(ABC):
    """LLM Provider 抽象基类（v6.0 可插拔认知引擎 BaseCognitiveEngine）"""
    
    @abstractmethod
    async def chat(
        self,
        messages: List[Dict[str, str]],
        context: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> str:
        """
        同步聊天接口
        
        Args:
            messages: 消息列表，格式为 [{"role": "user", "content": "..."}]
            context: 上下文信息（可选）
            **kwargs: 其他参数（如 temperature, max_tokens）
        
        Returns:
            模型返回的文本响应
        
        Raises:
            Exception: 当调用失败时抛出异常
        """
        pass
    
    @abstractmethod
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
        
        Raises:
            Exception: 当调用失败时抛出异常
        """
        pass
    
    @abstractmethod
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
            嵌入向量列表，每个文本对应一个向量
        
        Raises:
            Exception: 当调用失败时抛出异常
        """
        pass
    
    @abstractmethod
    def get_model_info(self) -> Dict[str, Any]:
        """
        获取模型信息
        
        Returns:
            包含模型名称、版本等信息的字典
        """
        pass
    
    @abstractmethod
    async def health_check(self) -> bool:
        """
        健康检查
        
        Returns:
            模型服务是否可用
        """
        pass
    
    # ========== 扩展调用方式（可选实现） ==========
    
    async def chat_with_image(
        self,
        messages: List[Dict[str, Any]],
        images: List[ImageInput],
        context: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> str:
        """
        图像输入聊天接口（多模态）
        
        Args:
            messages: 消息列表，可包含图像URL或base64
            images: 图像输入列表
            context: 上下文信息（可选）
            **kwargs: 其他参数
            
        Returns:
            模型返回的文本响应
            
        Raises:
            NotImplementedError: 如果Provider不支持此功能
        """
        raise NotImplementedError("Image input is not supported by this provider")
    
    async def stream_chat_with_image(
        self,
        messages: List[Dict[str, Any]],
        images: List[ImageInput],
        context: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> AsyncIterator[str]:
        """
        流式图像输入聊天接口
        
        Args:
            messages: 消息列表
            images: 图像输入列表
            context: 上下文信息（可选）
            **kwargs: 其他参数
            
        Yields:
            流式返回的文本片段
            
        Raises:
            NotImplementedError: 如果Provider不支持此功能
        """
        raise NotImplementedError("Stream image input is not supported by this provider")
    
    async def chat_with_video(
        self,
        messages: List[Dict[str, Any]],
        videos: List[VideoInput],
        context: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> str:
        """
        视频输入聊天接口
        
        Args:
            messages: 消息列表
            videos: 视频输入列表
            context: 上下文信息（可选）
            **kwargs: 其他参数
            
        Returns:
            模型返回的文本响应
            
        Raises:
            NotImplementedError: 如果Provider不支持此功能
        """
        raise NotImplementedError("Video input is not supported by this provider")
    
    async def chat_with_audio(
        self,
        messages: List[Dict[str, Any]],
        audios: List[AudioInput],
        context: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> str:
        """
        音频输入聊天接口
        
        Args:
            messages: 消息列表
            audios: 音频输入列表
            context: 上下文信息（可选）
            **kwargs: 其他参数
            
        Returns:
            模型返回的文本响应
            
        Raises:
            NotImplementedError: 如果Provider不支持此功能
        """
        raise NotImplementedError("Audio input is not supported by this provider")
    
    async def chat_with_web_search(
        self,
        messages: List[Dict[str, str]],
        web_search_config: WebSearchConfig,
        context: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> str:
        """
        联网搜索聊天接口
        
        Args:
            messages: 消息列表
            web_search_config: 联网搜索配置
            context: 上下文信息（可选）
            **kwargs: 其他参数
            
        Returns:
            模型返回的文本响应（包含搜索结果）
            
        Raises:
            NotImplementedError: 如果Provider不支持此功能
        """
        raise NotImplementedError("Web search is not supported by this provider")
    
    async def chat_with_tools(
        self,
        messages: List[Dict[str, str]],
        tools: List[ToolCall],
        context: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        工具调用接口
        
        Args:
            messages: 消息列表
            tools: 工具调用列表
            context: 上下文信息（可选）
            **kwargs: 其他参数
            
        Returns:
            包含工具调用结果的字典
            
        Raises:
            NotImplementedError: 如果Provider不支持此功能
        """
        raise NotImplementedError("Tool calling is not supported by this provider")
    
    async def chat_with_document(
        self,
        messages: List[Dict[str, str]],
        documents: List[DocumentInput],
        context: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> str:
        """
        文档理解接口
        
        Args:
            messages: 消息列表
            documents: 文档输入列表
            context: 上下文信息（可选）
            **kwargs: 其他参数
            
        Returns:
            模型返回的文本响应（基于文档内容）
            
        Raises:
            NotImplementedError: 如果Provider不支持此功能
        """
        raise NotImplementedError("Document understanding is not supported by this provider")
    
    async def async_chat(
        self,
        messages: List[Dict[str, str]],
        context: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> str:
        """
        异步调用接口（后台任务）
        
        Args:
            messages: 消息列表
            context: 上下文信息（可选）
            **kwargs: 其他参数
            
        Returns:
            任务ID或结果
            
        Raises:
            NotImplementedError: 如果Provider不支持此功能
        """
        raise NotImplementedError("Async calling is not supported by this provider")
    
    def supports_call_type(self, call_type: CallType) -> bool:
        """
        检查是否支持某种调用方式
        
        Args:
            call_type: 调用方式
            
        Returns:
            是否支持
        """
        support_map = {
            CallType.TEXT: True,  # 所有Provider都支持文本
            CallType.STREAM: True,  # 所有Provider都支持流式
            CallType.IMAGE: False,
            CallType.VIDEO: False,
            CallType.AUDIO: False,
            CallType.WEB_SEARCH: False,
            CallType.TOOL_CALL: False,
            CallType.ASYNC: False,
            CallType.DOCUMENT: False,
        }
        return support_map.get(call_type, False)


# v6.0 类型别名：可插拔认知引擎
BaseCognitiveEngine = BaseLLMProvider
