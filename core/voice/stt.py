"""
Speech-to-Text (STT) - 语音识别模块

支持多种语音识别服务提供商
"""

import os
import base64
import logging
from typing import Optional, Dict, Any
from enum import Enum
from abc import ABC, abstractmethod

from core.config import settings

logger = logging.getLogger(__name__)


class STTProvider(str, Enum):
    """支持的语音识别服务提供商"""
    ALIYUN = "aliyun"  # 阿里云语音识别
    OPENAI_WHISPER = "openai_whisper"  # OpenAI Whisper（开源）
    BAIDU = "baidu"  # 百度语音识别
    TENCENT = "tencent"  # 腾讯语音识别


class BaseSTTProvider(ABC):
    """语音识别提供者基类"""

    @abstractmethod
    async def recognize(
        self,
        audio_data: bytes,
        format: str = "wav",
        language: str = "zh-CN",
        **kwargs
    ) -> str:
        """
        识别语音为文本

        Args:
            audio_data: 音频数据（bytes）
            format: 音频格式（wav, mp3, m4a等）
            language: 语言代码（zh-CN, en-US等）
            **kwargs: 其他参数

        Returns:
            识别出的文本
        """
        pass

    @abstractmethod
    def supports_format(self, format: str) -> bool:
        """检查是否支持指定的音频格式"""
        pass


class AliyunSTTProvider(BaseSTTProvider):
    """阿里云语音识别提供者

    注意：阿里云ASR API不支持本地文件直传，必须使用公网可访问的URL。
    如果需要使用本地文件，请先上传到OSS或使用临时HTTP服务。
    推荐使用 Whisper 作为替代方案（支持本地文件）。
    """

    def __init__(self, api_key: Optional[str] = None, app_key: Optional[str] = None):
        """
        初始化阿里云STT提供者

        Args:
            api_key: 阿里云API Key（从环境变量读取）
            app_key: 阿里云App Key（可选，用于语音识别服务）
        """
        try:
            import dashscope
            # dashscope.audio.asr.Transcription 用于录音文件识别
            try:
                from dashscope.audio.asr import Transcription
                self.dashscope = dashscope
                self.Transcription = Transcription
                self.available = True
                logger.info("AliyunSTTProvider initialized successfully")
            except ImportError:
                self.available = False
                logger.warning("dashscope.audio.asr.Transcription not available. AliyunSTTProvider will not work.")
        except ImportError:
            self.available = False
            logger.warning("dashscope not installed. AliyunSTTProvider will not work.")

        self.api_key = api_key or settings.QWEN_AI_API_KEY or settings.QWEN_API_KEY
        self.app_key = app_key or settings.ALIYUN_APP_KEY

        if self.api_key and self.available:
            self.dashscope.api_key = self.api_key

    async def recognize(
        self,
        audio_data: bytes,
        format: str = "wav",
        language: str = "zh-CN",
        audio_url: Optional[str] = None,
        **kwargs
    ) -> str:
        """
        使用阿里云语音识别

        注意：阿里云ASR API不支持本地文件直传，必须使用公网可访问的URL。
        如果提供了 audio_url，将使用该URL；否则需要先上传文件到OSS。

        Args:
            audio_data: 音频数据（bytes）- 注意：阿里云API不支持直接使用，需要URL
            format: 音频格式
            language: 语言代码
            audio_url: 公网可访问的音频URL（必需，如果使用阿里云ASR）
            **kwargs: 其他参数
        """
        if not self.available:
            raise NotImplementedError(
                "dashscope package is required for AliyunSTTProvider. "
                "Please install it with: pip install dashscope, "
                "or use Whisper provider instead."
            )

        if not self.api_key:
            raise ValueError("API key is required for AliyunSTTProvider")

        # 阿里云ASR API需要公网可访问的URL，不支持本地文件
        if not audio_url:
            raise ValueError(
                "AliyunSTTProvider requires a public URL (audio_url parameter). "
                "Local files are not supported by Aliyun ASR API. "
                "Please upload the audio to OSS or use Whisper provider instead. "
                "Current default provider is Whisper which supports local files."
            )

        try:
            # 使用 dashscope.audio.asr.Transcription 进行语音识别
            # 模型：paraformer-v2（推荐）或 paraformer-8k-v2
            model = kwargs.get("model", "paraformer-v2")

            # 调用阿里云语音识别API
            # Transcription.call 需要 file_urls 参数（URL列表）
            response = self.Transcription.call(
                model=model,
                file_urls=[audio_url],  # 必须是公网可访问的URL列表
                **kwargs
            )

            if response.status_code == 200:
                # 提取识别结果
                # Transcription API 返回格式：response.output 是列表，每个元素是一个文件的识别结果
                if hasattr(response, 'output') and response.output:
                    if isinstance(response.output, list) and len(response.output) > 0:
                        # 返回第一个文件的识别结果
                        result = response.output[0]
                        if isinstance(result, dict):
                            # 可能的字段：text, sentence, sentences
                            return result.get('text', '') or result.get('sentence', '') or str(result.get('sentences', [''])[0] if isinstance(result.get('sentences'), list) else '')
                    elif isinstance(response.output, dict):
                        return response.output.get('text', '') or response.output.get('sentence', '')

                # 尝试从响应中提取文本
                result = response.output if hasattr(response, 'output') else response
                if isinstance(result, dict):
                    return result.get('text', '') or result.get('sentence', '')

            error_msg = f"Aliyun STT API error: {response.message if hasattr(response, 'message') else 'Unknown error'}"
            logger.error(error_msg)
            raise Exception(error_msg)

        except Exception as e:
            logger.error(f"Error in AliyunSTTProvider.recognize: {e}")
            raise

    def supports_format(self, format: str) -> bool:
        """阿里云支持的格式"""
        supported = ["wav", "mp3", "m4a", "pcm"]
        return format.lower() in supported


