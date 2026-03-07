"""
Text-to-Speech (TTS) - 语音合成模块

支持多种语音合成服务提供商
"""

import asyncio
import logging
import os
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


def _get_dashscope_key() -> Optional[str]:
    """统一获取百炼/DashScope API Key：env > settings"""
    return (
        os.environ.get("DASHSCOPE_API_KEY")
        or os.environ.get("QWEN_API_KEY")
        or os.environ.get("QWEN_AI_API_KEY")
        or getattr(settings, "QWEN_AI_API_KEY", None)
        or getattr(settings, "QWEN_API_KEY", None)
        or getattr(settings, "DASHSCOPE_API_KEY", None)
    )


class AliyunTTSProvider(BaseTTSProvider):
    """阿里云语音合成提供者（百炼平台，DASHSCOPE_API_KEY 通用）"""

    def __init__(self, api_key: Optional[str] = None, app_key: Optional[str] = None):
        self.api_key = api_key or _get_dashscope_key()
        self.app_key = app_key or getattr(settings, "ALIYUN_APP_KEY", None)
        self._synthesizer = None
        self._dashscope = None
        self._Audio = None
        try:
            import dashscope
            self._dashscope = dashscope
            if self.api_key:
                dashscope.api_key = self.api_key
            try:
                from dashscope.audio.tts_v2 import SpeechSynthesizer
                self._synthesizer_cls = SpeechSynthesizer
                self.available = True
            except ImportError:
                from dashscope import Audio
                self._Audio = Audio
                self._synthesizer_cls = None
                self.available = True
            self.available = True
        except ImportError:
            self.available = False
            self._synthesizer_cls = None
            self._Audio = None
            logger.warning("dashscope not installed. AliyunTTSProvider will not work.")

    async def synthesize(
        self,
        text: str,
        voice: str = "default",
        language: str = "zh-CN",
        speed: float = 1.0,
        pitch: float = 1.0,
        **kwargs
    ) -> bytes:
        """使用阿里云百炼语音合成（cosyvoice / sambert）"""
        if not self.available:
            raise NotImplementedError("dashscope package is required for AliyunTTSProvider")
        if not self.api_key:
            raise ValueError(
                "API key is required. Set DASHSCOPE_API_KEY or QWEN_API_KEY in .env"
            )
        if not (text or str(text).strip()):
            return b""
        text = str(text).strip()
        # 过滤无效文本：单字符、纯标点等，CosyVoice 会报 418
        if len(text) < 2 or not any(c.isalnum() or "\u4e00" <= c <= "\u9fff" for c in text):
            logger.debug("AliyunTTS 跳过无效文本: %r", text[:50])
            return b""
        # Edge TTS 音色名映射到 CosyVoice（zh-CN-XiaoxiaoNeural -> longanyang）
        _COSYVOICE_VOICE_MAP = {
            "zh-cn-xiaoxiaoneural": "longanyang",
            "zh-cn-yunxineural": "longanhuan",
            "zh-cn-yunyangneural": "longanyang",
            "zh-cn-xiaoyineural": "longanhuan",
        }
        raw_voice = (voice or "").strip().lower()
        voice_id = _COSYVOICE_VOICE_MAP.get(raw_voice) if raw_voice else None
        if not voice_id:
            voice_id = "longanyang" if voice in ("default", "") else voice
        # Edge 格式音色（zh-CN-XxxNeural）CosyVoice 不支持，统一用 longanyang
        if "neural" in str(voice_id).lower() or "zh-cn-" in str(voice_id).lower():
            voice_id = "longanyang"
        try:
            if self._synthesizer_cls is not None:
                def _call() -> bytes:
                    syn = self._synthesizer_cls(
                        model="cosyvoice-v3-flash",
                        voice=voice_id,
                    )
                    out = syn.call(text)
                    if out and isinstance(out, bytes):
                        return out
                    raise ValueError("SpeechSynthesizer returned empty or invalid audio")
                audio = await asyncio.to_thread(_call)
                return audio
            if self._Audio is not None:
                def _audio_call() -> bytes:
                    resp = self._Audio.call(
                        model="sambert-zhijia-v1",
                        text=text,
                        voice=voice_id,
                        format="wav",
                        sample_rate=16000,
                        **kwargs
                    )
                    if resp.status_code == 200 and hasattr(resp, "output") and resp.output:
                        out = resp.output
                        audio_b64 = getattr(out, "audio", None) or (out.get("audio") if isinstance(out, dict) else None)
                        if audio_b64:
                            import base64
                            return base64.b64decode(audio_b64)
                    raise Exception(getattr(resp, "message", "Aliyun TTS API error") or "Unknown error")
                return await asyncio.to_thread(_audio_call)
            raise NotImplementedError("No TTS backend available")
        except Exception as e:
            logger.error("AliyunTTSProvider.synthesize error: %s", e)
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
        """列出可用的语音列表（cosyvoice / sambert）"""
        voices = [
            {"id": "longanyang", "name": "龙安阳", "gender": "female", "language": "zh-CN"},
            {"id": "zhijia", "name": "知加", "gender": "female", "language": "zh-CN"},
            {"id": "zhiyan", "name": "知燕", "gender": "female", "language": "zh-CN"},
            {"id": "zhijing", "name": "知静", "gender": "female", "language": "zh-CN"},
        ]
        if language:
            return [v for v in voices if v["language"] == language]
        return voices


