"""
Region Configuration - 地域配置

支持不同地域的API端点配置
"""

import os
from enum import Enum
from typing import Dict, Optional
from dataclasses import dataclass


class Region(str, Enum):
    """支持的地域"""
    CN_BEIJING = "cn-beijing"  # 华北2（北京）
    AP_SINGAPORE = "ap-singapore"  # 新加坡
    US_VIRGINIA = "us-virginia"  # 美国（弗吉尼亚）
    SEA_INTL = "sea-intl"  # 东南亚/国际站兼容模式（dashscope-intl，与 JACHIN_ACTIVE_REGION=SEA 对齐）


@dataclass
class RegionConfig:
    """地域配置"""
    name: str
    base_url: str
    api_endpoint: str
    description: str


# 地域配置映射
REGION_CONFIGS: Dict[Region, RegionConfig] = {
    Region.CN_BEIJING: RegionConfig(
        name="华北2（北京）",
        base_url="https://dashscope.aliyuncs.com",
        api_endpoint="https://dashscope.aliyuncs.com/compatible-mode/v1",
        description="中国大陆地域，延迟最低"
    ),
    Region.AP_SINGAPORE: RegionConfig(
        name="新加坡",
        base_url="https://dashscope.ap-southeast-1.aliyuncs.com",
        api_endpoint="https://dashscope.ap-southeast-1.aliyuncs.com/compatible-mode/v1",
        description="亚太地域，适合海外用户"
    ),
    Region.US_VIRGINIA: RegionConfig(
        name="美国（弗吉尼亚）",
        base_url="https://dashscope.us-east-1.aliyuncs.com",
        api_endpoint="https://dashscope.us-east-1.aliyuncs.com/compatible-mode/v1",
        description="美国地域，适合北美用户"
    ),
    Region.SEA_INTL: RegionConfig(
        name="国际（东南亚常用）",
        base_url="https://dashscope-intl.aliyuncs.com",
        api_endpoint="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        description="国际站兼容模式，与 DASHSCOPE_API_BASE_SEA 默认一致"
    ),
}


def get_region_config(region: Region) -> RegionConfig:
    """
    获取地域配置

    Args:
        region: 地域枚举值

    Returns:
        RegionConfig 配置对象
    """
    return REGION_CONFIGS[region]


def get_default_region() -> Region:
    """获取默认地域（北京）"""
    return Region.CN_BEIJING


def effective_qwen_region_from_env() -> Region:
    """
    与 JACHIN_ACTIVE_REGION 对齐：显式 QWEN_REGION（环境变量优先）未设置且 ACTIVE=SEA → sea-intl；
    否则解析 QWEN_REGION / settings 或默认北京。
    """
    try:
        from core.brain.llm.dashscope_regional import get_jachin_active_region

        active = get_jachin_active_region()
    except ImportError:
        active = os.getenv("JACHIN_ACTIVE_REGION", "").strip().upper()
    raw_env = os.getenv("QWEN_REGION", "").strip()
    if active == "SEA" and not raw_env:
        return Region.SEA_INTL
    raw = raw_env
    if not raw:
        try:
            from core.config import settings

            raw = (getattr(settings, "QWEN_REGION", None) or "").strip()
        except Exception:
            raw = ""
    if raw:
        try:
            region_name = raw.upper().replace("-", "_")
            r = getattr(Region, region_name, None)
            if isinstance(r, Region):
                return r
        except Exception:
            pass
    if active == "SEA":
        return Region.SEA_INTL
    return Region.CN_BEIJING
