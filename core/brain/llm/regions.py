"""
Region Configuration - 地域配置

支持不同地域的API端点配置
"""

from enum import Enum
from typing import Dict, Optional
from dataclasses import dataclass


class Region(str, Enum):
    """支持的地域"""
    CN_BEIJING = "cn-beijing"  # 华北2（北京）
    AP_SINGAPORE = "ap-singapore"  # 新加坡
    US_VIRGINIA = "us-virginia"  # 美国（弗吉尼亚）


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
