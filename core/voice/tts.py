"""
Text-to-Speech (TTS) - 语音合成模块

支持多种语音合成服务提供商
"""

import logging
from typing import Optional, Dict, Any, AsyncIterator
from enum import Enum
from abc import ABC, abstractmethod

from core.config import settings

logger = logging.getLogger(__name__)


class TTSProvider(str, Enum):
    """支持的语音合成服务提供商"""
    ALIYUN = "aliyun"  # 阿里云语音合成
    EDGE_TTS = "edge_tts"  # Microsoft Edge TTS（免费，开源）
    BAIDU = "baidu"  # 百度语音合成
    TENCENT = "tencent"  # 腾讯语音合成


class BaseTTSProvider(ABC):
    """语音合成提供者基类"""
    
    @abstractmethod
    async def synthesize(
        self,
        text: str,
        voice: str = "default",
        language: str = "zh-CN",
        speed: float = 1.0,
        pitch: float = 1.0,
        **kwargs
    ) -> bytes:
        """
        将文本合成为语音
        
        Args:
            text: 要合成的文本
            voice: 语音名称/ID
            language: 语言代码
            speed: 语速（0.5-2.0）
            pitch: 音调（0.5-2.0）
            **kwargs: 其他参数
            
        Returns:
            音频数据（bytes）
        """
        pass
    
    @abstractmethod
    async def synthesize_stream(
        self,
        text: str,
        voice: str = "default",
        language: str = "zh-CN",
        speed: float = 1.0,
        pitch: float = 1.0,
        **kwargs
    ) -> AsyncIterator[bytes]:
        """
        流式合成语音
        
        Args:
            text: 要合成的文本
            voice: 语音名称/ID
            language: 语言代码
            speed: 语速
            pitch: 音调
            **kwargs: 其他参数
            
        Yields:
            音频数据块（bytes）
        """
        pass
    
    @abstractmethod
    def list_voices(self, language: Optional[str] = None) -> list:
        """列出可用的语音列表"""
        pass


class AliyunTTSProvider(BaseTTSProvider):
    """阿里云语音合成提供者"""
    
    def __init__(self, api_key: Optional[str] = None, app_key: Optional[str] = None):
        """
        初始化阿里云TTS提供者
        
        Args:
            api_key: 阿里云API Key
            app_key: 阿里云App Key（可选）
        """
        try:
            import dashscope
            from dashscope import Audio
            
            self.dashscope = dashscope
            self.Audio = Audio
            self.available = True
        except ImportError:
            self.available = False
            logger.warning("dashscope not installed. AliyunTTSProvider will not work.")
        
        self.api_key = api_key or settings.QWEN_AI_API_KEY or settings.QWEN_API_KEY
        self.app_key = app_key or settings.ALIYUN_APP_KEY
        
        if self.api_key and self.available:
            self.dashscope.api_key = self.api_key
    
    async def synthesize(
        self,
        text: str,
        voice: str = "default",
        language: str = "zh-CN",
        speed: float = 1.0,
        pitch: float = 1.0,
        **kwargs
    ) -> bytes:
        """使用阿里云语音合成"""
        if not self.available:
            raise NotImplementedError("dashscope package is required for AliyunTTSProvider")
        
        if not self.api_key:
            raise ValueError("API key is required for AliyunTTSProvider")
        
        try:
            # 阿里云语音合成API调用
            # 注意：实际API调用方式可能需要根据阿里云的具体服务调整
            response = self.Audio.call(
                model="sambert-zhijia-v1",  # 或使用其他模型
                text=text,
                voice=voice,
                format="wav",
                sample_rate=16000,
                **kwargs
            )
            
            if response.status_code == 200:
                # 提取音频数据
                if hasattr(response, 'output') and response.output:
                    if hasattr(response.output, 'audio'):
                        import base64
                        audio_base64 = response.output.audio
                        return base64.b64decode(audio_base64)
                    elif hasattr(response.output, 'audio_data'):
                        return response.output.audio_data
                
                # 尝试从响应中提取音频
                result = response.output if hasattr(response, 'output') else response
                if isinstance(result, dict):
                    audio_data = result.get('audio') or result.get('audio_data')
                    if audio_data:
                        import base64
                        if isinstance(audio_data, str):
                            return base64.b64decode(audio_data)
                        return audio_data
            
            error_msg = f"Aliyun TTS API error: {response.message if hasattr(response, 'message') else 'Unknown error'}"
            logger.error(error_msg)
            raise Exception(error_msg)
            
        except Exception as e:
            logger.error(f"Error in AliyunTTSProvider.synthesize: {e}")
            raise
    
    async def synthesize_stream(
        self,
        text: str,
        voice: str = "default",
        language: str = "zh-CN",
        speed: float = 1.0,
        pitch: float = 1.0,
        **kwargs
    ) -> AsyncIterator[bytes]:
        """流式合成语音（如果支持）"""
        # 对于不支持流式的服务，返回完整音频
        audio_data = await self.synthesize(text, voice, language, speed, pitch, **kwargs)
        yield audio_data
    
    def list_voices(self, language: Optional[str] = None) -> list:
        """列出可用的语音列表"""
        # 默认中文语音列表
        voices = [
            {"id": "zhijia", "name": "知加", "gender": "female", "language": "zh-CN"},
            {"id": "zhiyan", "name": "知燕", "gender": "female", "language": "zh-CN"},
            {"id": "zhijing", "name": "知静", "gender": "female", "language": "zh-CN"},
        ]
        
        if language:
            return [v for v in voices if v["language"] == language]
        return voices


