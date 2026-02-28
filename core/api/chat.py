"""
Chat API - 聊天接口

提供与 AI 模型对话的 API 端点。
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import logging

from core.brain.llm.factory import LLMProviderFactory
from core.config import settings, get_effective_qwen_api_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])

# 兼容简化版本的端点（用于 MVP 验证）
simple_router = APIRouter(prefix="/api", tags=["chat"])


class ChatMessage(BaseModel):
    """聊天消息模型"""
    role: str  # "user", "assistant", "system"
    content: str


class ChatRequest(BaseModel):
    """聊天请求模型"""
    messages: List[ChatMessage]
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    stream: bool = False


class ChatResponse(BaseModel):
    """聊天响应模型"""
    message: str
    model: str
    usage: Optional[Dict[str, Any]] = None


# Initialize LLM provider
try:
    # 准备 provider 参数
    provider_kwargs = {
        "model": settings.LLM_MODEL,
    }
    
    # 传递 API key（如果可用，优先用户保存的覆盖）
    api_key = get_effective_qwen_api_key()
    if api_key:
        provider_kwargs["api_key"] = api_key
    
    # 传递地域配置（如果使用 qwen-v2）
    if settings.LLM_PROVIDER == "qwen-v2" and hasattr(settings, "QWEN_REGION"):
        provider_kwargs["region"] = settings.QWEN_REGION
    
    llm_provider = LLMProviderFactory.create_provider(
        provider_type=settings.LLM_PROVIDER,
        **provider_kwargs
    )
    logger.info(f"Initialized LLM provider: {settings.LLM_PROVIDER} with model: {settings.LLM_MODEL}")
except Exception as e:
    logger.error(f"Failed to initialize LLM provider: {e}", exc_info=True)
    llm_provider = None


@router.post("/", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    聊天接口
    
    接收用户消息，调用 LLM 模型生成回复。
    """
    if not llm_provider:
        raise HTTPException(
            status_code=503,
            detail="LLM provider is not available. Please check configuration."
        )
    
    try:
        # Convert Pydantic models to dict format
        messages = [
            {"role": msg.role, "content": msg.content}
            for msg in request.messages
        ]
        
        # Prepare kwargs for LLM call
        kwargs = {}
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.max_tokens is not None:
            kwargs["max_tokens"] = request.max_tokens
        
        # Call LLM
        if request.stream:
            # Streaming response (future implementation)
            raise HTTPException(
                status_code=501,
                detail="Streaming is not yet implemented"
            )
        else:
            response_text = await llm_provider.chat(messages, **kwargs)
            model_info = llm_provider.get_model_info()
            # 更新上下文 Token 估算（供 ModelController）
            try:
                from core.api.console import update_context_used
                from core.utils.token_count import count_messages_tokens, count_tokens
                input_tokens = count_messages_tokens(messages)
                output_tokens = count_tokens(response_text)
                update_context_used(input_tokens + output_tokens)
            except Exception:
                pass
            return ChatResponse(
                message=response_text,
                model=model_info.get("model", settings.LLM_MODEL),
                usage=None
            )
    
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error in chat endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/health")
async def chat_health():
    """检查聊天服务健康状态"""
    if not llm_provider:
        # 提供详细的诊断信息
        env_vars = {
            "QWEN_AI_API_KEY": "Set" if settings.QWEN_AI_API_KEY else "Not set",
            "QWEN_API_KEY": "Set" if settings.QWEN_API_KEY else "Not set",
            "DASHSCOPE_API_KEY": "Set" if settings.DASHSCOPE_API_KEY else "Not set",
        }
        
        return {
            "status": "unavailable",
            "provider": settings.LLM_PROVIDER,
            "model": settings.LLM_MODEL,
            "error": "LLM provider not initialized",
            "diagnostics": {
                "settings_qwen_api_key": "Set" if settings.QWEN_API_KEY else "Not set",
                "environment_variables": env_vars,
                "config_llm_provider": settings.LLM_PROVIDER,
                "config_llm_model": settings.LLM_MODEL,
            }
        }
    
    try:
        health = await llm_provider.health_check()
        return {
            "status": "healthy" if health else "unhealthy",
            "provider": settings.LLM_PROVIDER,
            "model": llm_provider.get_model_info()
        }
    except Exception as e:
        return {
            "status": "error",
            "provider": settings.LLM_PROVIDER,
            "error": str(e)
        }


# 兼容简化版本的端点（用于 MVP 验证）
class SimpleChatRequest(BaseModel):
    """简化的聊天请求模型"""
    message: str


class SimpleChatResponse(BaseModel):
    """简化的聊天响应模型"""
    reply: str


@simple_router.post("/chat", response_model=SimpleChatResponse)
async def chat_simple(request: SimpleChatRequest):
    """
    简化的聊天接口（兼容 MVP 版本）
    
    接收格式: {"message": "你好"}
    返回格式: {"reply": "..."}
    """
    if not llm_provider:
        raise HTTPException(
            status_code=503,
            detail="LLM provider is not available. Please check configuration."
        )
    
    try:
        if not request.message:
            raise HTTPException(status_code=400, detail="'message' field is required")
        
        # 转换为标准消息格式
        messages = [{"role": "user", "content": request.message}]
        
        # 调用 LLM
        response_text = await llm_provider.chat(messages)
        # 更新上下文 Token 估算
        try:
            from core.api.console import update_context_used
            input_tokens = sum(len(str(m.get("content", ""))) for m in messages) // 3
            output_tokens = len(response_text) // 3
            update_context_used(input_tokens + output_tokens)
        except Exception:
            pass
        return SimpleChatResponse(reply=response_text)
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in simple chat endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
