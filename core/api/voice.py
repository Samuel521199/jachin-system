"""
Voice API - 语音相关接口

提供语音识别、语音合成和语音聊天功能
"""

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import logging
import base64

from core.voice import SpeechToText, STTProvider, TextToSpeech, TTSProvider
from core.brain.llm.factory import LLMProviderFactory
from core.brain.llm.call_types import AudioInput
from core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/voice", tags=["voice"])

# 初始化语音服务
# 默认使用 Whisper（免费，开源），如果需要使用阿里云，可以改为 STTProvider.ALIYUN
stt_provider = STTProvider.OPENAI_WHISPER  # 默认使用 Whisper（免费）
tts_provider = TTSProvider.EDGE_TTS  # 默认使用 Edge TTS（免费）

try:
    # 如果使用 Whisper，传递自定义模型路径
    stt_kwargs = {}
    if stt_provider == STTProvider.OPENAI_WHISPER and settings.WHISPER_MODEL_PATH:
        stt_kwargs['model_path'] = settings.WHISPER_MODEL_PATH
        logger.info(f"Using custom Whisper model path: {settings.WHISPER_MODEL_PATH}")
    
    stt = SpeechToText(provider=stt_provider, **stt_kwargs)
    # 检查provider是否可用
    if stt.provider and hasattr(stt.provider, 'available'):
        if not stt.provider.available:
            logger.error(f"STT provider {stt_provider} is not available. Please install required dependencies.")
            if stt_provider == STTProvider.OPENAI_WHISPER:
                logger.error("Install Whisper with: pip install openai-whisper")
                logger.error("Note: Whisper also requires ffmpeg. Install with: choco install ffmpeg (Windows)")
            stt = None
        else:
            logger.info(f"Initialized STT provider: {stt_provider} (available)")
    else:
        logger.info(f"Initialized STT provider: {stt_provider}")
except Exception as e:
    logger.error(f"Failed to initialize STT provider: {e}", exc_info=True)
    stt = None

try:
    tts = TextToSpeech(provider=tts_provider)
    logger.info(f"Initialized TTS provider: {tts_provider}")
except Exception as e:
    logger.error(f"Failed to initialize TTS provider: {e}")
    tts = None

# 初始化 LLM provider
try:
    llm_provider = LLMProviderFactory.create_provider(
        provider_type=settings.LLM_PROVIDER,
        model=settings.LLM_MODEL
    )
    logger.info(f"Initialized LLM provider for voice chat: {settings.LLM_PROVIDER}")
except Exception as e:
    logger.error(f"Failed to initialize LLM provider: {e}")
    llm_provider = None


class RecognizeRequest(BaseModel):
    """语音识别请求"""
    audio_base64: Optional[str] = None
    format: str = Field(default="wav", description="音频格式（wav, mp3, m4a等）")
    language: str = Field(default="zh-CN", description="语言代码")


class RecognizeResponse(BaseModel):
    """语音识别响应"""
    text: str
    language: str
    confidence: Optional[float] = None


class SynthesizeRequest(BaseModel):
    """语音合成请求"""
    text: str
    voice: str = Field(default="default", description="语音名称/ID")
    language: str = Field(default="zh-CN", description="语言代码")
    speed: float = Field(default=1.0, ge=0.5, le=2.0, description="语速")
    pitch: float = Field(default=1.0, ge=0.5, le=2.0, description="音调")


class PhonemizeRequest(BaseModel):
    """文本转音素请求（Split-Inference 供 Tier 3 Kokoro 使用）"""
    text: str
    style: str = Field(default="zm", description="语音风格，如 zm 用于中英混合")


class VoiceChatRequest(BaseModel):
    """语音聊天请求"""
    audio_base64: Optional[str] = None
    format: str = Field(default="wav", description="音频格式")
    language: str = Field(default="zh-CN", description="语言代码")
    return_audio: bool = Field(default=True, description="是否返回语音回复")
    voice: str = Field(default="default", description="TTS语音名称")
    speed: float = Field(default=1.0, ge=0.5, le=2.0, description="TTS语速")
    pitch: float = Field(default=1.0, ge=0.5, le=2.0, description="TTS音调")


