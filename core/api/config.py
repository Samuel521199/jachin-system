"""
配置 API - 供 Horizon 等组件显示环境、模型等信息
"""

from fastapi import APIRouter
from typing import Optional
from pydantic import BaseModel
from core.config import settings
from core.config.api_key_override import set_qwen_api_key_override, clear_cache

router = APIRouter(prefix="/api/v3/config", tags=["config"])


class ApiKeySaveRequest(BaseModel):
    """API Key 保存请求"""
    qwen_api_key: Optional[str] = None


@router.post("/apikey")
async def save_api_key(req: ApiKeySaveRequest):
    """
    保存 API Key（桌面端持久化，覆盖 .env）
    保存后立即生效，无需重启后端。
    """
    ok = set_qwen_api_key_override(req.qwen_api_key)
    clear_cache()
    # 清除 LLM provider 缓存，使新 key 生效
    try:
        from core.brain.llm.factory import LLMProviderFactory
        LLMProviderFactory.clear_cache()
    except Exception:
        pass
    return {"ok": ok, "message": "已保存" if ok else "保存失败"}


class ConfigResponse(BaseModel):
    """配置响应"""
    environment: str
    model_name: str
    cluster_mode: Optional[str] = None
    llm_provider: Optional[str] = None


@router.get("", response_model=ConfigResponse)
async def get_config():
    """
    获取运行环境与模型配置

    Returns:
        environment: 环境标识；model_name: 当前模型；cluster_mode: 集群模式
    """
    env = (
        getattr(settings, "ENVIRONMENT", None)
        or (f"{settings.CLUSTER_MODE} mode" if settings.CLUSTER_MODE else "single")
    )
    return ConfigResponse(
        environment=env,
        model_name=settings.LLM_MODEL,
        cluster_mode=getattr(settings, "CLUSTER_MODE", None),
        llm_provider=settings.LLM_PROVIDER,
    )