class EdgeTTSProvider(BaseTTSProvider):
    """Microsoft Edge TTS 提供者（免费，开源）"""
    
    def __init__(self):
        """初始化 Edge TTS 提供者"""
        try:
            import edge_tts
            self.edge_tts = edge_tts
            self.available = True
        except ImportError:
            self.available = False
            logger.warning("edge-tts package not installed. Install with: pip install edge-tts")
    
    async def synthesize(
        self,
        text: str,
        voice: str = "zh-CN-XiaoxiaoNeural",
        language: str = "zh-CN",
        speed: float = 1.0,
        pitch: float = 1.0,
        **kwargs
    ) -> bytes:
        """使用 Edge TTS 合成语音"""
        if not self.available:
            raise NotImplementedError("edge-tts package is required for EdgeTTSProvider")
        
        try:
            # 构建参数，只在需要时添加rate和pitch
            communicate_params = {
                "text": text,
                "voice": voice,
            }
            
            # 只在speed不等于1.0时添加rate参数
            if speed != 1.0:
                communicate_params["rate"] = f"+{int((speed - 1.0) * 100)}%"
            
            # 只在pitch不等于1.0时添加pitch参数
            if pitch != 1.0:
                communicate_params["pitch"] = f"+{int((pitch - 1.0) * 50)}Hz"
            
            communicate = self.edge_tts.Communicate(**communicate_params)
            
            audio_data = b""
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_data += chunk["data"]
            
            return audio_data
            
        except Exception as e:
            logger.error(f"Error in EdgeTTSProvider.synthesize: {e}")
            raise
    
    async def synthesize_stream(
        self,
        text: str,
        voice: str = "zh-CN-XiaoxiaoNeural",
        language: str = "zh-CN",
        speed: float = 1.0,
        pitch: float = 1.0,
        **kwargs
    ) -> AsyncIterator[bytes]:
        """流式合成语音"""
        if not self.available:
            raise NotImplementedError("edge-tts package is required for EdgeTTSProvider")
        
        try:
            # 构建参数，只在需要时添加rate和pitch
            communicate_params = {
                "text": text,
                "voice": voice,
            }
            
            # 只在speed不等于1.0时添加rate参数
            if speed != 1.0:
                communicate_params["rate"] = f"+{int((speed - 1.0) * 100)}%"
            
            # 只在pitch不等于1.0时添加pitch参数
            if pitch != 1.0:
                communicate_params["pitch"] = f"+{int((pitch - 1.0) * 50)}Hz"
            
            communicate = self.edge_tts.Communicate(**communicate_params)
            
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    yield chunk["data"]
                    
        except Exception as e:
            logger.error(f"Error in EdgeTTSProvider.synthesize_stream: {e}")
            raise
    
    async def list_voices(self, language: Optional[str] = None) -> list:
        """列出可用的语音列表"""
        if not self.available:
            return []
        
        try:
            voices = await self.edge_tts.list_voices()
            if language:
                return [v for v in voices if v["Locale"].startswith(language.split("-")[0])]
            return voices
        except Exception as e:
            logger.error(f"Error listing Edge TTS voices: {e}")
            return []


class TextToSpeech:
    """语音合成管理器"""
    
    def __init__(self, provider: TTSProvider = TTSProvider.EDGE_TTS, **kwargs):
        """
        初始化语音合成管理器
        
        Args:
            provider: 语音合成服务提供商
            **kwargs: 提供者特定参数
        """
        self.provider_type = provider
        self.provider: Optional[BaseTTSProvider] = None
        
        if provider == TTSProvider.ALIYUN:
            self.provider = AliyunTTSProvider(**kwargs)
        elif provider == TTSProvider.EDGE_TTS:
            self.provider = EdgeTTSProvider(**kwargs)
        else:
            raise ValueError(f"Unsupported TTS provider: {provider}")
    
    async def synthesize(
        self,
        text: str,
        voice: str = "default",
        language: str = "zh-CN",
        speed: float = 1.0,
        pitch: float = 1.0,
        **kwargs
    ) -> bytes:
        """
        将文本合成为语音
        
        Args:
            text: 要合成的文本
            voice: 语音名称/ID
            language: 语言代码
            speed: 语速
            pitch: 音调
            **kwargs: 其他参数
            
        Returns:
            音频数据（bytes）
        """
        if not self.provider:
            raise ValueError("TTS provider not initialized")
        
        return await self.provider.synthesize(text, voice, language, speed, pitch, **kwargs)
    
    async def synthesize_stream(
        self,
        text: str,
        voice: str = "default",
        language: str = "zh-CN",
        speed: float = 1.0,
        pitch: float = 1.0,
        **kwargs
    ) -> AsyncIterator[bytes]:
        """
        流式合成语音
        
        Args:
            text: 要合成的文本
            voice: 语音名称/ID
            language: 语言代码
            speed: 语速
            pitch: 音调
            **kwargs: 其他参数
            
        Yields:
            音频数据块（bytes）
        """
        if not self.provider:
            raise ValueError("TTS provider not initialized")
        
        async for chunk in self.provider.synthesize_stream(text, voice, language, speed, pitch, **kwargs):
            yield chunk
    
    async def list_voices(self, language: Optional[str] = None) -> list:
        """列出可用的语音列表"""
        if not self.provider:
            return []
        return await self.provider.list_voices(language)
