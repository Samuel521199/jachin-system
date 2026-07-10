"""
Chat API V2 - 增强版聊天接口

支持多模态、工具调用、联网搜索等功能
"""

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Union
import logging
import base64

from core.brain.llm.factory import LLMProviderFactory
from core.brain.llm.call_types import (
    CallType, ImageInput, VideoInput, AudioInput,
    DocumentInput, WebSearchConfig, ToolCall, CallOptions
)
from core.brain.llm.regions import effective_qwen_region_from_env
from core.brain.llm.personality import get_personality_manager
from core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/chat", tags=["chat-v2"])


@router.get("/personalities")
async def list_personalities():
    """
    列出所有可用的AI助手人格

    Returns:
        人格列表，包含ID、名称、描述等信息
    """
    personality_manager = get_personality_manager()
    personalities = personality_manager.list_personalities()

    return {
        "personalities": personalities,
        "default": personality_manager.default_personality,
        "total": len(personalities)
    }


@router.get("/llm-status")
async def llm_status():
    """
    检查当前 LLM（本地/云端）是否可用，供前端决定是否使用流式直连等。
    Returns:
        available: 是否可用
        provider: 类型，如 local / qwen
        model: 当前模型名
    """
    provider = await _resolve_provider()
    if not provider:
        return {"available": False, "provider": None, "model": None}
    try:
        healthy = await provider.health_check()
        info = provider.get_model_info() or {}
        return {
            "available": bool(healthy),
            "provider": info.get("provider") or getattr(provider, "model", ""),
            "model": info.get("model") or getattr(provider, "model", ""),
        }
    except Exception as e:
        logger.debug("llm_status check failed: %s", e)
        return {"available": False, "provider": None, "model": None}


class ChatMessage(BaseModel):
    """聊天消息模型"""
    role: str = Field(..., description="角色: user, assistant, system")
    content: Union[str, List[Dict[str, Any]]] = Field(..., description="消息内容（文本或多模态）")


class TextChatRequest(BaseModel):
    """文本聊天请求"""
    messages: List[ChatMessage]
    model: Optional[str] = None
    temperature: Optional[float] = Field(None, ge=0, le=2)
    max_tokens: Optional[int] = Field(None, gt=0)
    stream: bool = False
    personality_id: Optional[str] = Field(None, description="AI助手人格ID，如：default, tech_expert, life_assistant等")


class ImageChatRequest(BaseModel):
    """图像聊天请求"""
    messages: List[ChatMessage]
    image_urls: Optional[List[str]] = None
    model: Optional[str] = None
    temperature: Optional[float] = Field(None, ge=0, le=2)
    max_tokens: Optional[int] = Field(None, gt=0)
    stream: bool = False


class WebSearchChatRequest(BaseModel):
    """联网搜索聊天请求"""
    messages: List[ChatMessage]
    enable_search: bool = True
    max_results: int = Field(5, ge=1, le=10)
    model: Optional[str] = None
    temperature: Optional[float] = Field(None, ge=0, le=2)
    max_tokens: Optional[int] = Field(None, gt=0)


class ToolCallRequest(BaseModel):
    """工具调用请求"""
    messages: List[ChatMessage]
    tools: List[Dict[str, Any]]
    model: Optional[str] = None
    temperature: Optional[float] = Field(None, ge=0, le=2)
    max_tokens: Optional[int] = Field(None, gt=0)


class ChatResponse(BaseModel):
    """聊天响应"""
    content: str
    model: str
    usage: Optional[Dict[str, Any]] = None
    finish_reason: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None


# Initialize LLM provider
llm_provider = None
_qwen_fallback_provider = None  # GPU 过热时回退到云端
_current_provider_model: str = ""  # 当前 provider 对应的模型，用于热切换检测

try:
    # 解析地域配置（JACHIN_ACTIVE_REGION=SEA 时与 DashScope 国际 endpoint 对齐，除非显式设置 QWEN_REGION）
    region = effective_qwen_region_from_env()

    llm_provider = LLMProviderFactory.create_provider(
        provider_type=settings.LLM_PROVIDER,
        model=settings.LLM_MODEL,
        region=region
    )
    _current_provider_model = settings.LLM_MODEL or "qwen-turbo"
    logger.info(f"Initialized LLM provider: {settings.LLM_PROVIDER} model={_current_provider_model}")
    # 若主用 local，预创建 qwen 作为 GPU 过热时的云端回退
    if getattr(settings, "LLM_PROVIDER", "").lower() == "local":
        try:
            if settings.QWEN_API_KEY or settings.DASHSCOPE_API_KEY:
                _qwen_fallback_provider = LLMProviderFactory.create_provider(
                    provider_type="qwen",
                    model=getattr(settings, "LLM_MODEL", "qwen-turbo") or "qwen-turbo",
                    region=region
                )
                logger.info("Qwen fallback provider ready for GPU overheating diversion")
        except Exception as e:
            logger.debug("Qwen fallback not available: %s", e)