class VoiceChatResponse(BaseModel):
    """语音聊天响应"""
    user_text: str = Field(description="用户识别的文本")
    text: str = Field(description="AI的回复文本")
    audio_base64: Optional[str] = None
    audio_format: Optional[str] = None


@router.post("/recognize", response_model=RecognizeResponse)
async def recognize_audio(
    audio_file: Optional[UploadFile] = File(None),
    audio_base64: Optional[str] = Form(None),
    format: str = Form("wav"),
    language: str = Form("zh-CN")
):
    """
    语音识别接口
    
    支持两种输入方式：
    1. 上传音频文件（multipart/form-data）
    2. Base64编码的音频数据（form-data）
    """
    if not stt:
        error_detail = "Speech-to-Text service is not available."
        if stt_provider == STTProvider.OPENAI_WHISPER:
            error_detail += " Please install Whisper: pip install openai-whisper (also requires ffmpeg)"
        raise HTTPException(
            status_code=503,
            detail=error_detail
        )
    
    try:
        # 获取音频数据
        audio_data = None
        
        if audio_file:
            audio_data = await audio_file.read()
            format = format or audio_file.filename.split('.')[-1] if audio_file.filename else "wav"
        elif audio_base64:
            audio_data = base64.b64decode(audio_base64)
        else:
            raise HTTPException(status_code=400, detail="Either audio_file or audio_base64 must be provided")
        
        if not audio_data:
            raise HTTPException(status_code=400, detail="Audio data is empty")
        
        # 识别语音
        text = await stt.recognize(audio_data, format=format, language=language)
        
        return RecognizeResponse(
            text=text,
            language=language
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in voice recognition: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/synthesize")
async def synthesize_speech(request: SynthesizeRequest):
    """
    语音合成接口
    
    将文本合成为语音，返回音频数据
    """
    if not tts:
        raise HTTPException(
            status_code=503,
            detail="Text-to-Speech service is not available. Please check configuration."
        )
    
    try:
        # 合成语音
        audio_data = await tts.synthesize(
            text=request.text,
            voice=request.voice,
            language=request.language,
            speed=request.speed,
            pitch=request.pitch
        )
        
        # 返回音频数据
        return Response(
            content=audio_data,
            media_type="audio/wav",
            headers={
                "Content-Disposition": "attachment; filename=speech.wav"
            }
        )
    
    except Exception as e:
        logger.error(f"Error in speech synthesis: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/synthesize-stream")
async def synthesize_speech_stream(request: SynthesizeRequest):
    """
    流式语音合成接口
    
    将文本流式合成为语音，实时返回音频数据块
    """
    if not tts:
        raise HTTPException(
            status_code=503,
            detail="Text-to-Speech service is not available. Please check configuration."
        )
    
    try:
        async def generate_audio():
            async for chunk in tts.synthesize_stream(
                text=request.text,
                voice=request.voice,
                language=request.language,
                speed=request.speed,
                pitch=request.pitch
            ):
                yield chunk
        
        return StreamingResponse(
            generate_audio(),
            media_type="audio/wav",
            headers={
                "Content-Disposition": "attachment; filename=speech.wav"
            }
        )
    
    except Exception as e:
        logger.error(f"Error in streaming speech synthesis: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/chat", response_model=VoiceChatResponse)
async def voice_chat(
    audio_file: Optional[UploadFile] = File(None),
    audio_base64: Optional[str] = Form(None),
    format: str = Form("wav"),
    language: str = Form("zh-CN"),
    return_audio: bool = Form(True),
    voice: str = Form("default"),
    speed: float = Form(1.0),
    pitch: float = Form(1.0),
    personality_id: Optional[str] = Form(None, description="AI助手人格ID")
):
    """
    语音聊天接口
    
    完整的语音对话流程：
    1. 接收语音输入
    2. 识别为文本（STT）
    3. 调用 LLM 生成回复
    4. 合成语音回复（TTS，可选）
    5. 返回文本和音频
    """
    if not stt:
        raise HTTPException(
            status_code=503,
            detail="Speech-to-Text service is not available."
        )
    
    if not llm_provider:
        raise HTTPException(
            status_code=503,
            detail="LLM provider is not available."
        )
    
    try:
        # 1. 获取音频数据
        audio_data = None
        
        if audio_file:
            audio_data = await audio_file.read()
            format = format or audio_file.filename.split('.')[-1] if audio_file.filename else "wav"
        elif audio_base64:
            audio_data = base64.b64decode(audio_base64)
        else:
            raise HTTPException(status_code=400, detail="Either audio_file or audio_base64 must be provided")
        
        if not audio_data:
            raise HTTPException(status_code=400, detail="Audio data is empty")
        
        # 2. 语音识别（STT）
        user_text = await stt.recognize(audio_data, format=format, language=language)
        logger.info(f"Recognized text: {user_text}")
        
        # 3. 调用 LLM 生成回复（支持人格配置）
        messages = [{"role": "user", "content": user_text}]
        
        # 如果指定了人格，添加系统提示词
        if personality_id:
            try:
                from core.brain.llm.personality import get_personality_manager
                personality_manager = get_personality_manager()
                system_prompt = personality_manager.get_system_message(personality_id)
                if system_prompt:
                    messages.insert(0, {"role": "system", "content": system_prompt})
                
                # 使用人格的temperature和max_tokens
                personality = personality_manager.get_personality(personality_id)
                reply_text = await llm_provider.chat(
                    messages,
                    temperature=personality.temperature,
                    max_tokens=personality.max_tokens
                )
            except Exception as e:
                logger.warning(f"Failed to apply personality {personality_id}: {e}, using default")
                reply_text = await llm_provider.chat(messages)
        else:
            reply_text = await llm_provider.chat(messages)
        
        logger.info(f"Generated reply: {reply_text}")
        
        # 更新上下文 Token 估算
        try:
            from core.api.console import update_context_used
            from core.utils.token_count import count_messages_tokens, count_tokens
            input_tokens = count_messages_tokens(messages)
            output_tokens = count_tokens(reply_text)
            update_context_used(input_tokens + output_tokens)
        except Exception:
            pass
        
        # 4. 语音合成（TTS，如果请求）
        audio_base64_result = None
        audio_format_result = None
        
        if return_audio and tts:
            try:
                reply_audio = await tts.synthesize(
                    text=reply_text,
                    voice=voice,
                    language=language,
                    speed=speed,
                    pitch=pitch
                )
                audio_base64_result = base64.b64encode(reply_audio).decode('utf-8')
                audio_format_result = "wav"
            except Exception as e:
                logger.warning(f"Failed to synthesize speech: {e}")
                # 即使TTS失败，也返回文本回复
        
        return VoiceChatResponse(
            user_text=user_text,
            text=reply_text,
            audio_base64=audio_base64_result,
            audio_format=audio_format_result
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in voice chat: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/phonemize")
async def phonemize_text(request: PhonemizeRequest):
    """
    文本转音素（Split-Inference）
    
    供 Tier 3 本地 Kokoro ONNX 使用：Tier 2 处理音素，Tier 3 仅负责 ONNX 推理。
    需安装 misaki: pip install "misaki[zh]" 或 pip install "misaki[en]"
    返回 phonemes（IPA 字符列表）和 tokens（Kokoro token IDs，若可用）
    """
    def _run_g2p(g2p):
        phonemes, tokens = g2p(request.text)
        if isinstance(phonemes, str):
            phoneme_list = [c for c in phonemes]
        else:
            phoneme_list = [str(p) for p in phonemes]
        return {
            "phonemes": phoneme_list,
            "tokens": list(tokens) if tokens is not None else [],
        }

    try:
        try:
            from misaki import z  # z = Chinese
            g2p = z.G2P()
            return _run_g2p(g2p)
        except ImportError:
            from misaki import en
            g2p = en.G2P(trf=False, british=False, fallback=None)
            return _run_g2p(g2p)
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="Phonemize requires misaki. Install with: pip install 'misaki[zh]' or 'misaki[en]'"
        )
    except Exception as e:
        logger.error(f"Phonemize error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/voices")
async def list_voices(language: Optional[str] = None):
    """
    列出可用的语音列表
    
    Args:
        language: 语言代码（可选，如 zh-CN）
    """
    if not tts:
        raise HTTPException(
            status_code=503,
            detail="Text-to-Speech service is not available."
        )
    
    try:
        voices = await tts.list_voices(language=language)
        return {"voices": voices}
    except Exception as e:
        logger.error(f"Error listing voices: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
