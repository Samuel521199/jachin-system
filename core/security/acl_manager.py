"""
ACL Manager - 访问控制列表

v4.0 蜂群：Office vs Home 隔离，按 UserRole 控制技能/资源访问
"""

import logging
from typing import Optional, List, Set
from common.schemas.auth import UserRole, TrustZone

logger = logging.getLogger(__name__)


class ACLManager:
    """
    访问控制管理器 - 基于 TrustZone 和 UserRole 的权限检查
    """

    def __init__(self):
        # 角色 -> 允许的 skill 前缀（如 "com.jachin." 表示所有系统技能）
        self._role_skill_allow: dict = {
            UserRole.OWNER: {"*"},
            UserRole.ADMIN: {"com.jachin.*", "com.user.*"},
            UserRole.MEMBER: {"com.jachin.files", "com.jachin.calendar", "com.jachin.web-surfer"},
            UserRole.GUEST: set(),
        }
        # 跨域访问：home -> office 等
        self._cross_zone_allowed: bool = False

    def can_access_skill(
        self,
        skill_id: str,
        role: UserRole = UserRole.MEMBER,
        trust_zone: TrustZone = TrustZone.HOME,
    ) -> bool:
        """检查角色是否可访问指定技能"""
        allowed = self._role_skill_allow.get(role, set())
        if "*" in allowed:
            return True
        for pattern in allowed:
            if pattern.endswith(".*") and skill_id.startswith(pattern[:-1]):
                return True
            if pattern == skill_id:
                return True
        return False

    def set_role_skills(self, role: UserRole, patterns: Set[str]) -> None:
        """设置角色可访问的技能模式"""
        self._role_skill_allow[role] = patterns
