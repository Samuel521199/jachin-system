"""
License Schema - 技能授权数据模型

Layer 1 License Authority 返回的授权信息
"""

from datetime import datetime
from enum import Enum
from typing import Optional, List

from pydantic import BaseModel, Field, ConfigDict


class LicenseStatus(str, Enum):
    """授权状态"""
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"
    PENDING = "pending"


class LicenseToken(BaseModel):
    """
    技能授权 Token (Site License)
    
    从 L1 sync_licenses 拉取，供 L2 DRM 校验使用
    """
    skill_id: str = Field(description="技能 ID")
    license_key: str = Field(default="", description="License Key")
    status: LicenseStatus = Field(default=LicenseStatus.ACTIVE)
    expires_at: Optional[datetime] = Field(default=None, description="过期时间")
    home_domain_id: Optional[str] = Field(default=None, description="家庭域 ID")
    scope: str = Field(default="site", description="授权范围: site | device | user")

    model_config = ConfigDict(use_enum_values=True)


class SyncLicensesResponse(BaseModel):
    """sync_licenses API 响应"""
    licenses: List[LicenseToken] = Field(default_factory=list)
    synced_at: Optional[datetime] = None
