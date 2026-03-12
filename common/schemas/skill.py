"""
Skill Schema - 技能生命周期与缓存相关数据模型

对应 ARCHITECTURE_DESIGN_SPEC §3.1 Hybrid Lifecycle、§3.2 Intelligent Caching
"""

from enum import Enum
from typing import Optional, Dict, Any, List

from pydantic import BaseModel, Field, ConfigDict


class DeploymentStrategy(str, Enum):
    """
    部署策略 (Hybrid Lifecycle Management)
    
    - ephemeral: 即时模式，RAM 加载 -> 执行 -> 立即销毁，零磁盘占用
    - cached: 缓存模式，Hash 校验 -> 缺失则拉取 -> 磁盘缓存 -> 随用随开
    - resident: 常驻模式，安装后长期驻留后台，Keep-Alive / 休眠唤醒
    """
    EPHEMERAL = "ephemeral"
    CACHED = "cached"
    RESIDENT = "resident"


class SkillAssetsManifest(BaseModel):
    """
    技能资产清单 (Intelligent Caching)
    
    技能包分为 Logic (轻，代码) 和 Assets (重，模型/素材)。
    Logic 每次校验 Hash；Assets 仅在 Hash 不匹配时下载。
    """
    logic_hash: str = Field(description="Logic 包 SHA256，KB 级，每次校验")
    assets_hash: Optional[str] = Field(default=None, description="Assets 包 SHA256，增量更新时校验")
    assets_size_bytes: Optional[int] = Field(default=None, description="Assets 总大小（字节）")
    logic_paths: List[str] = Field(default_factory=list, description="Logic 文件路径列表")
    assets_paths: List[str] = Field(default_factory=list, description="Assets 文件路径列表")


# 供 manifest.py 导入
__all__ = ["DeploymentStrategy", "SkillAssetsManifest"]
