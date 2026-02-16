"""
配置 API - 供 Horizon 等组件显示环境、模型等信息
"""

from fastapi import APIRouter
from typing import Optional
from pydantic import BaseModel
from core.config import settings

router = APIRouter(prefix="/api/v3/config", tags=["config"])


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
