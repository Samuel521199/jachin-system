"""
Text-to-Speech (TTS) - 语音合成模块

支持多种语音合成服务提供商
"""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Optional, Dict, Any, AsyncIterator
from enum import Enum
from abc import ABC, abstractmethod

from core.config import settings

logger = logging.getLogger(__name__)

# Edge TTS：单次合成超时（秒）；最多 1 次 fallback（主音色 + 备用共 2 次尝试）
EDGE_TTS_REQUEST_TIMEOUT_SEC = 3.0
EDGE_TTS_MAX_FALLBACK_COUNT = 1
# TextToSpeech 外层兜底（应 ≥ 单次超时 × 最多尝试次数）
TTS_SYNTHESIZE_OUTER_TIMEOUT_SEC = 8.0

_NEXUS_CONFIG_PATH = Path.home() / ".jachin" / "nexus_config.json"


def _parse_bool_env(val: str | None) -> bool | None:
    if val is None or not str(val).strip():
        return None
    s = str(val).strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    return None


def _read_nexus_tts_enabled() -> bool | None:
    """nexus_config.json 中 tts_enabled；未配置则返回 None。"""
    if not _NEXUS_CONFIG_PATH.exists():
        return None
    try:
        raw = _NEXUS_CONFIG_PATH.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            raw = _NEXUS_CONFIG_PATH.read_text(encoding="utf-16")
        except Exception:
            return None
    except Exception:
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return None
    if not isinstance(data, dict) or "tts_enabled" not in data:
        return None
    return bool(data["tts_enabled"])


def is_tts_globally_enabled(source: str | None = None) -> bool:
    """
    是否允许调用云端 TTS。

    - 若 ``source`` 已给出且不是 ``voice``，直接关闭（文本/CLI 等路径不浪费外连）。
    - 否则：环境变量 JACHIN_TTS_ENABLED / TTS_ENABLED > nexus_config.json ``tts_enabled`` > settings.TTS_ENABLED
    """
    if source is not None and source != "voice":
        return False
    for key in ("JACHIN_TTS_ENABLED", "TTS_ENABLED"):
        v = _parse_bool_env(os.environ.get(key))
        if v is not None:
            return v
    nx = _read_nexus_tts_enabled()
    if nx is not None:
        return nx
    return bool(getattr(settings, "TTS_ENABLED", False))


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
        speed: float = 1.25,
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
        speed: float = 1.25,
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
        speed: float = 1.25,
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
        speed: float = 1.25,
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

    async def _collect_stream_audio(self, communicate: Any) -> bytes:
        audio_data = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data += chunk["data"]
        return audio_data

    async def synthesize(
        self,
        text: str,
        voice: str = "zh-CN-XiaoxiaoNeural",
        language: str = "zh-CN",
        speed: float = 1.25,
        pitch: float = 1.0,
        **kwargs
    ) -> bytes:
        """使用 Edge TTS 合成语音；失败时返回空 bytes，不抛异常（避免拖死事件循环）。"""
        if not self.available:
            logger.error("EdgeTTSProvider: edge-tts 未安装，跳过合成")
            return b""

        text = (text or "").strip()
        if not text:
            return b""

        voice = _resolve_edge_voice(voice, language)
        # 主音色 + 至多 EDGE_TTS_MAX_FALLBACK_COUNT 个备用（合计 ≤ 2 次网络尝试）
        to_try: list[str] = [voice]
        for v in _EDGE_TTS_FALLBACK_VOICES:
            if v != voice and len(to_try) <= EDGE_TTS_MAX_FALLBACK_COUNT:
                to_try.append(v)
                break

        for attempt, try_voice in enumerate(to_try):
            if attempt > 0:
                logger.info("Edge TTS fallback 尝试备用音色: %s", try_voice)

            try:
                communicate_params: Dict[str, Any] = {
                    "text": text,
                    "voice": try_voice,
                }
                if speed != 1.0:
                    communicate_params["rate"] = f"{'+' if speed >= 1 else ''}{int((speed - 1.0) * 100)}%"
                if pitch != 1.0:
                    communicate_params["pitch"] = f"{'+' if pitch >= 1 else ''}{int((pitch - 1.0) * 50)}Hz"

                communicate = self.edge_tts.Communicate(**communicate_params)
                audio_data = await asyncio.wait_for(
                    self._collect_stream_audio(communicate),
                    timeout=EDGE_TTS_REQUEST_TIMEOUT_SEC,
                )

                if audio_data:
                    return audio_data
                logger.error(
                    "Edge TTS 返回空音频 voice=%s（已超时限制 %.1fs 内完成流读取）",
                    try_voice,
                    EDGE_TTS_REQUEST_TIMEOUT_SEC,
                )
            except asyncio.TimeoutError:
                logger.error(
                    "Edge TTS 超时 voice=%s timeout=%.1fs",
                    try_voice,
                    EDGE_TTS_REQUEST_TIMEOUT_SEC,
                )
            except Exception as e:
                err_name = type(e).__name__
                if "NoAudioReceived" in err_name or "No audio" in str(e):
                    logger.error("Edge TTS NoAudioReceived voice=%s: %s", try_voice, e)
                else:
                    logger.error("Edge TTS 合成失败 voice=%s: %s", try_voice, e)

        logger.error("Edge TTS 已用尽允许次数（主音色 + 最多 %d 次 fallback），静默放弃语音", EDGE_TTS_MAX_FALLBACK_COUNT)
        return b""
    
    async def synthesize_stream(
        self,
        text: str,
        voice: str = "zh-CN-XiaoxiaoNeural",
        language: str = "zh-CN",
        speed: float = 1.25,
        pitch: float = 1.0,
        **kwargs
    ) -> AsyncIterator[bytes]:
        """流式合成语音（内部仍受单次超时约束，失败则结束迭代）"""
        if not self.available:
            return
        text = (text or "").strip()
        if not text:
            return

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
        try:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    yield chunk["data"]
        except Exception as e:
            logger.error("Edge TTS synthesize_stream 失败: %s", e)
    
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
        speed: float = 1.25,
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
        if not is_tts_globally_enabled(source="voice"):
            return b""
        if not self.provider:
            logger.error("TTS provider not initialized")
            return b""
        try:
            return await asyncio.wait_for(
                self.provider.synthesize(text, voice, language, speed, pitch, **kwargs),
                timeout=TTS_SYNTHESIZE_OUTER_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError:
            logger.error(
                "TTS synthesize 外层超时 %.1fs，静默放弃语音",
                TTS_SYNTHESIZE_OUTER_TIMEOUT_SEC,
            )
            return b""
        except Exception as e:
            logger.error("TTS synthesize 失败: %s", e)
            return b""
    
    async def synthesize_stream(
        self,
        text: str,
        voice: str = "default",
        language: str = "zh-CN",
        speed: float = 1.25,
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
        if not is_tts_globally_enabled(source="voice"):
            return
        if not self.provider:
            logger.error("TTS provider not initialized")
            return
        try:
            async for chunk in self.provider.synthesize_stream(text, voice, language, speed, pitch, **kwargs):
                yield chunk
        except Exception as e:
            logger.error("TTS synthesize_stream 失败: %s", e)
    
    async def list_voices(self, language: Optional[str] = None) -> list:
        """列出可用的语音列表"""
        if not self.provider:
            return []
        return await self.provider.list_voices(language)
