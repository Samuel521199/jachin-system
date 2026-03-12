"""
Cloud Client - Layer 1 对接

OAuth2 认证、License 同步、技能市场 API、DRM 校验
"""

from core.cloud_client.auth import CloudAuthClient, get_cloud_auth_client
from core.cloud_client.drm import check_skill_license, ensure_licenses_synced

__all__ = [
    "CloudAuthClient",
    "get_cloud_auth_client",
    "check_skill_license",
    "ensure_licenses_synced",
]
