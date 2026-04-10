"""
Voice API - 语音相关接口

提供语音识别、语音合成和语音聊天功能
"""
# 尽早加载 .env，确保 TTS_PROVIDER 被读取（L2 可能从不同目录启动）
try:
    from dotenv import load_dotenv
    from pathlib import Path as _P
    for _d in [_P(__file__).resolve().parent.parent.parent, _P.cwd()]:
        _env = _d / ".env"
        if _env.exists():
            load_dotenv(_env, encoding="utf-8")
            break
except ImportError:
    pass

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import logging
import os
import base64

from core.voice import SpeechToText, STTProvider, TextToSpeech, TTSProvider, IntentRouter, is_tts_globally_enabled
from core.brain.llm.factory import LLMProviderFactory
from core.brain.llm.call_types import AudioInput
from core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/voice", tags=["voice"])

# 初始化语音服务
# 默认使用 Whisper（免费，开源），如果需要使用阿里云，可以改为 STTProvider.ALIYUN
stt_provider = STTProvider.OPENAI_WHISPER  # 默认使用 Whisper（免费）
# TTS_PROVIDER：优先 env（确保 .env 生效），再 settings
_prov = (os.environ.get("TTS_PROVIDER") or getattr(settings, "TTS_PROVIDER", None) or "edge_tts").lower().strip()
tts_provider = TTSProvider.ALIYUN if _prov == "aliyun" else TTSProvider.EDGE_TTS
logger.info("[Voice] TTS_PROVIDER=%s (env=%s)", tts_provider, os.environ.get("TTS_PROVIDER", ""))

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
    _tts_on = is_tts_globally_enabled(source="voice")
    logger.info(
        "Initialized TTS provider: %s (globally enabled=%s)",
        tts_provider,
        _tts_on,
    )
    if tts and not _tts_on:
        logger.warning(
            "TTS 已加载但全局开关为关闭：POST /api/v2/voice/synthesize 与流式合成将返回 503。"
            " 请在环境变量设置 JACHIN_TTS_ENABLED=true（或 TTS_ENABLED=true），"
            "或在 ~/.jachin/nexus_config.json 设置 tts_enabled: true。详见项目根 .env.example 语音节。"
        )
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

# Commander Agent（智能意图路由 + Tool Calling，优先用于语音闭环）
try:
    from core.brain.commander import get_commander_agent
    commander_agent = get_commander_agent()
    if commander_agent:
        logger.info("Commander Agent 已就绪，将用于语音意图路由与工具调度")
    else:
        commander_agent = None
except Exception as e:
    logger.warning(f"Commander Agent 未就绪: {e}，将使用传统 LLM 闲聊")
    commander_agent = None

# 语义路由器（安全指令协议）
intent_router = IntentRouter()


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


class IntentRouteRequest(BaseModel):
    """语义路由请求（安全指令协议）"""
    text: str = Field(..., description="用户输入文本")


class IntentRouteResponse(BaseModel):
    """语义路由响应"""
    intent_type: str = Field(..., description="CHAT | COMMAND")
    risk_level: str = Field(..., description="low | medium | high")
    stripped_text: str = Field(..., description="去掉命令前缀后的文本")


class IntentRouteRequest(BaseModel):
    """语义路由请求（安全指令协议）"""
    text: str = Field(..., description="用户输入文本")


class IntentRouteResponse(BaseModel):
    """语义路由响应"""
    intent_type: str = Field(..., description="CHAT | COMMAND")
    risk_level: str = Field(..., description="low | medium | high")
    stripped_text: str = Field(..., description="去掉命令前缀后的文本")


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