except Exception as e:
    logger.error(f"Failed to initialize LLM provider: {e}")
    llm_provider = None


def _get_effective_provider(provider=None):
    """GPU 过热时，若主用 local 则回退到云端。传入 provider 时基于其判断。"""
    p = provider or llm_provider
    if not p:
        return None
    try:
        from core.utils.gpu_status import is_gpu_overheated
        from core.brain.llm.local_adapter import LocalAdapter
        if _qwen_fallback_provider and isinstance(p, LocalAdapter) and is_gpu_overheated():
            logger.info("GPU overheated, diverting to cloud provider")
            return _qwen_fallback_provider
    except Exception as e:
        logger.debug("GPU diversion check failed: %s", e)
    return p


async def _resolve_provider():
    """解析当前应使用的 provider：支持模型热切换（StateStore console/current_model）"""
    global llm_provider, _current_provider_model
    if not llm_provider:
        return None
    try:
        # v5.0: Dapr StateStore 已废弃
        class _Store:
            async def get(self, k): return None
        store = _Store()
        current_model = await store.get("console/current_model")
        if not current_model:
            current_model = getattr(settings, "LLM_MODEL", None) or "qwen-turbo"
        if current_model != _current_provider_model:
            try:
                region = effective_qwen_region_from_env()
                new_provider = LLMProviderFactory.create_provider(
                    provider_type=settings.LLM_PROVIDER,
                    model=current_model,
                    region=region,
                )
                llm_provider = new_provider
                _current_provider_model = current_model
                logger.info("Model hot-switched to %s", current_model)
            except Exception as e:
                logger.warning("Model hot-switch failed, keeping current: %s", e)
    except Exception as e:
        logger.debug("Resolve provider (current_model check) failed: %s", e)
    return _get_effective_provider(llm_provider)


@router.post("/text", response_model=ChatResponse)
async def chat_text(request: TextChatRequest):
    """
    文本聊天接口

    支持同步和流式输出
    """
    provider = await _resolve_provider()
    if not provider:
        raise HTTPException(
            status_code=503,
            detail="LLM provider is not available. Please check configuration."
        )

    try:
        # 获取人格管理器
        personality_manager = get_personality_manager()

        # 转换消息格式
        messages = [
            {"role": msg.role, "content": msg.content}
            for msg in request.messages
        ]

        # 检查是否已有system消息，如果没有则添加人格的系统提示词
        has_system_message = any(msg.get("role") == "system" for msg in messages)
        if not has_system_message and request.personality_id:
            system_prompt = personality_manager.get_system_message(request.personality_id)
            if system_prompt:
                messages.insert(0, {"role": "system", "content": system_prompt})

        # 准备参数（优先使用人格配置，然后使用请求参数）
        kwargs = {}
        if request.personality_id:
            personality = personality_manager.get_personality(request.personality_id)
            kwargs["temperature"] = request.temperature if request.temperature is not None else personality.temperature
            kwargs["max_tokens"] = request.max_tokens if request.max_tokens is not None else personality.max_tokens
        else:
            if request.temperature is not None:
                kwargs["temperature"] = request.temperature
            if request.max_tokens is not None:
                kwargs["max_tokens"] = request.max_tokens

        # 调用LLM
        if request.stream:
            # 流式响应
            async def generate():
                async for chunk in provider.stream_chat(messages, **kwargs):
                    yield f"data: {chunk}\n\n"
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                generate(),
                media_type="text/event-stream; charset=utf-8",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        else:
            # 同步响应
            response_text = await provider.chat(messages, **kwargs)
            model_info = provider.get_model_info()
            # 更新上下文 Token 统计（供 ModelController）
            try:
                from core.api.console import update_context_used
                from core.utils.token_count import count_messages_tokens, count_tokens
                input_tokens = count_messages_tokens(messages)
                output_tokens = count_tokens(response_text)
                update_context_used(input_tokens + output_tokens)
            except Exception:
                pass
            return ChatResponse(
                content=response_text,
                model=model_info.get("model", request.model or settings.LLM_MODEL),
                usage=None,
            )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error in text chat endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/image", response_model=ChatResponse)
