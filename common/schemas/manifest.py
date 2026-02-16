"""
Plugin Manifest Schema - 插件清单数据模型
用于 .jsp 插件的元数据定义

注意：此文件仅包含数据模型定义（Pydantic Schemas）
严禁包含业务逻辑代码
"""

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, ConfigDict
from enum import Enum

from common.schemas.skill import DeploymentStrategy


class PriceType(str, Enum):
    """价格类型枚举"""
    FREE = "free"
    ONE_TIME = "one_time"
    SUBSCRIPTION_MONTHLY = "subscription_monthly"
    SUBSCRIPTION_YEARLY = "subscription_yearly"


class PriceInfo(BaseModel):
    """价格信息"""
    amount: float = Field(ge=0, description="价格金额")
    currency: str = Field(default="USD", description="货币单位")
    type: PriceType = Field(default=PriceType.FREE, description="价格类型")


class Permission(BaseModel):
    """权限定义"""
    scope: str = Field(description="权限范围，如 'internet.access', 'file.write'")


class RuntimeConfig(BaseModel):
    """运行时配置"""
    type: str = Field(default="ray", description="运行时类型：ray (Ray RuntimeEnv)")
    python_version: str = Field(default="3.10", description="Python 版本要求")
    resources: Dict[str, Any] = Field(default_factory=dict, description="资源需求")


class ComputeType(str, Enum):
    """算力需求类型（大小脑协同）"""
    CPU_LIGHT = "cpu_light"    # 轻量级，CPU 即可
    CPU_MEDIUM = "cpu_medium"  # 中等，CPU
    GPU_HEAVY = "gpu_heavy"    # 重型，需 GPU


class Capability(BaseModel):
    """能力定义"""
    name: str = Field(description="能力名称")
    description: Optional[str] = Field(default=None, description="能力描述")
    tag: Optional[str] = Field(default=None, description="能力标签，如 user.reach 供 Sentinel 筛选")
    compute: Optional[str] = Field(default="cpu_light", description="算力需求: cpu_light|cpu_medium|gpu_heavy")
    input_schema: Optional[Dict[str, Any]] = Field(default=None, description="输入参数Schema")
    output_schema: Optional[Dict[str, Any]] = Field(default=None, description="输出结果Schema")


class SkillManifest(BaseModel):
    """
    技能清单 (Skill Manifest)
    
    用于预装技能和本地技能的定义，与 PluginManifest 的区别：
    - SkillManifest: 用于系统预装技能（_bundled），不需要签名和价格
    - PluginManifest: 用于 .jsp 插件包，需要签名和价格信息
    """
    
    # 基本信息
    id: str = Field(description="技能唯一标识（反向域名格式），如 'com.jachin.os-mate'")
    version: str = Field(description="版本号（遵循语义化版本）")
    name: str = Field(description="技能显示名称")
    description: Optional[str] = Field(default=None, description="技能描述")
    author: Optional[str] = Field(default=None, description="开发者名称")
    
    # 能力定义
    capabilities: List[Capability] = Field(default_factory=list, description="能力列表")
    
    # 权限申请
    permissions: List[Permission] = Field(default_factory=list, description="权限申请列表")
    
    # 依赖与运行时
    requirements: List[str] = Field(default_factory=list, description="Python 依赖列表")
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig, description="运行时配置")
    
    # 部署策略 (Hybrid Lifecycle, ARCHITECTURE_DESIGN_SPEC §3.1)
    deployment_strategy: DeploymentStrategy = Field(
        default=DeploymentStrategy.CACHED,
        description="ephemeral=即时销毁 | cached=磁盘缓存 | resident=常驻后台",
    )
    
    model_config = ConfigDict(
        use_enum_values=True,
        json_schema_extra={
            "example": {
                "id": "com.jachin.os-mate",
                "version": "1.0.0",
                "name": "系统管家",
                "description": "系统管理功能：关机、重启、音量控制",
                "author": "Jachin Team",
                "capabilities": [
                    {
                        "name": "shutdown",
                        "description": "关闭系统",
                    },
                    {
                        "name": "reboot",
                        "description": "重启系统",
                    }
                ],
                "permissions": [
                    {"scope": "system.power"},
                    {"scope": "system.control"}
                ],
                "requirements": [],
                "deployment_strategy": "cached"
            }
        }
    )


class PluginManifest(BaseModel):
    """
    插件清单 (v3.2)
    
    这是 .jsp 插件包的元数据定义，用于：
    - Tier 1 Market: 插件审核、定价、分发
    - Tier 2 Plugin Manager: 插件加载、权限验证、运行时配置
    """
    
    # 基本信息
    id: str = Field(description="插件唯一标识（反向域名格式），如 'com.developer.deep-research'")
    version: str = Field(description="版本号（遵循语义化版本）")
    name: str = Field(description="插件显示名称")
    description: Optional[str] = Field(default=None, description="插件描述")
    author: Optional[str] = Field(default=None, description="开发者名称")
    author_email: Optional[str] = Field(default=None, description="开发者邮箱")
    
    # 价格信息
    price: PriceInfo = Field(default_factory=lambda: PriceInfo(amount=0.0, type=PriceType.FREE))
    
    # 签名与安全
    developer_signature: Optional[str] = Field(
        default=None,
        description="开发者签名（Base64 编码），由 Tier 1 Market 审核后添加"
    )
    
    # 权限申请
    permissions: List[Permission] = Field(default_factory=list, description="权限申请列表")
    
    # 依赖与运行时
    requirements: List[str] = Field(default_factory=list, description="Python 依赖列表（requirements.txt 格式）")
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig, description="运行时配置")
    
    # License（安装时添加，不包含在 .jsp 包中）
    license_key: Optional[str] = Field(default=None, description="License Key（安装时添加）")
    
    model_config = ConfigDict(
        use_enum_values=True,
        json_schema_extra={
            "example": {
                "id": "com.developer.deep-research",
                "version": "2.1.0",
                "name": "深度研报助手",
                "description": "基于 AI 的深度研究报告生成工具",
                "author": "Developer Name",
                "price": {
                    "amount": 9.99,
                    "currency": "USD",
                    "type": "subscription_monthly"
                },
                "permissions": [
                    {"scope": "internet.access"},
                    {"scope": "file.write"}
                ],
                "requirements": ["pandas>=2.0", "numpy>=1.24.0"]
            }
        }
    )