class OpenAIWhisperSTTProvider(BaseSTTProvider):
    """OpenAI Whisper 语音识别提供者（开源）"""

    def __init__(self, model: str = "base", model_path: Optional[str] = None):
        """
        初始化 Whisper STT提供者

        Args:
            model: Whisper模型大小（tiny, base, small, medium, large）
            model_path: 自定义模型存储路径（可选）
                       如果未指定，将从环境变量 WHISPER_MODEL_PATH 或 XDG_CACHE_HOME 读取
                       如果都未设置，使用默认路径 ~/.cache/whisper
        """
        try:
            import whisper
            import os
            self.whisper = whisper
            self.available = True
            self.model_name = model
            self.model = None  # 延迟加载

            # 设置模型存储路径
            # 优先级：model_path 参数 > WHISPER_MODEL_PATH > XDG_CACHE_HOME > 默认路径
            if model_path:
                self.model_path = model_path
            elif settings.WHISPER_MODEL_PATH:
                self.model_path = settings.WHISPER_MODEL_PATH
            elif settings.XDG_CACHE_HOME:
                self.model_path = os.path.join(settings.XDG_CACHE_HOME, "whisper")
            else:
                # 默认路径：~/.cache/whisper
                default_cache = os.path.join(os.path.expanduser("~"), ".cache")
                self.model_path = os.path.join(default_cache, "whisper")

            # 设置环境变量，让 Whisper 使用自定义路径
            if self.model_path != os.path.join(os.path.expanduser("~"), ".cache", "whisper"):
                # 如果使用自定义路径，设置 XDG_CACHE_HOME
                # Whisper 会在 $XDG_CACHE_HOME/whisper 查找模型
                cache_dir = os.path.dirname(self.model_path)
                if cache_dir != os.path.join(os.path.expanduser("~"), ".cache"):
                    os.environ["XDG_CACHE_HOME"] = cache_dir
                    logger.info(f"Set XDG_CACHE_HOME to: {cache_dir}")

            # 确保目录存在
            try:
                os.makedirs(self.model_path, exist_ok=True)
            except Exception as e:
                logger.warning(f"Failed to create model directory {self.model_path}: {e}")
                # 如果创建失败，使用默认路径
                default_cache = os.path.join(os.path.expanduser("~"), ".cache")
                self.model_path = os.path.join(default_cache, "whisper")
                os.makedirs(self.model_path, exist_ok=True)
                logger.info(f"Using default path instead: {self.model_path}")

            logger.info(f"Whisper STT provider initialized with model: {model}")
            logger.info(f"Model storage path: {self.model_path}")
        except ImportError:
            self.available = False
            logger.error("whisper package not installed. Install with: pip install openai-whisper")
            logger.error("Or use conda: conda install -c conda-forge openai-whisper")

    async def recognize(
        self,
        audio_data: bytes,
        format: str = "wav",
        language: str = "zh",
        **kwargs
    ) -> str:
        """使用 Whisper 识别语音"""
        if not self.available:
            raise NotImplementedError("whisper package is required for OpenAIWhisperSTTProvider")

        try:
            import tempfile
            import io

            # 延迟加载模型
            if self.model is None:
                logger.info(f"Loading Whisper model: {self.model_name} from {self.model_path}")
                # load_model 会自动使用 XDG_CACHE_HOME 环境变量设置的路径
                # 如果模型文件已存在于自定义路径，也可以直接指定完整路径
                model_file = os.path.join(self.model_path, f"{self.model_name}.pt")
                if os.path.exists(model_file):
                    # 如果模型文件存在，直接加载
                    self.model = self.whisper.load_model(model_file)
                else:
                    # 否则使用模型名称，Whisper 会根据 XDG_CACHE_HOME 下载到正确位置
                    self.model = self.whisper.load_model(self.model_name)

            # 将音频数据保存到临时文件
            with tempfile.NamedTemporaryFile(delete=False, suffix=f".{format}") as tmp_file:
                tmp_file.write(audio_data)
                tmp_path = tmp_file.name

            try:
                # 使用 Whisper 识别
                result = self.model.transcribe(
                    tmp_path,
                    language=language if language != "zh-CN" else "zh",
                    **kwargs
                )

                return result["text"].strip()
            finally:
                # 清理临时文件
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)

        except Exception as e:
            logger.error(f"Error in OpenAIWhisperSTTProvider.recognize: {e}")
            raise

    def supports_format(self, format: str) -> bool:
        """Whisper 支持所有常见音频格式"""
        return True  # Whisper 支持多种格式


