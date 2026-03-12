"""
Trust Zone - 信任域隔离逻辑

v4.0 蜂群：家庭/办公室网络隔离，跨域访问策略
"""

import logging
from dataclasses import dataclass
from typing import Optional, Dict
from common.schemas.auth import TrustZone, TrustZoneConfig, UserRole

logger = logging.getLogger(__name__)


@dataclass
class SecurityContext:
    """
    安全上下文 - 每次 Skill 执行时传入
    供 BaseSkillActor 检查 zone_restricted
    """
    current_zone: str  # TrustZone 值，如 "HOME", "OFFICE", "PUBLIC"
    current_user: Optional[str] = None
    device_id: Optional[str] = None
    user_role: UserRole = UserRole.MEMBER

    def to_dict(self) -> Dict[str, str]:
        """转为字典供 execute(context=...) 传入"""
        return {
            "current_zone": self.current_zone,
            "current_user": self.current_user or "",
            "device_id": self.device_id or "",
        }


class TrustZoneManager:
    """
    信任域管理器 - 定义与校验跨域访问策略
    """

    def __init__(self):
        self._zones: Dict[TrustZone, TrustZoneConfig] = {
            TrustZone.HOME: TrustZoneConfig(
                zone=TrustZone.HOME,
                name="家庭",
                cross_zone_access=False,
            ),
            TrustZone.OFFICE: TrustZoneConfig(
                zone=TrustZone.OFFICE,
                name="办公室",
                cross_zone_access=False,
            ),
            TrustZone.GUEST: TrustZoneConfig(
                zone=TrustZone.GUEST,
                name="访客",
                cross_zone_access=False,
            ),
            TrustZone.PUBLIC: TrustZoneConfig(
                zone=TrustZone.PUBLIC,
                name="公共",
                cross_zone_access=False,
            ),
        }

    def can_cross_zone(
        self,
        from_zone: TrustZone,
        to_zone: TrustZone,
    ) -> bool:
        """检查是否允许从 from_zone 访问 to_zone"""
        if from_zone == to_zone:
            return True
        cfg = self._zones.get(from_zone)
        if cfg and cfg.cross_zone_access:
            return True
        return False

    def get_zone_config(self, zone: TrustZone) -> Optional[TrustZoneConfig]:
        """获取信任域配置"""
        return self._zones.get(zone)
