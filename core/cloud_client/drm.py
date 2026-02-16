"""
DRM Check - 技能分发前 License 校验

在 Layer 2 分发技能给 L3 之前，检查本地 License 列表是否包含该技能的有效授权。
"""

import logging
from typing import Optional, Any

from common.schemas.manifest import PriceType
from core.cloud_client.auth import get_cloud_auth_client

logger = logging.getLogger(__name__)


def check_skill_license(
    skill_id: str,
    manifest: Optional[Any] = None,
    licenses_path: Optional[str] = None,
) -> bool:
    """
    DRM 校验：检查技能是否有有效授权，方可分发给 L3
    
    Args:
        skill_id: 技能 ID
        manifest: 技能 Manifest（含 price 信息），可选
        licenses_path: 自定义 License 缓存路径，可选
    
    Returns:
        True=可分发，False=无授权
    """
    # 1. 免费技能始终通过
    if manifest is not None:
        price = getattr(manifest, "price", None)
        if price is not None and getattr(price, "type", None) == PriceType.FREE:
            return True
        # SkillManifest 无 price 时视为预装/免费
        if not hasattr(manifest, "price"):
            return True

    # 2. 从 CloudAuthClient 获取 License 列表
    client = get_cloud_auth_client()
    if licenses_path:
        client.licenses_path = __import__("pathlib").Path(licenses_path)
    licenses = client.get_licenses()

    # 3. 检查 skill_id 是否在有效 License 中
    for lic in licenses:
        if lic.skill_id == skill_id:
            if lic.status != "active":
                logger.warning(f"License for {skill_id} is not active: {lic.status}")
                return False
            # 可选：检查 expires_at
            if lic.expires_at:
                from datetime import datetime
                if datetime.utcnow() >= lic.expires_at:
                    logger.warning(f"License for {skill_id} has expired")
                    return False
            return True

    # 4. 未找到 License：预装技能（_bundled）默认放行，付费技能拒绝
    if manifest is None or not hasattr(manifest, "price"):
        # 无 manifest 时保守放行（如本地预装）
        return True
    if getattr(getattr(manifest, "price", None), "type", None) == PriceType.FREE:
        return True

    logger.warning(f"DRM: No valid license for paid skill {skill_id}")
    return False


def ensure_licenses_synced() -> bool:
    """
    确保 License 已同步（供启动时调用）
    
    Returns:
        是否成功（含使用本地缓存）
    """
    client = get_cloud_auth_client()
    licenses = client.sync_licenses()
    return len(licenses) >= 0  # 同步成功或使用缓存即视为 OK
