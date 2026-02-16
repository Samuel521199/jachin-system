"""
Voice Module - 语音模块

提供语音识别（STT）和语音合成（TTS）功能
"""

from .stt import SpeechToText, STTProvider
from .tts import TextToSpeech, TTSProvider

__all__ = [
    "SpeechToText",
    "STTProvider",
    "TextToSpeech",
    "TTSProvider",
]
