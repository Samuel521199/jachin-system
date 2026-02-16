"""
Unified exceptions - 统一异常定义

业务代码应使用此处定义的异常，便于统一捕获与错误码映射。
"""

from typing import Optional


class JachinError(Exception):
    """Jachin-System 基础异常"""

    def __init__(self, message: str, code: Optional[str] = None):
        self.message = message
        self.code = code or "JACHIN_ERROR"
        super().__init__(self.message)


class AccessDenied(JachinError):
    """信任域访问拒绝 - 技能 zone_restricted 检查失败"""

    def __init__(self, message: str = "访问被拒绝"):
        super().__init__(message, code="ACCESS_DENIED")


class ManifestError(JachinError):
    """Manifest 解析或验证错误"""

    def __init__(self, message: str):
        super().__init__(message, code="MANIFEST_ERROR")


class SkillNotFoundError(JachinError):
    """技能未找到"""

    def __init__(self, skill_id: str):
        super().__init__(f"Skill not found: {skill_id}", code="SKILL_NOT_FOUND")


class PermissionDeniedError(JachinError):
    """权限不足"""

    def __init__(self, message: str = "权限不足"):
        super().__init__(message, code="PERMISSION_DENIED")