async def chat_image(
    request: ImageChatRequest,
    image_files: Optional[List[UploadFile]] = None
):
    """
    图像聊天接口

    支持通过URL或文件上传图像
    """
    provider = await _resolve_provider()
    if not provider:
        raise HTTPException(
            status_code=503,
            detail="LLM provider is not available. Please check configuration."
        )

    if not provider.supports_call_type(CallType.IMAGE):
        raise HTTPException(
            status_code=400,
            detail=f"Model {provider.model} does not support image input"
        )

    try:
        # 准备图像输入
        images = []

        # 从URL添加图像
        if request.image_urls:
            for url in request.image_urls:
                images.append(ImageInput(image_url=url))

        # 从上传文件添加图像
        if image_files:
            for file in image_files:
                image_bytes = await file.read()
                images.append(ImageInput(image_bytes=image_bytes))

        if not images:
            raise HTTPException(
                status_code=400,
                detail="At least one image (URL or file) is required"
            )

        # 转换消息格式
        messages = [
            {"role": msg.role, "content": msg.content}
            for msg in request.messages
        ]

        # 准备参数
        kwargs = {}
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.max_tokens is not None:
            kwargs["max_tokens"] = request.max_tokens

        # 调用LLM
        if request.stream:
            async def generate():
                async for chunk in provider.stream_chat_with_image(messages, images, **kwargs):
                    yield f"data: {chunk}\n\n"
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                generate(),
                media_type="text/event-stream; charset=utf-8",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        else:
            response_text = await provider.chat_with_image(messages, images, **kwargs)
            model_info = provider.get_model_info()
            try:
                from core.api.console import update_context_used
                from core.utils.token_count import count_messages_tokens, count_tokens
                input_tokens = count_messages_tokens(messages)
                output_tokens = count_tokens(response_text)
                update_context_used(input_tokens + output_tokens)
            except Exception:
                pass
            return ChatResponse(
                content=response_text,
                model=model_info.get("model", request.model or settings.LLM_MODEL),
            )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error in image chat endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/web-search", response_model=ChatResponse)
async def chat_web_search(request: WebSearchChatRequest):
    """
    联网搜索聊天接口
    """
    provider = await _resolve_provider()
    if not provider:
        raise HTTPException(
            status_code=503,
            detail="LLM provider is not available. Please check configuration."
        )

    if not provider.supports_call_type(CallType.WEB_SEARCH):
        raise HTTPException(
            status_code=400,
            detail=f"Model {provider.model} does not support web search"
        )

    try:
        # 转换消息格式
        messages = [
            {"role": msg.role, "content": msg.content}
            for msg in request.messages
        ]

        # 配置联网搜索
        web_search_config = WebSearchConfig(
            enabled=request.enable_search,
            max_results=request.max_results
        )

        # 准备参数
        kwargs = {}
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.max_tokens is not None:
            kwargs["max_tokens"] = request.max_tokens

        # 调用LLM
        response_text = await provider.chat_with_web_search(
            messages, web_search_config, **kwargs
        )
        model_info = provider.get_model_info()
        try:
            from core.api.console import update_context_used
            from core.utils.token_count import count_messages_tokens, count_tokens
            input_tokens = count_messages_tokens(messages)
            output_tokens = count_tokens(response_text)
            update_context_used(input_tokens + output_tokens)
        except Exception:
            pass
        return ChatResponse(
            content=response_text,
            model=model_info.get("model", request.model or settings.LLM_MODEL),
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error in web search chat endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/tools", response_model=ChatResponse)
async def chat_tools(request: ToolCallRequest):
    """
    工具调用接口
    """
    provider = await _resolve_provider()
    if not provider:
        raise HTTPException(
            status_code=503,
            detail="LLM provider is not available. Please check configuration."
        )

    if not provider.supports_call_type(CallType.TOOL_CALL):
        raise HTTPException(
            status_code=400,
            detail=f"Model {provider.model} does not support tool calling"
        )

    try:
        # 转换消息格式
        messages = [
            {"role": msg.role, "content": msg.content}
            for msg in request.messages
        ]

        # 转换工具定义
        tools = [
            ToolCall(
                name=tool.get("name", ""),
                description=tool.get("description"),
                parameters=tool.get("parameters", {})
            )
            for tool in request.tools
        ]

        # 准备参数
        kwargs = {}
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.max_tokens is not None:
            kwargs["max_tokens"] = request.max_tokens

        # 调用LLM
        result = await provider.chat_with_tools(messages, tools, **kwargs)
        model_info = provider.get_model_info()
        content = result.get("content", "")
        try:
            from core.api.console import update_context_used
            from core.utils.token_count import count_messages_tokens, count_tokens
            input_tokens = count_messages_tokens(messages)
            output_tokens = count_tokens(content)
            update_context_used(input_tokens + output_tokens)
        except Exception:
            pass
        return ChatResponse(
            content=content,
            model=model_info.get("model", request.model or settings.LLM_MODEL),
            tool_calls=result.get("tool_calls"),
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error in tools chat endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/capabilities")
async def get_capabilities():
    """
    获取当前模型的能力信息
    """
    provider = await _resolve_provider()
    if not provider:
        raise HTTPException(
            status_code=503,
            detail="LLM provider is not available. Please check configuration."
        )

    model_info = provider.get_model_info()
    capabilities = model_info.get("capabilities", {})

    return {
        "model": model_info.get("model"),
        "provider": model_info.get("provider"),
        "region": model_info.get("region"),
        "capabilities": {
            "text": capabilities.get("text", False),
            "stream": capabilities.get("stream", False),
            "image": capabilities.get("image", False),
            "video": capabilities.get("video", False),
            "audio": capabilities.get("audio", False),
            "web_search": capabilities.get("web_search", False),
            "tool_call": capabilities.get("tool_call", False),
        }
    }