@router.post("/intent", response_model=IntentRouteResponse)
async def route_intent(request: IntentRouteRequest):
    """
    语义路由：将用户文本分类为 CHAT（闲聊）或 COMMAND（系统指令）。
    前端可根据 intent_type 切换 UI（如 Alert Mode），根据 risk_level 决定是否二次确认。
    """
    routed = intent_router.route(request.text)
    return IntentRouteResponse(
        intent_type=routed.intent_type,
        risk_level=routed.risk_level,
        stripped_text=routed.stripped_text,
    )


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
    if not is_tts_globally_enabled(source="voice"):
        raise HTTPException(
            status_code=503,
            detail="TTS is disabled. Set JACHIN_TTS_ENABLED=true or TTS_ENABLED=true, or tts_enabled in ~/.jachin/nexus_config.json.",
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
        if not audio_data:
            raise HTTPException(
                status_code=503,
                detail="TTS returned empty audio (timeout, network error, or text invalid).",
            )
        # 返回音频数据
        return Response(
            content=audio_data,
            media_type="audio/wav",
            headers={
                "Content-Disposition": "attachment; filename=speech.wav"
            }
        )
    
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        err_msg = str(e)
        if "No audio" in err_msg or "NoAudioReceived" in type(e).__name__:
            raise HTTPException(
                status_code=503,
                detail="TTS service temporarily unavailable. Please try again or check network connectivity."
            )
        logger.error(f"Error in speech synthesis: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Speech synthesis failed: {err_msg}")


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
    if not is_tts_globally_enabled(source="voice"):
        raise HTTPException(
            status_code=503,
            detail="TTS is disabled. Set JACHIN_TTS_ENABLED=true or TTS_ENABLED=true, or tts_enabled in ~/.jachin/nexus_config.json.",
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


async def _mock_tts(text: str) -> str:
    """TTS 占位：当真实 TTS 不可用时返回空 base64"""
    return ""


@router.post("/process", response_model=VoiceChatResponse)
async def voice_process(
    audio_file: Optional[UploadFile] = File(None),
    audio_base64: Optional[str] = Form(None),
    format: str = Form("wav"),
    language: str = Form("zh-CN"),
    return_audio: bool = Form(True),
    voice: str = Form("default"),
    speed: float = Form(1.0),
    pitch: float = Form(1.0),
):
    """
    全链路语音闭环：STT -> Commander 意图路由与工具调度 -> TTS

    与 /chat 的区别：强制使用 Commander Agent（Function Calling），
    可调度沙箱插件（如时间、天气）并生成拟人化回复。
    """
    if not stt:
        raise HTTPException(status_code=503, detail="Speech-to-Text service is not available.")
    if not commander_agent:
        raise HTTPException(
            status_code=503,
            detail="Commander Agent is not available. Install openai: pip install openai, and configure LLM.",
        )

    try:
        audio_data = None
        if audio_file:
            audio_data = await audio_file.read()
            format = format or (audio_file.filename.split(".")[-1] if audio_file.filename else "wav")
        elif audio_base64:
            audio_data = base64.b64decode(audio_base64)
        else:
            raise HTTPException(status_code=400, detail="Either audio_file or audio_base64 must be provided")
        if not audio_data:
            raise HTTPException(status_code=400, detail="Audio data is empty")

        user_text = await stt.recognize(audio_data, format=format, language=language)
        logger.info(f"[Process] Recognized: {user_text}")

        reply_text = await commander_agent.process_request(user_text)
        logger.info(f"[Process] Reply: {reply_text}")

        audio_base64_result = None
        audio_format_result = None
        if return_audio and tts and is_tts_globally_enabled(source="voice"):
            try:
                reply_audio = await tts.synthesize(
                    text=reply_text, voice=voice, language=language, speed=speed, pitch=pitch
                )
                if reply_audio:
                    audio_base64_result = base64.b64encode(reply_audio).decode("utf-8")
                    audio_format_result = "wav"
            except Exception as e:
                logger.warning(f"TTS failed: {e}")
        elif return_audio and not tts:
            audio_base64_result = (await _mock_tts(reply_text)) or None

        return VoiceChatResponse(
            user_text=user_text,
            text=reply_text,
            audio_base64=audio_base64_result,
            audio_format=audio_format_result,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Voice process error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


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

        # 3. 生成回复：优先 Commander（Tool Calling），否则 LLM 闲聊
        reply_text = ""
        if commander_agent:
            try:
                reply_text = await commander_agent.process_request(user_text)
            except Exception as e:
                logger.warning(f"Commander fallback to LLM: {e}")

        if not reply_text and llm_provider:
            messages = [{"role": "user", "content": user_text}]
            if personality_id:
                try:
                    from core.brain.llm.personality import get_personality_manager
                    personality_manager = get_personality_manager()
                    system_prompt = personality_manager.get_system_message(personality_id)
                    if system_prompt:
                        messages.insert(0, {"role": "system", "content": system_prompt})
                    personality = personality_manager.get_personality(personality_id)
                    reply_text = await llm_provider.chat(
                        messages,
                        temperature=personality.temperature,
                        max_tokens=personality.max_tokens
                    )
                except Exception as e:
                    logger.warning(f"Failed to apply personality {personality_id}: {e}")
                    reply_text = await llm_provider.chat(messages)
            else:
                reply_text = await llm_provider.chat(messages)

        if not reply_text:
            reply_text = "主人，我在呢。有什么可以帮你的吗？"
        
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
        
        if return_audio and tts and is_tts_globally_enabled(source="voice"):
            try:
                reply_audio = await tts.synthesize(
                    text=reply_text,
                    voice=voice,
                    language=language,
                    speed=speed,
                    pitch=pitch
                )
                if reply_audio:
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
    if not is_tts_globally_enabled(source="voice"):
        return {"voices": []}
    
    try:
        voices = await tts.list_voices(language=language)
        return {"voices": voices}
    except Exception as e:
        logger.error(f"Error listing voices: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
