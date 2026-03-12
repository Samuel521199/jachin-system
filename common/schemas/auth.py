"""
Auth & Trust Schemas - 信任域与用户角色

v4.0 蜂群智能：支持家庭/办公室等多信任域隔离
"""

from enum import Enum
from typing import Optional, List
from pydantic import BaseModel, Field


class TrustZone(str, Enum):
    """信任域"""
    HOME = "home"       # 家庭网络
    OFFICE = "office"   # 办公室网络
    PUBLIC = "public"   # 公共/访客网络（受限）
    GUEST = "guest"     # 访客（受限，兼容）


class UserRole(str, Enum):
    """用户角色"""
    OWNER = "owner"           # 设备所有者，完全控制
    ADMIN = "admin"           # 管理员
    MEMBER = "member"         # 普通成员
    GUEST = "guest"           # 访客，只读/受限


class TrustZoneConfig(BaseModel):
    """信任域配置"""
    zone: TrustZone = Field(description="信任域标识")
    name: str = Field(description="显示名称")
    allowed_roles: List[UserRole] = Field(default_factory=list)
    cross_zone_access: bool = Field(default=False, description="是否允许跨域访问")


class UserContext(BaseModel):
    """用户上下文（请求时携带）"""
    user_id: Optional[str] = None
    role: UserRole = UserRole.MEMBER
    trust_zone: TrustZone = TrustZone.HOME
    device_id: Optional[str] = None
