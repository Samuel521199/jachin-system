"""
Call Types - 调用方式定义

定义支持的各种调用方式
"""

from enum import Enum
from typing import List, Dict, Any, Optional, AsyncIterator, Union
from dataclasses import dataclass
from abc import ABC, abstractmethod


class CallType(str, Enum):
    """支持的调用方式"""
    TEXT = "text"  # 文本输入
    STREAM = "stream"  # 流式输出
    IMAGE = "image"  # 图像输入
    VIDEO = "video"  # 视频输入
    AUDIO = "audio"  # 音频输入
    WEB_SEARCH = "web_search"  # 联网搜索
    TOOL_CALL = "tool_call"  # 工具调用
    ASYNC = "async"  # 异步调用
    DOCUMENT = "document"  # 文档理解


@dataclass
class TextInput:
    """文本输入"""
    content: str
    role: str = "user"


@dataclass
class ImageInput:
    """图像输入"""
    image_url: Optional[str] = None
    image_base64: Optional[str] = None
    image_bytes: Optional[bytes] = None


@dataclass
class VideoInput:
    """视频输入"""
    video_url: Optional[str] = None
    video_base64: Optional[str] = None
    video_bytes: Optional[bytes] = None


@dataclass
class AudioInput:
    """音频输入"""
    audio_url: Optional[str] = None
    audio_base64: Optional[str] = None
    audio_bytes: Optional[bytes] = None
    format: str = "wav"  # wav, mp3, etc.


@dataclass
class ToolCall:
    """工具调用"""
    name: str
    parameters: Dict[str, Any]
    description: Optional[str] = None


@dataclass
class DocumentInput:
    """文档理解输入"""
    document_url: Optional[str] = None
    document_base64: Optional[str] = None
    document_bytes: Optional[bytes] = None
    document_type: str = "pdf"  # pdf, docx, txt, etc.


@dataclass
class WebSearchConfig:
    """联网搜索配置"""
    enabled: bool = True
    max_results: int = 5
    search_engine: str = "bing"  # bing, google, etc.


@dataclass
class CallOptions:
    """调用选项"""
    temperature: float = 0.7
    max_tokens: int = 2000
    top_p: float = 0.9
    top_k: int = 50
    stream: bool = False
    web_search: Optional[WebSearchConfig] = None
    tools: Optional[List[ToolCall]] = None
    async_mode: bool = False


@dataclass
class CallRequest:
    """调用请求"""
    call_type: CallType
    messages: List[Dict[str, Any]]
    options: Optional[CallOptions] = None
    context: Optional[Dict[str, Any]] = None


@dataclass
class CallResponse:
    """调用响应"""
    content: str
    usage: Optional[Dict[str, Any]] = None
    model: Optional[str] = None
    finish_reason: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None


class CallHandler(ABC):
    """调用处理器抽象基类"""

    @abstractmethod
    async def handle(self, request: CallRequest) -> Union[CallResponse, AsyncIterator[str]]:
        """
        处理调用请求

        Args:
            request: 调用请求

        Returns:
            调用响应或流式迭代器
        """
        pass

    @abstractmethod
    def supports(self, call_type: CallType) -> bool:
        """
        检查是否支持某种调用方式

        Args:
            call_type: 调用方式

        Returns:
            是否支持
        """
        pass