# Edge TTS fallback voices when primary fails (Microsoft service can be unstable)
_EDGE_TTS_FALLBACK_VOICES = [
    "zh-CN-XiaoxiaoNeural",
    "zh-CN-YunxiNeural",
    "zh-CN-YunyangNeural",
    "zh-CN-XiaoyiNeural",
]


def _resolve_edge_voice(voice: str, language: str = "zh-CN") -> str:
    """Resolve 'default' or invalid voice to a valid Edge TTS voice."""
    if voice and voice != "default" and voice.startswith(("zh-", "en-", "ja-", "ko-")):
        return voice
    # Default Chinese voices
    if language.startswith("zh"):
        return "zh-CN-XiaoxiaoNeural"
    return "en-US-JennyNeural"


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

        text = (text or "").strip()
        if not text:
            raise ValueError("Text cannot be empty for TTS synthesis")

        voice = _resolve_edge_voice(voice, language)
        # Build try list: primary voice first, then fallbacks we haven't tried
        to_try = [voice] + [v for v in _EDGE_TTS_FALLBACK_VOICES if v != voice]

        for attempt, try_voice in enumerate(to_try):
            if attempt > 0:
                logger.info("Edge TTS retry with fallback voice: %s", try_voice)

            try:
                communicate_params = {
                    "text": text,
                    "voice": try_voice,
                }
                if speed != 1.0:
                    communicate_params["rate"] = f"{'+' if speed >= 1 else ''}{int((speed - 1.0) * 100)}%"
                if pitch != 1.0:
                    communicate_params["pitch"] = f"{'+' if pitch >= 1 else ''}{int((pitch - 1.0) * 50)}Hz"

                communicate = self.edge_tts.Communicate(**communicate_params)
                audio_data = b""
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        audio_data += chunk["data"]

                if audio_data:
                    return audio_data
                logger.warning("Edge TTS returned empty audio for voice %s, trying fallback", try_voice)
            except Exception as e:
                err_name = type(e).__name__
                if "NoAudioReceived" in err_name or "No audio" in str(e):
                    logger.warning("Edge TTS NoAudioReceived for voice %s, trying fallback: %s", try_voice, e)
                    continue
                logger.error("Error in EdgeTTSProvider.synthesize: %s", e)
                raise

        raise RuntimeError("No audio received from Edge TTS after retries")
    
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

        text = (text or "").strip()
        if not text:
            raise ValueError("Text cannot be empty for TTS synthesis")

        voice = _resolve_edge_voice(voice, language)

        communicate_params = {
            "text": text,
            "voice": voice,
        }
        if speed != 1.0:
            communicate_params["rate"] = f"{'+' if speed >= 1 else ''}{int((speed - 1.0) * 100)}%"
        if pitch != 1.0:
            communicate_params["pitch"] = f"{'+' if pitch >= 1 else ''}{int((pitch - 1.0) * 50)}Hz"

        communicate = self.edge_tts.Communicate(**communicate_params)
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                yield chunk["data"]
    
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