class SpeechToText:
    """语音转文字门面类"""

    def __init__(self, provider: STTProvider = STTProvider.OPENAI_WHISPER, model_path: Optional[str] = None):
        """
        初始化语音转文字服务

        Args:
            provider: STT提供者类型
            model_path: 自定义模型存储路径（仅对 Whisper 有效）
        """
    """语音识别管理器"""

    def __init__(self, provider: STTProvider = STTProvider.ALIYUN, **kwargs):
        """
        初始化语音识别管理器

        Args:
            provider: 语音识别服务提供商
            **kwargs: 提供者特定参数
        """
        self.provider_type = provider
        self.provider: Optional[BaseSTTProvider] = None

        if provider == STTProvider.ALIYUN:
            self.provider = AliyunSTTProvider(**kwargs)
        elif provider == STTProvider.OPENAI_WHISPER:
            self.provider = OpenAIWhisperSTTProvider(**kwargs)
        else:
            raise ValueError(f"Unsupported STT provider: {provider}")

    async def recognize(
        self,
        audio_data: bytes,
        format: str = "wav",
        language: str = "zh-CN",
        **kwargs
    ) -> str:
        """
        识别语音为文本

        Args:
            audio_data: 音频数据
            format: 音频格式
            language: 语言代码
            **kwargs: 其他参数

        Returns:
            识别出的文本
        """
        if not self.provider:
            raise ValueError("STT provider not initialized")

        if not self.provider.supports_format(format):
            raise ValueError(f"Unsupported audio format: {format}")

        return await self.provider.recognize(audio_data, format, language, **kwargs)

    def supports_format(self, format: str) -> bool:
        """检查是否支持指定的音频格式"""
        if not self.provider:
            return False
        return self.provider.supports_format(format)
